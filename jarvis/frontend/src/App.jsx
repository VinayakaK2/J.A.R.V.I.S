import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './index.css';

const API_URL = 'http://localhost:8000';

function App() {
  const [token, setToken] = useState(localStorage.getItem('jarvis_token') || null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [chatLog, setChatLog] = useState([]);
  const [inputText, setInputText] = useState('');
  const [sysLogs, setSysLogs] = useState([]);
  const [activeTab, setActiveTab] = useState('chat'); // 'chat' | 'logs'
  const sessionId = "frontend_sess_1";

  // Auth Setters
  const login = async () => {
    try {
      const res = await axios.post(`${API_URL}/login`, { username, password });
      setToken(res.data.access_token);
      localStorage.setItem('jarvis_token', res.data.access_token);
    } catch (e) { alert("Login Failed: " + String(e)); }
  };

  const register = async () => {
    try {
      await axios.post(`${API_URL}/register`, { username, password });
      alert("Registered Successfully! You can now log in.");
    } catch (e) { alert("Registration Failed: " + String(e)); }
  };

  // Agent Interfacing
  const sendChat = async () => {
    if(!inputText) return;
    const msg = inputText;
    setInputText("");
    const newLog = [...chatLog, { role: "user", content: msg }];
    setChatLog(newLog);

    try {
      const res = await axios.post(
        `${API_URL}/chat`, 
        { session_id: sessionId, message: msg, tone: "professional" },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setChatLog([...newLog, { role: "assistant", content: res.data.reply }]);
    } catch (e) {
      setChatLog([...newLog, { role: "assistant", content: "[Error Communicating with JARVIS]" }]);
    }
  };

  const fetchLogs = async () => {
    try {
      const res = await axios.get(`${API_URL}/logs?limit=50`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setSysLogs(res.data.logs);
    } catch (e) { console.error("Failed to load logs"); }
  };

  useEffect(() => {
    if(token && activeTab === 'logs') {
      fetchLogs();
    }
  }, [token, activeTab]);

  // View: Not Authenticated
  if (!token) {
    return (
      <div className="auth-container">
        <h1>JARVIS v4</h1>
        <p>Login to Access Autonomous Capabilities</p>
        <div className="auth-form">
          <input placeholder="Username" value={username} onChange={e => setUsername(e.target.value)} />
          <input type="password" placeholder="Password" value={password} onChange={e => setPassword(e.target.value)} />
          <div className="auth-buttons">
            <button onClick={login}>Login</button>
            <button onClick={register} className="secondary">Register</button>
          </div>
        </div>
      </div>
    );
  }

  // View: Authenticated Dashboard
  return (
    <div className="dashboard-container">
      <nav className="sidebar">
        <h2>JARVIS <sub>v4</sub></h2>
        <button className={activeTab === 'chat' ? 'active' : ''} onClick={() => setActiveTab('chat')}>💬 Agent Chat</button>
        <button className={activeTab === 'logs' ? 'active' : ''} onClick={() => setActiveTab('logs')}>📊 Execution Logs</button>
        <div className="spacer"></div>
        <button className="danger" onClick={() => { setToken(null); localStorage.removeItem('jarvis_token'); }}>Logout</button>
      </nav>

      <main className="main-content">
        {activeTab === 'chat' ? (
          <div className="chat-interface">
            <div className="chat-history">
              {chatLog.map((log, i) => (
                <div key={i} className={`chat-bubble ${log.role}`}>
                  <strong>{log.role === 'user' ? 'You: ' : 'JARVIS: '}</strong>
                  <span>{log.content}</span>
                </div>
              ))}
            </div>
            <div className="chat-input-area">
              <input 
                value={inputText} 
                onChange={(e) => setInputText(e.target.value)} 
                onKeyDown={(e) => { if(e.key === 'Enter') sendChat() }}
                placeholder="Ask JARVIS to open an app, search something, etc..." 
              />
              <button onClick={sendChat}>Send</button>
            </div>
          </div>
        ) : (
          <div className="logs-interface">
            <h2>Live Observability Trace</h2>
            <button onClick={fetchLogs} className="refresh-btn">Refresh</button>
            <div className="logs-list">
              {sysLogs.map((log, i) => (
                <div key={i} className="log-entry">
                  <span className="timestamp">{log.timestamp.split('.')[0]}</span>
                  <span className={`event-badge ${log.event_type}`}>{log.event_type}</span>
                  <pre>{JSON.stringify(log.details, null, 2)}</pre>
                </div>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
