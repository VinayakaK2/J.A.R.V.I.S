"""
main.py
────────
JARVIS v7 FastAPI application entry point.

New in v7:
  • /webhook/whatsapp  — Twilio WhatsApp inbound messages
  • /voice             — Twilio Voice TwiML entry (records caller)
  • /voice/process     — Twilio recordings → Whisper → JARVIS → ElevenLabs → TwiML
  • /webhook/telegram  — Telegram Bot webhook (via integration/telegram.py)
  • /static            — StaticFiles mount exposes workspace for Twilio audio playback
  • app.state          — Shares singleton orchestrator / audio_processor with routers
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import urljoin

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, Form, File, Depends, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import settings
from memory.db import init_db, SessionLocal, User
from orchestrator import OrchestratorAgent
from voice.audio import AudioProcessor
from background_agent import background_agent
from auth import get_password_hash, verify_password, create_access_token, get_current_user
from auth.identity import get_user_info          # Role resolution for webhooks
from memory.session_store import get_or_create_session  # Unified session key
from integration.telegram import telegram_router  # Telegram webhook router

# Configure application-level logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

# Runs at startup and shutdown — initialises DB, shared singletons, and background agent
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise the SQLite database schema
    init_db()
    logger.info("[Startup] Database initialised.")

    # Create singletons and attach to app.state so routers can access them
    app.state.orchestrator    = OrchestratorAgent()
    app.state.audio_processor = AudioProcessor()
    logger.info("[Startup] Orchestrator and AudioProcessor ready.")

    # Launch the background scheduling loop as an async task
    bg_task = asyncio.create_task(background_agent.run())
    logger.info("[Startup] Background agent started.")

    yield  # Application is live and serving requests

    # Graceful shutdown
    background_agent.stop()
    bg_task.cancel()
    logger.info("[Shutdown] Background agent stopped.")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="JARVIS API", version="7.0.0", lifespan=lifespan)

# Expose workspace directory as /static so Twilio can fetch TTS audio files
app.mount(
    "/static",
    StaticFiles(directory=settings.workspace_dir, html=False),
    name="static",
)

# Mount Telegram webhook router
app.include_router(telegram_router)

# CORS — update allow_origins with your production domain before deploying
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiter — max 15 requests per 10 seconds per IP ──────────────────────
request_logs: dict = {}

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Crude in-memory rate limiter to protect against flood attacks."""
    client_ip = request.client.host
    now = time.time()

    if client_ip not in request_logs:
        request_logs[client_ip] = []

    # Keep only entries from the last 10 seconds
    request_logs[client_ip] = [t for t in request_logs[client_ip] if now - t < 10]
    if len(request_logs[client_ip]) > 15:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too Many Requests. Rate limit exceeded."},
        )

    request_logs[client_ip].append(now)
    response = await call_next(request)
    return response


# Convenience accessors for the global singletons stored on app.state
def _orchestrator(request: Request) -> OrchestratorAgent:
    """Return the shared OrchestratorAgent singleton from app.state."""
    return request.app.state.orchestrator

def _audio_processor(request: Request) -> AudioProcessor:
    """Return the shared AudioProcessor singleton from app.state."""
    return request.app.state.audio_processor


# ─── Schemas ──────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    username: str
    password: str

class ChatRequest(BaseModel):
    session_id: str
    message: str
    tone: str = "professional"

class ChatResponse(BaseModel):
    session_id: str
    reply: str

class ScheduleRequest(BaseModel):
    session_id: str
    description: str
    tool: str
    params: dict
    run_at: str   # ISO 8601 datetime string e.g. "2025-04-12T20:00:00"

class HealthResponse(BaseModel):
    status: str
    version: str


# ─── Core Endpoints ────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Liveness probe — returns 200 when the server is running."""
    return HealthResponse(status="ok", version="7.0.0")


