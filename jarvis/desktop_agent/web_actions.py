import logging
from typing import Optional
from playwright.sync_api import sync_playwright, Page, Browser

logger = logging.getLogger(__name__)

class WebAgent:
    """Manages deterministic persistent browser automation sessions."""
    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    def _ensure_browser(self):
        if not self._playwright:
            self._playwright = sync_playwright().start()
        if not self._browser:
            # We run in non-headless so the vision system can still physically see it 
            # if we wanted, or fallback to pure scraping easily. But for OS interaction, headless=False is best.
            self._browser = self._playwright.chromium.launch(headless=False)
        if not self._page or self._page.is_closed():
            self._page = self._browser.new_page()

    def open_url(self, url: str) -> str:
        try:
            self._ensure_browser()
            self._page.goto(url, wait_until="networkidle")
            logger.info(f"[WebAgent] Opened URL: {url}")
            return f"Successfully opened {url}. Page title: {self._page.title()}"
        except Exception as e:
            return f"Error opening URL: {e}"

    def search_google(self, query: str) -> str:
        try:
            self._ensure_browser()
            self._page.goto("https://www.google.com")
            # Dismiss cookie consent if visible (rudimentary check)
            try:
                self._page.click("button:has-text('Accept all')", timeout=1000)
            except:
                pass
            
            self._page.fill("textarea[name='q']", query)
            self._page.press("textarea[name='q']", "Enter")
            self._page.wait_for_load_state("networkidle")
            logger.info(f"[WebAgent] Searched Google for: {query}")
            return f"Successfully executed Google Search for {query}."
        except Exception as e:
            return f"Error searching google: {e}"

    def click_selector(self, selector: str) -> str:
        try:
            self._ensure_browser()
            self._page.click(selector, timeout=5000)
            logger.info(f"[WebAgent] Clicked selector: {selector}")
            return f"Successfully clicked {selector}"
        except Exception as e:
            return f"Error clicking selector: {e}"

    def fill_input(self, selector: str, text: str) -> str:
        try:
            self._ensure_browser()
            self._page.fill(selector, text, timeout=5000)
            logger.info(f"[WebAgent] Filled input '{selector}' with text.")
            return f"Successfully filled {selector}"
        except Exception as e:
            return f"Error filling input: {e}"

# Singleton instance
web_agent = WebAgent()

# Exposable functions tying into tool registry
def open_url(url: str) -> str:
    return web_agent.open_url(url)

def search_google(query: str) -> str:
    return web_agent.search_google(query)

def click_selector(selector: str) -> str:
    return web_agent.click_selector(selector)

def fill_input(selector: str, text: str) -> str:
    return web_agent.fill_input(selector, text)
