import json
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from config.settings import settings

# SQLAlchemy engine — check_same_thread=False required for SQLite with async usage
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
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

# ─── Init ─────────────────────────────────────────────────────────────────────

# Create all tables if they do not yet exist
def init_db():
    Base.metadata.create_all(bind=engine)
