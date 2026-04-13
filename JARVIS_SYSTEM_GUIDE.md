# JARVIS — Multi-Agent AI Assistant System
### Production-Ready Implementation Guide using n8n

---

## 1. SYSTEM ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        INPUT CHANNELS                                   │
│   [WhatsApp/Twilio]  [Telegram Bot]  [Phone Call/Twilio Voice]          │
│         │                  │                    │                        │
│    Text/Media          Text/Voice          Voice Call                   │
└──────────────┬──────────────┬───────────────────┬──────────────────────┘
               │              │                   │
               └──────────────┴───────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  INPUT NORMALIZER  │   ← Code Node (n8n)
                    │  STT if voice     │   ← Whisper API
                    │  Format to JSON   │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │   MEMORY AGENT    │   ← Fetch user context
                    │  Load context     │   ← PostgreSQL / Supabase
                    │  User prefs, tone │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │ ORCHESTRATOR AGENT │  ← Central Brain (GPT-4o/Claude)
                    │  Route request    │
                    │  Decide agents    │
                    └────────┬──────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
 ┌────────▼───────┐ ┌────────▼───────┐ ┌───────▼────────┐
 │  TASK PLANNER  │ │ TOOL EXECUTOR  │ │  SAFETY AGENT  │
 │  Break intent  │ │  Call APIs     │ │  Validate resp │
 │  into steps    │ │  Run tools     │ │  Check tone    │
 └────────┬───────┘ └────────┬───────┘ └───────┬────────┘
          │                  │                  │
          └──────────────────┴──────────────────┘
                             │
                    ┌────────▼────────┐
                    │ COMMUNICATION   │  ← Format response
                    │    AGENT        │  ← Adapt tone/style
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  MEMORY UPDATE  │  ← Save to DB
                    └────────┬────────┘
                             │
               ┌─────────────┼─────────────┐
               │             │             │
      ┌────────▼───┐  ┌──────▼─────┐  ┌───▼──────────┐
      │  Text Resp │  │ TTS (Voice)│  │  File/Media  │
      │  Telegram/ │  │  ElevenLabs│  │  Response    │
      │  WhatsApp  │  │  Response  │  │              │
      └────────────┘  └────────────┘  └──────────────┘
```

---

## 2. AGENT DEFINITIONS & SYSTEM PROMPTS

### AGENT 1: Orchestrator Agent (Central Brain)

**Role**: Receives normalized input + user context, decides what to do next.

**System Prompt**:
```
You are JARVIS, an intelligent AI orchestrator. Your job is to analyze incoming user requests and output a structured routing decision.

Given:
- user_message: The user's request (text)
- user_context: Their preferences, history, tone setting
- channel: whatsapp | telegram | voice

You MUST respond with ONLY this JSON structure:
{
  "intent": "task_execution | information_query | conversation | creative | emergency",
  "requires_planning": true | false,
  "tools_needed": ["web_search", "code_execution", "messaging", "email", "calendar", "file_creation", "weather"],
  "tone": "professional | friendly | playful",
  "language": "en | hi | hinglish",
  "complexity": "simple | medium | complex",
  "routing": {
    "planner": true | false,
    "executor": true | false,
    "memory_update": true | false
  },
  "safety_flag": false,
  "orchestrator_note": "Brief internal note on why you routed this way"
}

Rules:
- requires_planning = true if the task has 2+ steps
- tools_needed must only include tools that are actually needed
- tone must match user_context.preferred_tone unless user clearly shifts it
- safety_flag = true if request seems harmful, dangerous, or inappropriate
- Always respect the user's language preference
```

---

### AGENT 2: Task Planner Agent

**Role**: Takes complex requests and breaks them into an ordered execution plan.

**System Prompt**:
```
You are a task planning agent. You receive a user's goal and must output a precise, ordered execution plan.

Input format:
{
  "goal": "User's high-level intent",
  "tools_available": ["list of tools"],
  "context": "User's history/preferences"
}

