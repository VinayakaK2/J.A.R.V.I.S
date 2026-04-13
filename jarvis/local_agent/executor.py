import logging
from typing import Callable, Dict, Any, Optional

import desktop_agent.actions as desktop_actions
import desktop_agent.web_actions as web_actions
import desktop_agent.winauto_actions as winauto_actions

logger = logging.getLogger(__name__)

# Subset registry specifically enforcing isolation of only desktop interaction tools
LOCAL_TOOLS: Dict[str, Callable] = {
    # Desktop automation tools (Native)
    "open_application": desktop_actions.open_application,
    "open_website":     desktop_actions.open_website,
    "type_text":        desktop_actions.type_text,
    "press_keys":       desktop_actions.press_keys,
    "click":            desktop_actions.click,
    # Desktop automation tools (Web/Playwright)
    "open_url":         web_actions.open_url,
    "search_google":    web_actions.search_google,
    "click_selector":   web_actions.click_selector,
    "fill_input":       web_actions.fill_input,
    # Windows Accessible Desktop Action (PyWinAuto)
    "click_native_element": winauto_actions.click_native_element,
    "fill_native_input":    winauto_actions.fill_native_input,
}

def execute_local_task(action: str, params: Dict[str, Any]) -> str:
    """Invokes a permitted local desktop tool and returns its stringified output."""
    tool_fn = LOCAL_TOOLS.get(action)
    if not tool_fn:
        error_msg = f"Security Violation or Missing Tool: '{action}' is not permitted to run locally."
        logger.error(f"[LocalExecutor] {error_msg}")
        raise ValueError(error_msg)

    logger.info(f"[LocalExecutor] Executing '{action}' with params: {params}")
    try:
        result = tool_fn(**params)
        return str(result)
    except Exception as e:
        logger.error(f"[LocalExecutor] Execution failed for '{action}': {e}")
        raise
