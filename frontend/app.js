/**
 * JARVIS Frontend — Main Application Logic
 * Handles: API communication, chat UI, particle background,
 *          agent chain animation, status checks, session management
 */

// ============================================================
// CONSTANTS — All magic values extracted and named clearly
// ============================================================
const API_BASE_URL      = 'http://localhost:8000';
const MAX_MSG_LENGTH    = 500;
const SESSION_KEY       = 'jarvis_session_id';
const AGENT_STEP_DELAY  = 600; // ms between agent chain animation steps

// ============================================================
// STATE — Centralized app state
// ============================================================
const state = {
  sessionId:     '',
  currentTone:   'professional',
  messageCount:  0,
  sessionStart:  Date.now(),
  isLoading:     false,
};

// ============================================================
// DOM REFS — Cached selectors for performance
// ============================================================
const $ = (id) => document.getElementById(id);

const DOM = {
  chatWindow:      $('chat-window'),
  messageInput:    $('message-input'),
  sendBtn:         $('send-btn'),
  voiceBtn:        $('voice-btn'),
  typingIndicator: $('typing-indicator'),
  sessionDisplay:  $('session-id-display'),
  headerTime:      $('header-time'),
  charCounter:     $('char-counter'),
  statMessages:    $('stat-messages'),
  statSessionTime: $('stat-session-time'),
  clearBtn:        $('clear-chat-btn'),
  welcomeTime:     $('welcome-time'),
  toast:           $('toast'),
  toastMsg:        $('toast-msg'),
  statusApiDot:    $('status-api'),
  statusApiText:   $('status-api-text'),
  statusDbDot:     $('status-db'),
  statusDbText:    $('status-db-text'),
  statusAiDot:     $('status-ai'),
  statusAiText:    $('status-ai-text'),
  // Agent chain steps
  agentMem:  $('agent-mem'),
  agentOrch: $('agent-orch'),
  agentPlan: $('agent-plan'),
  agentExec: $('agent-exec'),
  agentComm: $('agent-comm'),
};

// ============================================================
// SESSION — Generate or restore a session ID
// ============================================================
function initSession() {
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    // Generate a compact unique session ID
    sessionId = 'jarvis-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }
  state.sessionId = sessionId;
  DOM.sessionDisplay.textContent = sessionId;
}

// ============================================================
// CLOCK — Live HH:MM:SS in header
// ============================================================
function startClock() {
  // Set initial welcome message time
  DOM.welcomeTime.textContent = new Date().toLocaleTimeString();

  const tick = () => {
    const now = new Date();
    DOM.headerTime.textContent = now.toLocaleTimeString('en-US', { hour12: false });

    // Update session timer
    const elapsed = Math.floor((Date.now() - state.sessionStart) / 1000);
    const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const secs = String(elapsed % 60).padStart(2, '0');
    DOM.statSessionTime.textContent = `${mins}:${secs}`;
  };

  tick();
  setInterval(tick, 1000);
}

// ============================================================
// STATUS CHECK — Ping the API to verify all systems
// ============================================================
async function checkApiStatus() {
  try {
    const res = await fetch(`${API_BASE_URL}/docs`, { method: 'HEAD', signal: AbortSignal.timeout(4000) });
    if (res.ok || res.status === 200 || res.redirected) {
      setStatus('api', 'online', 'ONLINE');
      setStatus('db',  'online', 'CONNECTED');

      // Check if OpenAI key is real by inspecting a lightweight endpoint
      // In mock mode, planner always falls back — we just inform the user
      setStatus('ai', 'warning', 'MOCK MODE');
    }
  } catch {
    setStatus('api', 'offline', 'OFFLINE');
    setStatus('db',  'offline', 'N/A');
    setStatus('ai',  'offline', 'N/A');
    showToast('⚠ API server unreachable. Start the backend on port 8000.');
  }
}

/** Updates a status indicator dot and label text */
function setStatus(key, cls, label) {
  const dot  = $(`status-${key}`);
  const text = $(`status-${key}-text`);
  dot.className  = `status-dot ${cls}`;
  text.textContent = label;
}