Output ONLY this JSON:
{
  "plan_id": "uuid",
  "goal_summary": "One sentence summary",
  "steps": [
    {
      "step_id": 1,
      "action": "tool_name OR agent_name",
      "description": "What to do",
      "input_params": {},
      "depends_on": [],
      "expected_output": "What this step produces"
    }
  ],
  "estimated_steps": 3,
  "can_parallelize": false
}

Be minimal. Don't create unnecessary steps. If the goal can be done in 1 step, return 1 step.
For "build a portfolio website": plan HTML generation → save file → provide download link.
For "message Rahul": resolve contact → send message.
```

---

### AGENT 3: Tool Executor Agent

**Role**: Executes individual tool calls from the plan.

**System Prompt**:
```
You are a tool execution agent. You receive a step from a plan and execute it by calling the appropriate tool.

When you receive a step, format your tool call request as:
{
  "tool": "tool_name",
  "action": "specific_action",
  "params": {},
  "fallback": "what to do if tool fails"
}

Available tools and their schemas:
- web_search: { query: string }
- code_execute: { language: "python"|"javascript", code: string }
- file_create: { filename: string, content: string, type: "html"|"css"|"js"|"py" }
- send_whatsapp: { to: string, message: string }
- send_telegram: { chat_id: string, message: string }
- send_email: { to: string, subject: string, body: string }
- calendar_create: { title: string, datetime: string, duration_mins: number }
- weather_get: { location: string }
- db_query: { query: string, params: [] }

Always validate params before executing. If a required param is missing, return:
{ "status": "need_input", "missing_params": ["param_name"], "ask_user": "message to ask user" }
```

---

### AGENT 4: Memory Agent

**Role**: Retrieves and stores user context, preferences, conversation history.

**DB Schema (PostgreSQL/Supabase)**:
```sql
-- Users table
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  phone VARCHAR(20) UNIQUE,
  telegram_id VARCHAR(50) UNIQUE,
  name VARCHAR(100),
  preferred_tone VARCHAR(20) DEFAULT 'professional',
  preferred_language VARCHAR(10) DEFAULT 'en',
  created_at TIMESTAMP DEFAULT NOW()
);

-- Conversations table
CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  channel VARCHAR(20),
  message TEXT,
  response TEXT,
  intent VARCHAR(50),
  created_at TIMESTAMP DEFAULT NOW()
);

-- User preferences table
CREATE TABLE user_preferences (
  user_id UUID REFERENCES users(id) PRIMARY KEY,
  contacts JSONB DEFAULT '{}',
  frequent_tasks JSONB DEFAULT '[]',
  timezone VARCHAR(50) DEFAULT 'Asia/Kolkata',
  voice_enabled BOOLEAN DEFAULT true,
  updated_at TIMESTAMP DEFAULT NOW()
);

-- Context window (last 10 messages for active session)
CREATE TABLE active_sessions (
  user_id UUID REFERENCES users(id) PRIMARY KEY,
  context JSONB DEFAULT '[]',
  last_active TIMESTAMP DEFAULT NOW()
);
```

**Memory Retrieval Code (n8n Code Node)**:
```javascript
// Fetch user memory before sending to orchestrator
const userId = $input.first().json.user_id;

const query = `
  SELECT 
    u.name, u.preferred_tone, u.preferred_language,
    up.contacts, up.frequent_tasks, up.timezone,
    s.context as recent_messages
  FROM users u
  LEFT JOIN user_preferences up ON u.id = up.user_id
  LEFT JOIN active_sessions s ON u.id = s.user_id
  WHERE u.id = $1
`;

// This runs via n8n's Postgres node
return [{ json: { query, params: [userId] } }];
```

---

### AGENT 5: Communication Agent

**Role**: Takes raw tool output or information and crafts a natural, context-appropriate response.

**System Prompt**:
```
You are the voice and personality of JARVIS. You receive raw data/results and must craft a response that sounds natural and human.

