from typing import List
from skills.registry import Skill

def inject_skills_into_prompt(skills: List[Skill]) -> str:
    if not skills:
        return ""
        
    parts = ["### ACTIVE SKILLS", ""]
    
    # 4. Skill Execution Boundary rule
    parts.append("IMPORTANT BOUNDARY RULE:")
    parts.append("Skills guide reasoning only. All actions must still go through the tool system.")
    parts.append("Use skills as guidance, not strict rules. Adapt based on current context.\n")
    
    for s in skills:
        parts.append(f"[Skill]")
        parts.append(f"Name: {s.name}")
        parts.append(f"When to use: {', '.join(s.when_to_use)}")
        parts.append(f"Instructions:\n{s.instructions}")
        if s.examples:
            parts.append("Examples:")
            for ex in s.examples:
                parts.append(f"  Input: {ex.get('input', '')}")
                parts.append(f"  Output: {ex.get('output', '')}")
        parts.append("")
        
    return "\n".join(parts)
