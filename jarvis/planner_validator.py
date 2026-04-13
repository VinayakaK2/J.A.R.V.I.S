import logging
from typing import Tuple

from planner import Plan
from tools.registry import registry

logger = logging.getLogger(__name__)

class PlannerValidator:
    """Intelligent validation layer checking planner outputs against system constraints."""
    
    def __init__(self):
        self.available_tools = set(registry.get_available_tools())
        
    def validate_plan(self, plan: Plan) -> Tuple[bool, str]:
        """Validates all steps rigidly against allowed operations and structural integrity."""
        if not plan.steps:
            return False, "Plan has no steps."
            
        for step in plan.steps:
            # Check Tool exists
            if step.tool not in self.available_tools:
                reason = f"Step {step.step_id} requested unregistered tool '{step.tool}'."
                logger.warning(f"[Validator] {reason}")
                return False, reason
            
            # Additional logic can be applied here to strictly evaluate params
            # e.g. checking length constraints, ensuring "click" has ints.
            if step.tool == "click":
                if not isinstance(step.params.get("x"), (int, float)) or not isinstance(step.params.get("y"), (int, float)):
                    reason = f"Step {step.step_id} 'click' requires numerical x and y."
                    return False, reason
            elif step.tool == "type_text" or step.tool == "press_keys":
                if "text" not in step.params and "keys" not in step.params:
                    reason = f"Step {step.step_id} typing tools require 'text' or 'keys' dicts."
                    return False, reason
            elif step.tool == "open_application":
                if "app_name" not in step.params:
                    return False, "open_application requires 'app_name' param."

        return True, "Plan is structurally valid."

# Global singleton
validator = PlannerValidator()