Input:
{
  "raw_result": "Data from tool execution or AI response",
  "tone": "professional | friendly | playful",
  "language": "en | hi | hinglish",
  "channel": "whatsapp | telegram | voice",
  "user_name": "Name of user"
}

Rules:
- For voice channel: Write in flowing, spoken language. No markdown. No bullet points. Speak like a smart human assistant. Use natural pauses with commas.
- For text channel: Can use light formatting. WhatsApp supports *bold* and _italic_. Keep it concise.
- Tone=professional: Crisp, efficient, no small talk.
- Tone=friendly: Warm, add a touch of humor when appropriate.
- Tone=playful: Light flirty energy, witty, but ALWAYS respectful. Only when user has clearly initiated this tone.
- In Hinglish: Mix Hindi and English naturally. Don't force-translate everything.
- NEVER start a response with "I" as the first word.
- NEVER be robotic. NEVER say "I am an AI language model."
- If task completed successfully, confirm it in a natural way.
- If something failed, be honest but reassuring.

Output ONLY the final message string. Nothing else.
```

---

### AGENT 6: Safety Agent

**Role**: Final check before any response goes out.

**System Prompt**:
```
You are a safety validation agent. Review the outgoing response for issues.

Check for:
1. Inappropriate content (sexual, violent, harmful)
2. Private information leak (passwords, keys, personal data)
3. Incorrect/dangerous information (medical, legal advice stated as fact)
4. Tone mismatch (too flirty when not appropriate)
5. Hallucinated facts presented as real

Input: { "response": "...", "original_request": "..." }

Output:
{
  "approved": true | false,
  "issues": [],
  "corrected_response": "Only present if approved=false",
  "severity": "low | medium | high"
}

If approved=false with severity=high, replace with: "Sorry, I can't help with that one."
```

---

## 3. n8n WORKFLOW BREAKDOWN

### WORKFLOW 1: Telegram Bot Handler

**Nodes in order**:

```
1. Telegram Trigger
   - Listen for messages (text + voice/audio)
   - Extract: chat_id, user_id, message_type, content

2. Code Node: "Normalize Input"
   - Detect if message_type = voice
   - If voice: extract file_id
   - Output: { source: "telegram", user_id, raw_content, input_type }

3. HTTP Request: "Download Voice File" (conditional)
   - Condition: input_type === "voice"
   - GET https://api.telegram.org/file/bot{TOKEN}/{file_path}
   - Save to buffer

4. HTTP Request: "Whisper STT" (conditional)
   - POST https://api.openai.com/v1/audio/transcriptions
   - multipart/form-data: file + model=whisper-1
   - Output: transcribed text

5. Code Node: "Merge Text"
   - If was voice: use transcribed text
   - If was text: use original message
   - Output: { unified_text, user_id, chat_id, channel: "telegram" }

6. Postgres Node: "Get or Create User"
   - SELECT/INSERT user record
   - Return user_id, name, tone, language prefs

7. Postgres Node: "Load Session Context"
   - SELECT last 10 messages for user
   - Return context array

8. OpenAI Node: "Orchestrator Agent"
   - Model: gpt-4o (or claude-3-5-sonnet)
   - System: [Orchestrator system prompt from above]
   - User: JSON.stringify({ message, context, channel, user_prefs })
   - Output: routing JSON

9. Code Node: "Parse Orchestrator Output"
   - JSON.parse the response
   - Check safety_flag
   - Determine which path to take

10. IF Node: "Safety Check"
    - True: safety_flag === true → go to Safety Response
    - False: continue to routing

11. Switch Node: "Route by Complexity"
    - Case "simple": skip planner → go to Tool Executor
    - Case "medium": go to Task Planner
    - Case "complex": go to Task Planner with full context

