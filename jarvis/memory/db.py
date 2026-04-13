import json
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from config.settings import settings

# Gracefully support either SQLite (dev fallback) or PostgreSQL (Production)
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ─── Auth / User ─────────────────────────────────────────────────────────────

# Multi-user support with hashed passwords
class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at    = Column(DateTime, default=datetime.utcnow)

# ─── Tables ───────────────────────────────────────────────────────────────────

# Stores every conversation turn (user messages + JARVIS replies)
class Interaction(Base):
    __tablename__ = "interactions"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True) # Evolved to logically group by user_id
    role       = Column(String)    # 'user' or 'assistant'
    content    = Column(Text)
    timestamp  = Column(DateTime, default=datetime.utcnow)


# Stores persistent key-value user preferences (e.g. preferred tone, name)
class Preference(Base):
    __tablename__ = "preferences"

    id    = Column(Integer, primary_key=True, index=True)
    key   = Column(String, unique=True, index=True)
    value = Column(Text)


# Stores executed tool step results for audit and memory recall
class StepResult(Base):
    __tablename__ = "step_results"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    plan_id    = Column(String, index=True)
    step_id    = Column(Integer)
    tool       = Column(String)
    status     = Column(String)           # success | failed | blocked | skipped
    output     = Column(Text, nullable=True)
    error      = Column(Text, nullable=True)
    timestamp  = Column(DateTime, default=datetime.utcnow)


# Stores scheduled / deferred tasks for the background agent to execute
class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True)
    description= Column(Text)              # Human-readable description
    tool       = Column(String)            # Which tool to call
    params     = Column(Text)             # JSON-encoded params dict
    run_at     = Column(DateTime)         # When to execute
    
    priority   = Column(Integer, default=1)  # 1 (Normal), 2 (High), etc.
    recurrence = Column(String, nullable=True) # e.g. 'daily', 'hourly'
    
    retries    = Column(Integer, default=0)
    max_retries= Column(Integer, default=3)
    status     = Column(String, default="pending") # pending, completed, failed
    
    created_at = Column(DateTime, default=datetime.utcnow)

# ─── Cognitive Architecture ──────────────────────────────────────────────────

class ToolMetric(Base):
    __tablename__ = "tool_metrics"
    
    id            = Column(Integer, primary_key=True, index=True)
    tool_name     = Column(String, unique=True, index=True)
    success_count = Column(Integer, default=0)
    fail_count    = Column(Integer, default=0)
    
class EventTrigger(Base):
    __tablename__ = "event_triggers"
    
    id            = Column(Integer, primary_key=True, index=True)
    session_id    = Column(String, index=True)
    condition     = Column(String) # e.g., "if price drops below 500"
    action_desc   = Column(String) # e.g., "Notify me"
    last_checked  = Column(DateTime, default=datetime.utcnow)
    active        = Column(Boolean, default=True)


class PlanPattern(Base):
    __tablename__ = "plan_patterns"
    
    id          = Column(Integer, primary_key=True, index=True)
    goal        = Column(String, index=True)
    tool_chain  = Column(Text)   # JSON literal of [tool1, tool2, ...]
    outcome     = Column(String) # success, failed
    timestamp   = Column(DateTime, default=datetime.utcnow)

# ─── Hybrid Cloud/Local Extension ─────────────────────────────────────────────

# Tracks overall plan execution state reliably, avoiding large fragile local JSON dumps
class ExecutionState(Base):
    __tablename__ = "execution_states"
    
    id              = Column(Integer, primary_key=True, index=True)
    session_id      = Column(String, index=True)
    plan_id         = Column(String, unique=True, index=True)
    request_id      = Column(String, index=True)          # Links observability chain upward
    
    current_step    = Column(Integer, default=0)          # Pointer
    steps           = Column(Text)                        # Plan JSON or strict representation
    completed_steps = Column(Text, default="[]")          # JSON array of successfully run step IDs
    
    status          = Column(String, default="running")   # running, waiting_for_local, completed, failed
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Queued tasks explicitly bound to run locally on the user's personal context
class LocalTask(Base):
    __tablename__ = "local_tasks"
    
    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, index=True)       # The owner of the local agent
    agent_id    = Column(String, index=True)        # Specifically assigned distributed agent instance
    
    idempotency_key = Column(String, unique=True, index=True) # Prevent duplicates across network
    
    request_id  = Column(String, index=True)        # Observability chain
    session_id  = Column(String, index=True)
    plan_id     = Column(String, index=True)
    step_id     = Column(String, index=True)
    plan_json   = Column(Text, nullable=True)       # Deprecated when USE_EXECUTION_STATE=True

    action      = Column(String)                    # Tool name
    params      = Column(Text)                      # JSON serialized kwargs
    
    status      = Column(String, default="pending") # pending, running, completed, failed
    result      = Column(Text, nullable=True)       
    
    priority        = Column(Integer, default=1)
    retries         = Column(Integer, default=0)
    max_retries     = Column(Integer, default=3)
    last_attempt_at = Column(DateTime, nullable=True)
    
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Online presence tracker for the user's remote local agent
class AgentStatus(Base):
    __tablename__ = "agent_status"
    
    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, index=True)
    agent_id       = Column(String, unique=True, index=True) # Identify instances
    last_heartbeat = Column(DateTime, default=datetime.utcnow)
    status         = Column(String, default="offline") # online, offline

# ─── Init ─────────────────────────────────────────────────────────────────────

# Create all tables if they do not yet exist
def init_db():
    Base.metadata.create_all(bind=engine)