@app.post("/register", tags=["Auth"])
async def register(req: AuthRequest):
    """Register a new JARVIS web-UI user (username + password)."""
    with SessionLocal() as db:
        if db.query(User).filter(User.username == req.username).first():
            raise HTTPException(status_code=400, detail="Username already registered")
        user = User(username=req.username, password_hash=get_password_hash(req.password))
        db.add(user)
        db.commit()
    return {"status": "success", "username": req.username}


@app.post("/login", tags=["Auth"])
async def login(req: AuthRequest):
    """Authenticate a web-UI user and return a JWT access token."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == req.username).first()
        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Incorrect username or password")
        access_token = create_access_token(data={"sub": user.username, "id": user.id})
    return {"access_token": access_token, "token_type": "bearer", "username": req.username}


@app.post("/chat", response_model=ChatResponse, tags=["Agent"])
async def chat_endpoint(
    req: ChatRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Main conversational endpoint bound to JWT auth (web UI / API clients)."""
    try:
        # Owner flag is determined by whether the username matches settings.owner_name
        # Web UI users are always treated as "owner" since they passed JWT auth
        scoped_session = f"usr_{current_user.id}_{req.session_id}"
        reply = _orchestrator(request).process_request(
            session_id=scoped_session,
            user_input=req.message,
            tone=req.tone,
            channel="default",
            role="owner",  # JWT-authenticated users have owner-level access
        )
        return ChatResponse(session_id=req.session_id, reply=reply)
    except Exception as e:
        logger.exception("[/chat] Unhandled error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/schedule", tags=["Agent"])