12. OpenAI Node: "Task Planner" (if needed)
    - System: [Task Planner system prompt]
    - Output: execution plan JSON

13. Code Node: "Execute Plan Steps"
    - Loop through plan steps
    - Call appropriate tool nodes
    - Collect results

14. [Tool Nodes - see Tool Execution Workflow below]

15. OpenAI Node: "Communication Agent"
    - System: [Communication Agent system prompt]
    - Input: { raw_result, tone, language, channel, user_name }
    - Output: final response string

16. OpenAI Node: "Safety Agent"
    - System: [Safety Agent system prompt]
    - Input: { response, original_request }
    - Output: approved/rejected

17. Code Node: "Final Response Prep"
    - If voice requested: flag for TTS
    - If text: clean formatting for channel

18. HTTP Request: "ElevenLabs TTS" (if voice output needed)
    - POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
    - Body: { text, model_id: "eleven_turbo_v2", voice_settings: { stability: 0.5, similarity_boost: 0.8 } }
    - Save audio buffer

19. Telegram Node: "Send Response"
    - If voice: sendAudio
    - If text: sendMessage
    - Use chat_id from step 1

20. Postgres Node: "Update Memory"
    - INSERT conversation record
    - UPDATE active_sessions context (keep last 10)
```

---

### WORKFLOW 2: WhatsApp (Twilio) Handler

```
1. Webhook Node: "Twilio WhatsApp Webhook"
   - POST /webhook/whatsapp
   - Parse: From, Body, MediaUrl (if media)

2. Code Node: "Twilio Auth Verify"
   - Verify X-Twilio-Signature header
   - Reject if invalid

3. Code Node: "Extract WhatsApp Data"
   - Parse Twilio form body
   - Extract phone number, message, media URL

4. HTTP Request: "Download Media" (if voice message)
   - Download from MediaUrl with Twilio auth

5. [Continue same as Telegram from step 4 onwards]
   - Same Whisper STT if voice
   - Same orchestrator routing
   - Same agents

6. HTTP Request: "Twilio Send Response"
   - POST https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json
   - Body: { From: whatsapp:+14155238886, To: user_number, Body: response }
   - Or MediaUrl for audio response
```

---

### WORKFLOW 3: Voice Call Handler (Twilio Voice)

```
1. Webhook Node: "Twilio Call Webhook"
   - Twilio calls this when call is answered
   - Return TwiML to record caller's speech

2. Code Node: "TwiML Generator"
   - Return: <Response><Record action="/voice-process" timeout="5" /></Response>

3. Webhook Node: "Process Recording"
   - Receives RecordingUrl from Twilio
   - Download .mp3 recording

4. Whisper STT → Orchestrator → Agents (same flow)

5. ElevenLabs TTS: Generate voice response audio

6. HTTP Request: "Twilio Play Audio"
   - Upload audio to accessible URL
   - Return TwiML: <Response><Play>{audio_url}</Play></Response>
```

---

## 4. TOOL EXECUTION NODES (n8n sub-workflow)

```javascript
// Code Node: Tool Router
const step = $input.first().json;
const toolName = step.action;

const toolRoutes = {
  web_search: 'HTTP_SERP_API',
  code_execute: 'HTTP_CODE_SANDBOX',
  file_create: 'CODE_FILE_GEN',
  send_whatsapp: 'TWILIO_WHATSAPP',
  send_telegram: 'TELEGRAM_SEND',
  send_email: 'SMTP_SEND',
  calendar_create: 'GOOGLE_CALENDAR',
  weather_get: 'HTTP_WEATHER_API',
  db_query: 'POSTGRES_NODE'
};