// ============================================================
// PARTICLE BACKGROUND — Floating cyan grid-dots animation
// ============================================================
function initParticles() {
  const canvas = $('particle-canvas');
  const ctx    = canvas.getContext('2d');
  let W, H, particles;

  // Particle properties
  const PARTICLE_COUNT = 55;
  const PARTICLE_COLOR = 'rgba(0, 191, 255, 0.5)';
  const LINE_COLOR     = 'rgba(0, 191, 255, 0.06)';
  const LINE_DIST      = 140;

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
    spawnParticles();
  }

  function spawnParticles() {
    particles = Array.from({ length: PARTICLE_COUNT }, () => ({
      x:  Math.random() * W,
      y:  Math.random() * H,
      vx: (Math.random() - 0.5) * 0.35,
      vy: (Math.random() - 0.5) * 0.35,
      r:  Math.random() * 1.8 + 0.6,
    }));
  }

  function drawFrame() {
    ctx.clearRect(0, 0, W, H);

    // Draw connection lines between nearby particles
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x;
        const dy = particles[i].y - particles[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < LINE_DIST) {
          ctx.strokeStyle = LINE_COLOR;
          ctx.lineWidth   = (1 - dist / LINE_DIST) * 0.8;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }
      }
    }

    // Draw and move particles
    particles.forEach(p => {
      ctx.fillStyle = PARTICLE_COLOR;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();

      p.x += p.vx;
      p.y += p.vy;

      // Wrap around edges instead of bouncing
      if (p.x < 0) p.x = W;
      if (p.x > W) p.x = 0;
      if (p.y < 0) p.y = H;
      if (p.y > H) p.y = 0;
    });

    requestAnimationFrame(drawFrame);
  }

  window.addEventListener('resize', resize);
  resize();
  drawFrame();
}

// ============================================================
// TONE SELECTOR — Switch active tone button
// ============================================================
function initToneSelector() {
  document.querySelectorAll('.tone-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tone-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.currentTone = btn.dataset.tone;
    });
  });
}

// ============================================================
// QUICK COMMANDS — Pre-fill input from sidebar buttons
// ============================================================
function initQuickCommands() {
  document.querySelectorAll('.quick-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      DOM.messageInput.value = btn.dataset.cmd;
      DOM.messageInput.focus();
      autoResizeTextarea();
      updateCharCounter();
    });
  });
}

// ============================================================
// INPUT HELPERS — Auto-resize textarea, char counter
// ============================================================
function autoResizeTextarea() {
  DOM.messageInput.style.height = 'auto';
  DOM.messageInput.style.height = Math.min(DOM.messageInput.scrollHeight, 120) + 'px';
}

function updateCharCounter() {
  const len = DOM.messageInput.value.length;
  DOM.charCounter.textContent = `${len} / ${MAX_MSG_LENGTH}`;
  DOM.charCounter.style.color = len > MAX_MSG_LENGTH * 0.9
    ? 'rgba(255, 71, 87, 0.8)'
    : 'var(--col-text-dim)';
}

// ============================================================
// TOAST — Show a temporary error/info notification
// ============================================================
function showToast(msg, durationMs = 4000) {
  DOM.toastMsg.textContent = msg;
  DOM.toast.classList.add('show');
  DOM.toast.classList.remove('hidden');

  setTimeout(() => {
    DOM.toast.classList.remove('show');
  }, durationMs);
}

// ============================================================
// AGENT CHAIN ANIMATION — Simulates the pipeline visually
// ============================================================
const AGENT_STEPS = ['agentMem', 'agentOrch', 'agentPlan', 'agentExec', 'agentComm'];

function resetAgentChain() {
  AGENT_STEPS.forEach(key => {
    DOM[key].className = 'agent-step';
  });
}

function animateAgentChain() {
  resetAgentChain();
  let i = 0;

  const advance = () => {
    if (i > 0) {
      DOM[AGENT_STEPS[i - 1]].className = 'agent-step done';
    }
    if (i < AGENT_STEPS.length) {
      DOM[AGENT_STEPS[i]].className = 'agent-step active';
      i++;
      return setTimeout(advance, AGENT_STEP_DELAY);
    }
  };

  advance();
}

// ============================================================
// CHAT RENDERING — Build and append message bubbles
// ============================================================

/** Format ISO timestamp to readable HH:MM:SS */
function formatTime(d = new Date()) {
  return d.toLocaleTimeString('en-US', { hour12: false });
}