async def schedule_task(
    req: ScheduleRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Directly schedule a tool call scoped to user."""
    from datetime import datetime
    from memory.manager import MemoryAgent

    try:
        run_at = datetime.fromisoformat(req.run_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid run_at format. Use ISO 8601.")

    memory = MemoryAgent()
    scoped_session = f"usr_{current_user.id}_{req.session_id}"
    memory.enqueue_task(
        session_id=scoped_session,
        description=req.description,
        tool=req.tool,
        params=req.params,
        run_at=run_at,
    )
    return {
        "status": "scheduled",
        "tool": req.tool,
        "run_at": run_at.isoformat(),
        "session_id": req.session_id,
    }


@app.post("/voice", tags=["Agent"])
async def voice_endpoint(
    session_id: str = Form(...),
    tone: str = Form("professional"),
    audio_file: UploadFile = File(...),
    request: Request = None,
    current_user: User = Depends(get_current_user),
):
    """REST voice endpoint for direct audio upload (web UI — JWT required)."""
    try:
        audio_bytes  = await audio_file.read()
        text_message = await _audio_processor(request).process_audio_to_text(audio_bytes)

        scoped_session = f"usr_{current_user.id}_{session_id}"
        reply_text   = _orchestrator(request).process_request(
            session_id=scoped_session,
            user_input=text_message,
            tone=tone,
            channel="default",
            role="owner",
        )
        reply_audio  = await _audio_processor(request).process_text_to_audio(reply_text)
        return {
            "session_id":       session_id,
            "transcribed_text": text_message,
            "reply_text":       reply_text,
            "audio_snippet":    reply_audio.hex()[:80] + "...",
        }
    except Exception as e:
        logger.exception("[/voice] Unhandled error")
        raise HTTPException(status_code=500, detail=str(e))


# ─── WhatsApp Webhook (Twilio) ─────────────────────────────────────────────────

@app.post("/webhook/whatsapp", tags=["Channels"])
async def whatsapp_webhook(request: Request):
    """
    Receive an inbound WhatsApp message from Twilio and reply via JARVIS.

    Twilio sends form-encoded data.  Key fields:
      From    — sender's WhatsApp number (e.g. whatsapp:+91XXXXXXXXXX)
      Body    — message text
    """
    form = await request.form()
    sender: str = form.get("From", "")      # e.g. "whatsapp:+91XXXXXXXXXX"
    body: str   = (form.get("Body") or "").strip()

    if not sender or not body:
        logger.warning("[WhatsApp] Missing From or Body in webhook payload.")
        return Response(content="", media_type="text/xml", status_code=200)

    logger.info(f"[WhatsApp] Message from {sender}: '{body}'")

    # Resolve caller identity and role
    clean_phone = sender.replace("whatsapp:", "")
    user_info   = get_user_info(clean_phone)
    role        = user_info["role"]
    logger.info(f"[WhatsApp] Role resolved: {role} for {clean_phone}")

    # Get (or create) a stable cross-channel session for this phone number
    session_id = get_or_create_session(clean_phone, channel="whatsapp")

    # Route to JARVIS
    orchestrator = _orchestrator(request)
    try:
        reply = orchestrator.process_request(
            session_id=session_id,
            user_input=body,
            tone="professional",
            channel="whatsapp",
            role=role,
        )
    except Exception as e:
        logger.exception(f"[WhatsApp] Orchestrator error: {e}")
        reply = "Something went wrong. Please try again shortly."

    if reply is None:
        reply = "✅ Your task has been scheduled and will run in the background."

    # Send reply back via Twilio WhatsApp
    from tools.actions import send_whatsapp
    send_whatsapp(number=clean_phone, message=reply)

    # Twilio expects an empty 200 response when we send the reply via API (not TwiML)
    return Response(content="", media_type="text/xml", status_code=200)


# ─── Voice Webhooks (Twilio) ───────────────────────────────────────────────────

@app.post("/voice/incoming", tags=["Channels"])
async def voice_incoming(request: Request):
    """
    Step 1 of the Twilio voice call flow.

    Twilio hits this URL when a call connects.  We respond with TwiML that
    greets the caller and immediately starts recording their speech.
    The recording is POSTed to /voice/process once the caller stops speaking.
    """
    form = await request.form()
    caller: str = form.get("From", "unknown")
    logger.info(f"[Voice] Incoming call from {caller}")

    # Build the process URL from the configured base URL
    process_url = f"{settings.base_url}/voice/process"

    # TwiML response: greet the caller, then record up to 30 seconds
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Aditi" language="hi-IN">
        Namaste! Main JARVIS hoon. Aap kya jaanna chahte hain?
    </Say>
    <Record
        action="{process_url}"
        method="POST"
        maxLength="30"
        timeout="5"
        transcribe="false"
        playBeep="true"
    />
    <Say>Koi awaaz nahi mili. Phir se try karein.</Say>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


@app.post("/voice/process", tags=["Channels"])
async def voice_process(request: Request):
    """
    Step 2 of the Twilio voice call flow.

    Twilio POSTs the recording metadata here after the caller stops speaking.
    We:
      1. Download + transcribe the audio via Whisper (voice/pipeline.py)
      2. Route the text to JARVIS (channel="voice")
      3. Synthesise the reply with ElevenLabs
      4. Save the MP3 and return TwiML <Play> pointing at the static URL
    """
    from voice.pipeline import (
        transcribe_audio_from_url,
        text_to_speech,
        save_audio_for_twilio,
    )

    form = await request.form()
    caller: str       = form.get("From", "unknown")
    recording_url: str = form.get("RecordingUrl", "")

    logger.info(f"[Voice] Processing recording from {caller}: {recording_url}")

    # Resolve role for the caller
    user_info = get_user_info(caller)
    role      = user_info["role"]
    session_id = get_or_create_session(caller, channel="voice")

    # ── Step 1: Transcribe ────────────────────────────────────────────────────
    if recording_url:
        user_text = await transcribe_audio_from_url(recording_url)
    else:
        user_text = ""

    if not user_text or user_text.startswith("I couldn"):
        # Nothing usable — let Twilio retry / end the call gracefully
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Aditi" language="hi-IN">
        Mujhe aapki awaaz sunai nahi di. Phir se try karein.
    </Say>
    <Redirect method="POST">/voice/incoming</Redirect>
</Response>"""
        return Response(content=twiml, media_type="text/xml")

    logger.info(f"[Voice] Whisper transcription: '{user_text}'")

    # ── Step 2: Route to JARVIS ───────────────────────────────────────────────
    orchestrator = _orchestrator(request)
    try:
        reply_text = orchestrator.process_request(
            session_id=session_id,
            user_input=user_text,
            tone="professional",
            channel="voice",
            role=role,
        )
    except Exception as e:
        logger.exception(f"[Voice] Orchestrator error: {e}")
        reply_text = "Kuch problem ho gayi. Thodi der baad try karein."

    if reply_text is None:
        reply_text = "Aapka task schedule ho gaya hai. Background mein chal raha hai."

    # ── Step 3 & 4: TTS → Save → Respond ─────────────────────────────────────
    audio_bytes = await text_to_speech(reply_text)
    audio_path  = await save_audio_for_twilio(audio_bytes)

    if audio_path:
        # Construct fully-qualified URL for Twilio to fetch
        audio_url = f"{settings.base_url}{audio_path}"
        logger.info(f"[Voice] Playing TTS audio: {audio_url}")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{audio_url}</Play>
    <Record
        action="/voice/process"
        method="POST"
        maxLength="30"
        timeout="5"
        transcribe="false"
        playBeep="true"
    />
    <Say>Koi awaaz nahi mili. Alvida!</Say>
    <Hangup/>
</Response>"""
    else:
        # Fallback: use Twilio Polly TTS if ElevenLabs audio file wasn't saved
        logger.warning("[Voice] No audio file saved — falling back to Polly TTS.")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Aditi" language="hi-IN">{reply_text}</Say>
    <Record
        action="/voice/process"
        method="POST"
        maxLength="30"
        timeout="5"
        transcribe="false"
        playBeep="true"
    />
    <Say>Koi awaaz nahi mili. Alvida!</Say>
    <Hangup/>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


# ─── Observability Endpoints ───────────────────────────────────────────────────

from observability.logger import structured_logger

@app.get("/logs", tags=["Observability"])
async def get_logs(limit: int = 100, current_user: User = Depends(get_current_user)):
    """Returns the recent execution log entries."""
    try:
        logs = structured_logger.read_recent_logs(limit=limit)
        return {"count": len(logs), "logs": logs}
    except Exception as e:
        logger.exception("[/logs] Error reading logs")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/trace/{task_id}", tags=["Observability"])
async def get_trace(task_id: int, current_user: User = Depends(get_current_user)):
    """Returns full chronological execution graph trace for a specific job."""
    try:
        logs = structured_logger.read_recent_logs(limit=500)
        trace_steps = [
            L for L in logs
            if L.get("details", {}).get("step_id") == task_id
            or L.get("details", {}).get("task_id") == task_id
        ]
        return {"task_id": task_id, "total_events": len(trace_steps), "timeline": trace_steps}
    except Exception as e:
        logger.exception("[/trace] Error reading traces")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analysis/{task_id}", tags=["Observability"])
async def get_analysis(task_id: int):
    """Returns failure diagnostics and hierarchical plan delta for a specific job."""
    try:
        logs = structured_logger.read_recent_logs(limit=1000)
        trace_steps = [
            L for L in logs
            if L.get("details", {}).get("step_id") == task_id
            or L.get("details", {}).get("task_id") == task_id
        ]
        failures     = [L for L in trace_steps if L.get("event") == "STEP_FAILED"]
        observations = [L for L in trace_steps if L.get("event") == "OBSERVATION"]
        return {
            "task_id":               task_id,
            "v6_failure_diagnostics": failures[-1].get("details", {}).get("diagnostic", {}) if failures else None,
            "visual_discrepancy":     observations[-1].get("details", {}).get("vision", {}) if observations else None,
            "timeline":               trace_steps,
        }
    except Exception as e:
        logger.exception("[/analysis] Error reading tracing metrics")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Sessions Observability (new in v7) ───────────────────────────────────────

@app.get("/sessions", tags=["Observability"])
async def list_sessions(current_user: User = Depends(get_current_user)):
    """Returns all active cross-channel sessions (in-memory snapshot)."""
    from memory.session_store import list_active_sessions
    return {"sessions": list_active_sessions()}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