return [{ json: { route: toolRoutes[toolName], params: step.input_params } }];
```

### Tool: Web Search
```
HTTP Request Node
- Method: GET
- URL: https://serpapi.com/search.json
- Params: { q: "{{query}}", api_key: "{{SERP_API_KEY}}", engine: "google" }
- Auth: Query Param
```

### Tool: Code Execution (via Judge0 API)
```
HTTP Request Node
- Method: POST
- URL: https://judge0-ce.p.rapidapi.com/submissions
- Body: {
    source_code: "{{code}}",
    language_id: 63,  // 63=JavaScript, 71=Python
    stdin: ""
  }
- Headers: X-RapidAPI-Key, Content-Type: application/json
```

### Tool: File Creation
```javascript
// Code Node
const { filename, content, type } = $input.first().json;

// Generate file content
const fileContent = Buffer.from(content).toString('base64');

// Save to temp storage or return as binary
return [{
  json: { filename, type, size: content.length },
  binary: {
    data: {
      data: fileContent,
      mimeType: type === 'html' ? 'text/html' : 'text/plain',
      fileName: filename
    }
  }
}];
```

### Tool: Weather
```
HTTP Request Node
- URL: https://api.openweathermap.org/data/2.5/weather
- Params: { q: "{{location}}", appid: "{{OWM_KEY}}", units: "metric" }
```

### Tool: Google Calendar
```
Google Calendar Node (native n8n)
- Operation: Create Event
- Calendar ID: primary
- Title: {{title}}
- Start: {{datetime}}
- Duration: {{duration_mins}} minutes
```

---

## 5. REQUIRED APIS & SETUP

### APIs You Need:

| Service | Purpose | Free Tier | Cost |
|---------|---------|-----------|------|
| OpenAI API | GPT-4o for agents | $5 credit | ~$0.01/1K tokens |
| ElevenLabs | Natural TTS voice | 10K chars/month | $5/mo for more |
| Twilio | WhatsApp + Voice calls | $15 trial | Pay-per-use |
| Telegram Bot API | Telegram integration | Free | Free |
| SerpAPI | Web search | 100/month | $50/mo |
| OpenWeatherMap | Weather data | 1K calls/day | Free |
| Judge0 | Code execution | 50/day | Free |
| Supabase/PostgreSQL | Memory + context | 500MB free | Free |
| Whisper (via OpenAI) | STT | Included in OpenAI | $0.006/min |

### ElevenLabs Voice Setup:
```
Recommended Voice IDs:
- "Rachel" (21m00Tcm4TlvDq8ikWAM) - Professional, clear
- "Bella" (EXAVITQu4vr4xnSDxMaL) - Warm, friendly  
- "Elli" (MF3mGyEYCl7XYWbV9V6O) - Young, playful

Voice Settings for Natural Sound:
{
  "stability": 0.45,        // Slight variation = more natural
  "similarity_boost": 0.80, // Stay close to voice profile
  "style": 0.20,            // Slight expressiveness
  "use_speaker_boost": true
}
```

### n8n Setup:
```bash
# Self-hosted (recommended for production)
docker run -it --rm \
  --name n8n \
  -p 5678:5678 \
  -v ~/.n8n:/home/node/.n8n \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=admin \
  -e N8N_BASIC_AUTH_PASSWORD=your_password \
  n8nio/n8n

# OR use n8n Cloud (n8n.io) - easier setup
```

### Environment Variables (n8n Credentials):
```
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
TELEGRAM_BOT_TOKEN=...
SERP_API_KEY=...
OPENWEATHER_API_KEY=...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
JUDGE0_API_KEY=...
GOOGLE_CALENDAR_CREDENTIALS={...service account JSON...}
```

---

## 6. EXAMPLE EXECUTION FLOWS

### Flow 1: "Build me a portfolio website"

```
User (Telegram): "Build me a portfolio website"

1. Input Normalizer → { text: "Build me a portfolio website", channel: "telegram" }

2. Memory Agent → { name: "Vinayaka", tone: "friendly", lang: "en" }

3. Orchestrator Output:
{
  "intent": "creative",
  "requires_planning": true,
  "tools_needed": ["code_execution", "file_create"],
  "tone": "friendly",
  "complexity": "complex",
  "routing": { "planner": true, "executor": true }
}

