# 🤖 JARVIS — How to Start All Servers

This guide explains how to start every component of the JARVIS multi-agent AI assistant system from scratch.

---

## 📋 Prerequisites

Before starting, ensure you have the following installed:

| Requirement   | Min Version | Check Command          |
|---------------|-------------|------------------------|
| Python        | 3.10+       | `python --version`     |
| pip           | latest      | `pip --version`        |

---

## ⚙️ First-Time Setup (Do This Once)

### 1. Install Python Dependencies

Navigate to the backend folder and install all required packages:

```powershell
cd h:\Jarvis\jarvis
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example env file and fill in your API keys:

```powershell
# On Windows PowerShell
Copy-Item .env.example .env
```

Then open `h:\Jarvis\jarvis\.env` and set:

```env
OPENAI_API_KEY=sk-your-real-openai-key-here
ELEVENLABS_API_KEY=your-elevenlabs-api-key-here
DATABASE_URL=sqlite:///./jarvis.db
LOG_LEVEL=INFO
WORKSPACE_DIR=./workspace
```

> ⚠️ **Never commit your `.env` file to version control.** It contains secret API keys.

---

## 🚀 Starting the Servers

You need **two terminal windows** running simultaneously.

---

### Terminal 1 — Backend API Server (FastAPI)

```powershell
cd h:\Jarvis\jarvis
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

| Detail     | Value                         |
|------------|-------------------------------|
| URL        | http://localhost:8000         |
| API Docs   | http://localhost:8000/docs    |
| ReDoc      | http://localhost:8000/redoc   |
| Hot Reload | ✅ Yes (auto-restarts on save) |

**Expected output when running correctly:**
```
INFO:     Will watch for changes in these directories: ['H:\Jarvis\jarvis']
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [XXXX] using StatReload
INFO:     Started server process [XXXX]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

---

### Terminal 2 — Frontend UI Server (Static HTML)

```powershell
cd h:\Jarvis\frontend
python -m http.server 3000
```

| Detail | Value                     |
|--------|---------------------------|
| URL    | http://localhost:3000     |
| Files  | index.html, style.css, app.js |

**Expected output when running correctly:**
```
Serving HTTP on :: port 3000 (http://[::]:3000/) ...
```

Open your browser and go to: **http://localhost:3000**

---

## 🔁 Quick-Start Script (One Command)

You can save this as `start.ps1` in `h:\Jarvis\` and run it to launch both servers at once:

```powershell
# start.ps1 — Launches both JARVIS servers in separate windows

# Start the FastAPI backend in a new PowerShell window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd h:\Jarvis\jarvis; python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

# Start the frontend static server in a new PowerShell window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd h:\Jarvis\frontend; python -m http.server 3000"

Write-Host "✅ JARVIS servers starting..."
Write-Host "   Backend  → http://localhost:8000"
Write-Host "   Frontend → http://localhost:3000"
Write-Host "   API Docs → http://localhost:8000/docs"
```

Run it with:

```powershell
cd h:\Jarvis
.\start.ps1
```

---

## 🛑 Stopping the Servers

In each terminal window, press:

```
Ctrl + C
```

---

## 🌐 Available Endpoints

| Endpoint        | Method | Description                              |
|-----------------|--------|------------------------------------------|
| `/chat`         | POST   | Send a text message to JARVIS            |
| `/voice`        | POST   | Send an audio file, receive a voice reply|
| `/docs`         | GET    | Interactive Swagger API documentation    |
| `/redoc`        | GET    | ReDoc API documentation                  |

### Example `/chat` Request

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "abc123", "message": "Hello JARVIS", "tone": "professional"}'
```

---

## ❗ Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError` | Dependencies not installed | Run `pip install -r requirements.txt` |
| Port 8000 already in use | Another process is running | Run `netstat -ano \| findstr :8000` then kill the PID |
| Port 3000 already in use | Another process is running | Run `netstat -ano \| findstr :3000` then kill the PID |
| `OPENAI_API_KEY not set` | Missing env var | Edit `h:\Jarvis\jarvis\.env` and add your key |
| Frontend shows blank page | Backend not running | Start Terminal 1 first, then refresh the browser |
| `Application startup complete` missing | Import error in code | Check the backend terminal for Python tracebacks |

---

## 📁 Project Structure Reference

```
h:\Jarvis\
├── HOW_TO_START_SERVERS.md   ← You are here
├── JARVIS_SYSTEM_GUIDE.md    ← Full system architecture guide
├── frontend/
│   ├── index.html            ← Main UI (served on port 3000)
│   ├── style.css             ← Styles
│   └── app.js                ← Frontend logic
└── jarvis/
    ├── main.py               ← FastAPI app entry point (port 8000)
    ├── requirements.txt      ← Python dependencies
    ├── .env                  ← Your secret API keys (never commit!)
    ├── .env.example          ← Template for env vars
    ├── orchestrator.py       ← Orchestrator agent
    ├── planner.py            ← Planner agent
    ├── executor.py           ← Executor agent
    ├── communication/        ← Communication agent
    ├── memory/               ← Memory & database layer
    ├── safety/               ← Safety guardrails
    ├── tools/                ← Tool integrations
    ├── voice/                ← Whisper / ElevenLabs audio
    └── config/               ← App settings
```
