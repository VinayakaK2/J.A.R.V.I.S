from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)

class Skill(BaseModel):
    name: str
    description: str
    when_to_use: List[str]
    instructions: str
    examples: List[Dict[str, str]] = Field(default_factory=list)
    priority: float = 0.5
    version: int = 1
    status: str = "core"  # "core", "exploration", "archived"
    family: Optional[str] = None  # To cluster versions together, e.g., 'code_debugging'
    promoted_at: Optional[float] = None
    demoted_at: Optional[float] = None
    mitigation_stage: int = 0  # 0=None, 1=Weight Reduction, 2=Canary Only, 3=Rollback

class SkillRegistry:
    MAX_VERSIONS_PER_FAMILY = 4

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._register_default_skills()
        
    def register_skill(self, skill: Skill):
        if not skill.family:
            skill.family = skill.name.split('_v')[0] if '_v' in skill.name else skill.name

        self._skills[skill.name] = skill
        self._enforce_version_limits(skill.family)

    def _enforce_version_limits(self, family: str):
        # Find all skills in this family
        family_skills = [s for s in self._skills.values() if s.family == family]
        
        # Sort by version strictly descending
        family_skills.sort(key=lambda s: s.version, reverse=True)
        
        # Archive any versions beyond the allowed max to prevent registry bloat
        for old_skill in family_skills[self.MAX_VERSIONS_PER_FAMILY:]:
            if old_skill.status != "archived":
                old_skill.status = "archived"
                logger.info(f"[SkillRegistry] Archived older skill version: {old_skill.name}")
        
    def get_all_skills(self, include_archived: bool = False) -> List[Skill]:
        if include_archived:
            return list(self._skills.values())
        return [s for s in self._skills.values() if s.status != "archived"]
        
    def get_skill(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def _register_default_skills(self):
        # 1. Code Debugging
        self.register_skill(Skill(
            name="code_debugging",
            description="Fix bugs in code.",
            when_to_use=["error", "bug", "fix code", "crash", "exception", "traceback", "debug"],
            instructions="""1. Identify the root cause of the error.
2. Trace the sequence of events leading to the bug.
3. Suggest an explicitly safe and precise code change.
4. Verify logical assumptions around the crashed component.""",
            examples=[{"input": "Fix ValueError in parser", "output": "Trace traceback to line 45, check type conversion assumptions."}],
            priority=0.8
        ))
        
        # 2. Web Automation
        self.register_skill(Skill(
            name="web_automation",
            description="Browser actions and navigation.",
            when_to_use=["browser", "navigate", "web", "url", "click", "scrape", "search youtube", "open chrome"],
            instructions="""1. Identify the target URL and navigation goals.
2. Use tools to load the page and wait for elements if needed.
3. Keep navigation paths realistic, avoiding clicking un-rendered elements.
4. Surface any captured information cleanly.""",
            priority=0.7
        ))
        
        # 3. File Handling
        self.register_skill(Skill(
            name="file_handling",
            description="Read and write files.",
            when_to_use=["file", "read", "write", "create a file", "edit document", "app.py", "save"],
            instructions="""1. Determine the path and ensure it's absolute or correctly relative.
2. Use the minimal file editing scope needed. Avoid complete rewrites if partial replacements work.
3. Validate written content format (e.g., proper JSON closing braces).""",
            priority=0.6
        ))
        
        # 4. Communication
        self.register_skill(Skill(
            name="communication",
            description="Messaging and responses.",
            when_to_use=["message", "whatsapp", "telegram", "email", "respond", "send message", "say"],
            instructions="""1. Draft the message confirming its intended tone.
2. Verify the recipient address/number.
3. Use strict, non-retryable actions for sending to avoid spam. Ensure the action triggers only once.""",
            priority=0.9
        ))
        
        # 5. Planning Optimization
        self.register_skill(Skill(
            name="planning_optimization",
            description="Break down complex tasks efficiently.",
            when_to_use=["plan", "complex", "strategy", "multiple", "steps", "architect"],
            instructions="""1. Break the user intent into logical sub-goals.
2. Use tools in a sequence that minimizes redundant calls.
3. Build branching strategies if later steps depend heavily on the output of earlier ones.""",
            priority=0.5
        ))

# Global instance
skill_registry = SkillRegistry()