4. Task Planner Output:
{
  "goal_summary": "Generate and deliver a complete portfolio website",
  "steps": [
    { "step_id": 1, "action": "gpt4_generate", "description": "Generate HTML/CSS/JS portfolio code", "input_params": { "style": "modern", "sections": ["about", "skills", "projects", "contact"] } },
    { "step_id": 2, "action": "file_create", "description": "Save as index.html", "depends_on": [1] },
    { "step_id": 3, "action": "send_telegram", "description": "Send download link", "depends_on": [2] }
  ]
}

5. Tool Executor runs GPT-4o with:
System: "You are a web developer. Generate a complete, single-file portfolio website with modern design. Return ONLY the HTML code."
User: "Name: Vinayaka K, Role: AI Engineer & Data Scientist, Skills: Python, React, n8n, AI Tools"

6. File Creation Node → saves portfolio.html

7. Communication Agent → "Done! Here's your portfolio 🎉 I kept it clean and modern. Let me know if you want any changes!"

8. Telegram Node → sends file + message
```

---

### Flow 2: "Message Rahul that I'll be late"

```
User (WhatsApp voice): [voice message saying "Message Rahul that I'll be late"]

1. Whisper STT → "Message Rahul that I'll be late"

2. Memory Agent → loads contacts: { "Rahul": "+919876543210" }

3. Orchestrator Output:
{
  "intent": "task_execution",
  "requires_planning": false,
  "tools_needed": ["send_whatsapp"],
  "complexity": "simple"
}

4. Tool Executor:
{
  "tool": "send_whatsapp",
  "params": {
    "to": "whatsapp:+919876543210",
    "message": "Hey Rahul! Vinayaka will be a bit late. Sorry for the inconvenience!"
  }
}

5. WhatsApp API → message sent to Rahul ✓

6. Communication Agent → "Done! Rahul's been notified."

7. ElevenLabs TTS → voice response sent back to user
```

---

### Flow 3: "Kal ka weather bata" (Voice call)

```
User (Phone Call): "Kal ka weather bata"

1. Twilio records voice → audio file

2. Whisper STT → "Kal ka weather bata"

3. Memory Agent → { language: "hinglish", location: "Bengaluru", timezone: "Asia/Kolkata" }

4. Orchestrator Output:
{
  "intent": "information_query",
  "requires_planning": false,
  "tools_needed": ["weather_get"],
  "language": "hinglish",
  "complexity": "simple"
}

5. Tool Executor:
GET openweathermap.org/forecast?q=Bengaluru&cnt=2
Response: { tomorrow: { temp: 28, condition: "Partly cloudy", humidity: 72 } }

6. Communication Agent (Hinglish, voice mode):
"Kal Bengaluru mein thoda cloudy rehega, temperature around 28 degrees. Humidity thodi zyada hai toh comfortable clothes pehen lena."

7. ElevenLabs TTS → Natural voice audio

8. Twilio Call → plays audio response on call
```

---

## 7. PERSONALITY IMPLEMENTATION

```javascript
// Code Node: Tone Selector
function selectTone(userMessage, sessionHistory, userPrefs) {
  const msg = userMessage.toLowerCase();
  
  // Explicit playful triggers (user must clearly initiate)
  const playfulTriggers = ['flirt', 'wink', '😉', '😏', 'fun mode', 'chill ho jao'];
  const formalTriggers = ['formal', 'professional', 'work', 'meeting', 'report'];
  const hinglishMarkers = ['yaar', 'bhai', 'kya', 'hai', 'bata', 'kar', 'mujhe'];
  
  if (playfulTriggers.some(t => msg.includes(t))) return 'playful';
  if (formalTriggers.some(t => msg.includes(t))) return 'professional';
  if (hinglishMarkers.some(t => msg.includes(t))) return 'friendly'; // + hinglish
  
  return userPrefs.preferred_tone || 'professional';
}

