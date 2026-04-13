import logging
import platform

logger = logging.getLogger(__name__)

class WinAutoAgent:
    def __init__(self):
        self.is_windows = platform.system() == "Windows"
        
    def _execute_safe(self, action_name: str, app_title: str, func, *args):
        if not self.is_windows:
            return f"Action {action_name} aborted. PyWinAuto requires a native Windows environment."
        try:
            from pywinauto.application import Application
            # Connects to the active window by regex matching its Title
            app = Application(backend="uia").connect(title_re=f".*{app_title}.*", timeout=5)
            dlg = app.top_window()
            return func(dlg, *args)
        except Exception as e:
            logger.error(f"[WinAuto] Failed targeting {app_title}: {e}")
            return f"Error executing native UI access: {str(e)}"

    def click_element(self, app_title: str, element_title: str) -> str:
        """Clicks an explicit element by its automation ID or Title natively"""
        def _click(dlg, title):
            element = dlg.child_window(title=title, control_type="Button")
            element.click_input()
            return f"Clicked natively on '{title}' in '{app_title}'"
            
        return self._execute_safe("click_element", app_title, _click, element_title)

    def fill_input(self, app_title: str, element_title: str, text: str) -> str:
        """Types directly into an explicit control element."""
        def _fill(dlg, title, txt):
            # Target an Edit control
            element = dlg.child_window(title=title, control_type="Edit")
            element.set_edit_text(txt)
            return f"Typed '{text}' into '{title}' inside '{app_title}'"
            
        return self._execute_safe("fill_input", app_title, _fill, element_title, text)

# Global singleton
win_agent = WinAutoAgent()

def click_native_element(app_title: str, element_title: str) -> str:
    return win_agent.click_element(app_title, element_title)

def fill_native_input(app_title: str, element_title: str, text: str) -> str:
    return win_agent.fill_input(app_title, element_title, text)