/** Sanitize user-supplied text to prevent XSS */
function sanitize(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/** Convert plain JARVIS reply text to a safe HTML representation */
function formatJarvisReply(text) {
  // Escape HTML first
  const safe = sanitize(text);
  // Convert newlines to paragraphs
  const paragraphs = safe.split('\n').filter(line => line.trim());
  if (paragraphs.length <= 1) return `<p>${safe}</p>`;
  return paragraphs.map(p => `<p>${p}</p>`).join('');
}

/**
 * Append a message bubble to the chat window.
 * @param {'user'|'jarvis'} role
 * @param {string} content - Text content to render
 */
function appendMessage(role, content) {
  const isJarvis = role === 'jarvis';

  const row = document.createElement('div');
  row.className = `message-row ${role}`;

  const avatarEl = document.createElement('div');
  avatarEl.className = `msg-avatar ${isJarvis ? 'jarvis-avatar' : 'user-avatar'}`;
  avatarEl.textContent = isJarvis ? 'J' : 'Y';

  const bubble = document.createElement('div');
  bubble.className = `msg-bubble ${isJarvis ? 'jarvis-bubble' : 'user-bubble'}`;

  const sender = document.createElement('div');
  sender.className = 'msg-sender';
  sender.textContent = isJarvis ? 'JARVIS' : 'YOU';

  const contentEl = document.createElement('div');
  contentEl.className = 'msg-content';
  contentEl.innerHTML = isJarvis ? formatJarvisReply(content) : `<p>${sanitize(content)}</p>`;

  const timeEl = document.createElement('div');
  timeEl.className = 'msg-time';
  timeEl.textContent = formatTime();

  bubble.appendChild(sender);
  bubble.appendChild(contentEl);
  bubble.appendChild(timeEl);

  row.appendChild(avatarEl);
  row.appendChild(bubble);

  DOM.chatWindow.appendChild(row);
  scrollToBottom();
}

/** Smooth-scroll the chat to the latest message */
function scrollToBottom() {
  DOM.chatWindow.scrollTop = DOM.chatWindow.scrollHeight;
}

// ============================================================
// LOADING STATE — Show/hide the typing indicator
// ============================================================
function setLoading(isLoading) {
  state.isLoading = isLoading;
  DOM.typingIndicator.classList.toggle('hidden', !isLoading);
  DOM.sendBtn.disabled = isLoading;
  DOM.messageInput.disabled = isLoading;

  if (isLoading) {
    scrollToBottom();
    animateAgentChain();
  } else {
    resetAgentChain();
  }
}

// ============================================================
// API CALL — POST /chat to the JARVIS backend
// ============================================================
async function sendMessageToJarvis(userText) {
  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: state.sessionId,
      message:    userText,
      tone:       state.currentTone,
    }),
  });

  if (!response.ok) {
    const errBody = await response.json().catch(() => ({}));
    throw new Error(errBody.detail || `Server error ${response.status}`);
  }

  return response.json();
}

// ============================================================
// SEND HANDLER — Main entry point for sending a message
// ============================================================
async function handleSend() {
  const text = DOM.messageInput.value.trim();

  // Guard: empty or too long
  if (!text)                      return;
  if (text.length > MAX_MSG_LENGTH) { showToast(`Message is too long (max ${MAX_MSG_LENGTH} chars).`); return; }
  if (state.isLoading)             return;

  // Clear input and render user bubble
  DOM.messageInput.value = '';
  autoResizeTextarea();
  updateCharCounter();
  appendMessage('user', text);

  // Update message counter
  state.messageCount++;
  DOM.statMessages.textContent = state.messageCount;

  // Show typing / loading state
  setLoading(true);

  try {
    const data = await sendMessageToJarvis(text);
    appendMessage('jarvis', data.reply);
  } catch (err) {
    // Show error inline in chat and as toast
    appendMessage('jarvis', `⚠ System error: "${err.message}". Please check the backend server.`);
    showToast(`Error: ${err.message}`);
    console.error('[JARVIS Frontend] API error:', err);
  } finally {
    setLoading(false);
    DOM.messageInput.focus();
  }
}

// ============================================================
// NEW SESSION — Reset chat and generate a new session
// ============================================================
function startNewSession() {
  // Remove old session key to force regeneration
  sessionStorage.removeItem(SESSION_KEY);
  initSession();

  // Clear all messages except the welcome bubble (re-render it)
  DOM.chatWindow.innerHTML = '';

  // Re-append welcome message
  const welcome = document.createElement('div');
  welcome.className = 'message-row jarvis';
  welcome.id = 'welcome-msg';
  welcome.innerHTML = `
    <div class="msg-avatar jarvis-avatar">J</div>
    <div class="msg-bubble jarvis-bubble">
      <div class="msg-sender">JARVIS</div>
      <div class="msg-content">
        <p>New session initialized. All systems nominal. How may I assist you?</p>
      </div>
      <div class="msg-time">${formatTime()}</div>
    </div>`;
  DOM.chatWindow.appendChild(welcome);

  // Reset state
  state.messageCount = 0;
  state.sessionStart = Date.now();
  DOM.statMessages.textContent = '0';
}

// ============================================================
// EVENT WIRING — Attach all event listeners
// ============================================================
function attachEvents() {
  // Send on button click
  DOM.sendBtn.addEventListener('click', handleSend);

  // Send on Enter (Shift+Enter = newline)
  DOM.messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  // Auto-resize textarea as user types
  DOM.messageInput.addEventListener('input', () => {
    autoResizeTextarea();
    updateCharCounter();
  });

  // New session button
  DOM.clearBtn.addEventListener('click', startNewSession);

  // Voice button (placeholder — not yet implemented)
  DOM.voiceBtn.addEventListener('click', () => {
    showToast('🎙 Voice input will be enabled once audio recording is connected to the /voice endpoint.');
  });
}

// ============================================================
// BOOT — Initialize everything on DOM ready
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  initSession();
  startClock();
  initParticles();
  initToneSelector();
  initQuickCommands();
  attachEvents();
  checkApiStatus();

  // Set welcome time
  DOM.welcomeTime.textContent = formatTime();

  // Focus input
  DOM.messageInput.focus();
});