// Language detector
function detectLanguage(text) {
  const hindiDevanagari = /[\u0900-\u097F]/;
  const hindiRoman = /(kya|hai|bata|mujhe|yaar|kal|aaj|karo|dena)/i;
  
  if (hindiDevanagari.test(text)) return 'hi';
  if (hindiRoman.test(text)) return 'hinglish';
  return 'en';
}
```

---

## 8. MEMORY UPDATE LOGIC

```javascript
// Code Node: Update Session Context
const { userId, userMessage, assistantResponse, intent } = $input.first().json;

// Build new context entry
const newEntry = {
  timestamp: new Date().toISOString(),
  user: userMessage,
  assistant: assistantResponse,
  intent: intent
};

// Fetch existing context
const existingContext = $node["Load Session Context"].json.context || [];

// Keep last 10 exchanges (rolling window)
const updatedContext = [...existingContext, newEntry].slice(-10);

return [{
  json: {
    userId,
    context: JSON.stringify(updatedContext),
    lastActive: new Date().toISOString()
  }
}];
```

---

## 9. ERROR HANDLING

```javascript
// Global Error Handler (Error Trigger Node in n8n)
const error = $input.first().json;

const userFriendlyMessages = {
  'OPENAI_RATE_LIMIT': "Just give me a sec, I'm a bit busy right now.",
  'TOOL_EXECUTION_FAILED': "Hmm, couldn't complete that task. Can you try again?",
  'STT_FAILED': "Didn't catch that clearly. Can you repeat or type it?",
  'CONTACT_NOT_FOUND': "I don't have this contact saved. What's the number?",
  'DEFAULT': "Something went sideways. Give me a moment and try again."
};

const msg = userFriendlyMessages[error.code] || userFriendlyMessages['DEFAULT'];

// Log to Supabase for debugging
// Send friendly message back to user channel
return [{ json: { error_message: msg, original_error: error } }];
```

---

## 10. SCALABILITY IMPROVEMENTS (Future)

### Phase 2 Upgrades:
1. **Vector Memory** - Add Pinecone/Qdrant for semantic memory search
   - "Remember when I asked about X last month" will work
2. **Multi-user Isolation** - Each user gets their own agent context
3. **Parallel Tool Execution** - Run independent plan steps simultaneously
4. **Streaming Responses** - For long tasks, send progress updates
5. **Agent Learning** - Track which tasks fail and retrain prompts
6. **WhatsApp Business API** - Upgrade from Twilio to direct Meta API
7. **Voice Emotion Detection** - Analyze voice tone to adapt personality
8. **Plugin System** - Allow adding new tools via n8n sub-workflows
9. **Rate Limiting** - Per-user request throttling
10. **Audit Logging** - Full trace of every agent decision

### n8n Optimization:
- Use n8n's built-in AI Agent node for simpler flows
- Sub-workflows for reusable components
- Error workflow triggers for robust fallback
- Use n8n Queue mode for high traffic

---

## 11. QUICK START CHECKLIST

```
□ n8n instance running (docker or cloud)
□ Telegram bot created via @BotFather → get BOT_TOKEN
□ Twilio account → WhatsApp sandbox activated
□ OpenAI API key with GPT-4o access
□ ElevenLabs account → get Voice ID
□ Supabase project → run DB schema migrations
□ SerpAPI key for web search
□ OpenWeatherMap key
□ All credentials added to n8n
□ Telegram Trigger node connected
□ WhatsApp Webhook URL set in Twilio console
□ Test each agent node individually first
□ Test full flow end-to-end with simple message
□ Enable n8n error workflow
□ Set up daily DB backup
```

---

*Built for: Vinayaka K — JARVIS Multi-Agent System v1.0*
*Stack: n8n + GPT-4o + ElevenLabs + Twilio + Supabase*
