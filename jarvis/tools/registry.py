from typing import Callable, Dict, Optional
import tools.actions as actions
import desktop_agent.actions as desktop_actions
import desktop_agent.web_actions as web_actions
import desktop_agent.winauto_actions as winauto_actions

# Maps every registered tool name to its callable function
class ToolRegistry:
    def __init__(self):
        self.registry: Dict[str, Callable] = {
            "create_file":      actions.create_file,
            "read_file":        actions.read_file,
            "search_web":       actions.search_web,
            "send_whatsapp":    actions.send_whatsapp,
            "send_telegram":    actions.send_telegram,
            "run_code_sandbox": actions.run_code_sandbox,
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

        # Human-readable descriptions injected into the planner prompt
        self.descriptions: Dict[str, str] = {
            "create_file":      "Create a file in the workspace. Params: name (str), content (str).",
            "read_file":        "Read a file from the workspace. Params: name (str).",
            "search_web":       "Search the web via DuckDuckGo. Params: query (str).",
            "send_whatsapp":    "Send a WhatsApp message via Twilio. Params: number (str, e.g. +91XXXXXXXXXX), message (str).",
            "send_telegram":    "Send a Telegram message via Bot API. Params: chat_id (str), message (str).",
            "run_code_sandbox": "Execute a Python code snippet safely. Params: code (str).",
            # Desktop descriptions
            "open_application": "Open a whitelisted application (e.g. 'chrome', 'notepad'). Params: app_name (str).",
            "open_website":     "Open a URL in the default native browser natively. Params: url (str).",
            "type_text":        "Type raw text over the currently active screen window. Params: text (str).",
            "press_keys":       "Press a keyboard key or hotkey combination (e.g. 'enter', 'ctrl+c'). Params: keys (str).",
            "click":            "Click the mouse at exact screen coordinates. Params: x (int), y (int).",
            # Playwright Web Actions
            "open_url":         "Open a URL in deterministic browser. Params: url (str).",
            "search_google":    "Visually search Google via Deterministic browser. Params: query (str).",
            "click_selector":   "Click a DOM selector in deterministic browser. Params: selector (str).",
            "fill_input":       "Fill a DOM element with text in deterministic browser. Params: selector (str), text (str).",
            # WinAuto Accessibility Native Control
            "click_native_element": "Natively click an accessible UI element without mouse coordinates. Params: app_title (str), element_title (str).",
            "fill_native_input": "Type securely into an accessible OS UI element control. Params: app_title (str), element_title (str), text (str).",
        }

    # Retrieve the callable function for a given tool name
    def get_tool(self, name: str) -> Optional[Callable]:
        return self.registry.get(name)

    # Return all registered tool names
    def get_available_tools(self) -> list:
        return list(self.registry.keys())

    # Return the descriptions dict used by the planner to build its prompt
    def get_tool_descriptions(self) -> Dict[str, str]:
        return self.descriptions

# Global registry instance shared across the application
registry = ToolRegistry()
