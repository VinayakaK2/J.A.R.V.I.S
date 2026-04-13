import json
import logging
from typing import List, Dict
from sqlalchemy import Column, Integer, String, Text, DateTime
from memory.db import Base, SessionLocal
from datetime import datetime

logger = logging.getLogger(__name__)

class PlanPattern(Base):
    __tablename__ = "plan_patterns"
    
    id          = Column(Integer, primary_key=True, index=True)
    goal        = Column(String, index=True)
    tool_chain  = Column(Text)   # JSON literal of [tool1, tool2, ...]
    outcome     = Column(String) # success, failed
    timestamp   = Column(DateTime, default=datetime.utcnow)

class PatternLearningEngine:
    def record_pattern(self, goal: str, steps: List[Dict], success: bool):
        try:
            tools = [s.get('tool', 'unknown') for s in steps]
            with SessionLocal() as db:
                p = PlanPattern(
                    goal=goal,
                    tool_chain=json.dumps(tools),
                    outcome="success" if success else "failed"
                )
                db.add(p)
                db.commit()
        except Exception as e:
            logger.error(f"[Patterns] Error recording pattern: {e}")

    def get_bias_for_goal(self, goal: str) -> str:
        """Looks up similar goals and injects success/failure patterns into the planner"""
        try:
            with SessionLocal() as db:
                # Naive matching for demonstration. Use embeddings (e.g., pgvector) for true prod
                patterns = db.query(PlanPattern).filter(PlanPattern.goal.ilike(f"%{goal}%")).all()
                if not patterns:
                    return ""
                
                successes = [p.tool_chain for p in patterns if p.outcome == "success"]
                failures = [p.tool_chain for p in patterns if p.outcome == "failed"]
                
                bias = ""
                if successes:
                    bias += f"Historically SUCCESSFUL tool sequence for similar goals: {successes[-1]}.\n"
                if failures:
                    bias += f"Historically FAILED tool sequence. AVOID: {failures[-1]}.\n"
                return bias
        except:
            return ""

patterns_engine = PatternLearningEngine()
