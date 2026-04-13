"""
tests/test_semantic_perception.py

Standalone test harness for perception/semantic.py validation.

Runs three test suites against the live OS:
  Suite 1 — Deterministic Targeting
  Suite 2 — Ambiguity Handling
  Suite 3 — OS Interference (notification during perception)

Captures and formats:
  - PERCEPTION_CYCLE logs
  - ELEMENT_RESOLUTION logs
  - SEMANTIC_ANALYSIS_COMPLETE logs
  - Fallback trigger detection
  - Ambiguity flags

Usage:
  cd h:\\Jarvis\\jarvis
  python tests/test_semantic_perception.py

Prerequisites:
  - Chrome installed and in PATH
  - USE_SEMANTIC_PERCEPTION = True in settings (already set)
  - OpenAI API key in .env
"""

import sys
import os
import time
import json
import subprocess
import textwrap
from typing import Any, Dict, List, Optional
from datetime import datetime

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Path setup so imports resolve from project root ─────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── CLI flags ─────────────────────────────────────────────────────────────────
# Pass --no-llm to skip GPT-4o Stage 3 and test only Stages 1 & 2 (no API key required)
NO_LLM = "--no-llm" in sys.argv

# ── Intercept structured_logger to capture events in-process ─────────────────
captured_events: List[Dict[str, Any]] = []

class _CapturingLogger:
    """
    Wraps the real structured_logger and mirrors every log_event call
    into our captured_events list for test assertion.
    """
    def __init__(self, real_logger):
        self._real = real_logger

    def log_event(self, event_type: str, details: Dict[str, Any]):
        entry = {
            "event":   event_type,
            "ts":      datetime.utcnow().isoformat() + "Z",
            "details": details,
        }
        captured_events.append(entry)
        self._real.log_event(event_type, details)

    def read_recent_logs(self, limit: int = 100):
        return self._real.read_recent_logs(limit)

# Patch BEFORE importing perception so the module-level binding picks up our wrapper.
# We patch both the observability module reference AND directly on the semantic module
# after import to cover the already-bound name inside semantic.py.
import observability.logger as _obs_mod
_real_logger = _obs_mod.structured_logger
_capturing_logger = _CapturingLogger(_real_logger)
_obs_mod.structured_logger = _capturing_logger

# Now import perception — it will call `from observability.logger import structured_logger`
# which may already be cached. We patch the module attribute after to be safe.
from perception.semantic import perception_semantic, SemanticPerception
import perception.semantic as _sem_mod
_sem_mod.structured_logger = _capturing_logger   # Override the bound name inside semantic.py

from config.settings import settings

# ── Colour helpers for terminal output ───────────────────────────────────────
try:
    import colorama; colorama.init()
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"
except ImportError:
    GREEN = YELLOW = RED = CYAN = BOLD = RESET = ""

# ── Helpers ───────────────────────────────────────────────────────────────────

def divider(title: str):
    line = "=" * 70
    print(f"\n{BOLD}{CYAN}{line}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{line}{RESET}")

def section(title: str):
    print(f"\n{BOLD}  >> {title}{RESET}")

def ok(msg: str):  print(f"  {GREEN}[OK]{RESET} {msg}")
def warn(msg: str): print(f"  {YELLOW}[WARN]{RESET} {msg}")
def fail(msg: str): print(f"  {RED}[FAIL]{RESET} {msg}")
def info(msg: str): print(f"    {msg}")

def drain_events() -> List[Dict[str, Any]]:
    """Returns and clears all captured events since last drain."""
    global captured_events
    evts = list(captured_events)
    captured_events.clear()
    return evts

def open_chrome(url: str = "about:newtab"):
    """Launches Chrome and waits for the window to appear."""
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "chrome", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False
        )
        time.sleep(2.0)  # Buffer for OS process spawn
        ok(f"Chrome launch triggered: {url}")
    except Exception as exc:
        fail(f"Chrome launch failed: {exc}")

def close_chrome():
    """Kills all Chrome processes cleanly."""
    try:
        subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
        time.sleep(1.0)
        ok("Chrome closed.")
    except Exception as exc:
        warn(f"Chrome close failed: {exc}")

def trigger_notification():
    """Fires a Windows toast notification via PowerShell to simulate OS interference."""
    try:
        script = textwrap.dedent("""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
                [Windows.UI.Notifications.ToastTemplateType]::ToastText01)
            $template.SelectSingleNode('//text[@id=1]').InnerText = 'JARVIS Test Notification'
            $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('JARVIS').Show($toast)
        """)
        subprocess.Popen(
            ["powershell", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(0.8)
        ok("Windows toast notification triggered.")
    except Exception as exc:
        warn(f"Notification trigger failed (non-critical): {exc}")

def print_perception_cycle(evt: Dict):
    d = evt["details"]
    section("PERCEPTION_CYCLE")
    info(f"Stage:              accessibility")
    info(f"Target hint:        '{d.get('target_window', d.get('target_window_hint', ''))}'")
    info(f"Resolved window:    '{d.get('resolved_window', 'None')}'")
    info(f"Resolution method:  {d.get('resolution_method', 'N/A')}")
    info(f"Elements found:     {d.get('elements_found', 0)}")
    info(f"Fallback triggered: {d.get('fallback_triggered', False)}")
    if d.get('fallback_source'):
        warn(f"Fallback source:    {d.get('fallback_source')}")
    if d.get('ocr_elements_found'):
        warn(f"OCR elements found: {d.get('ocr_elements_found')}")
    if d.get('element_labels'):
        info(f"Element labels:     {d.get('element_labels')}")
    if d.get('error'):
        fail(f"Error:              {d.get('error')}")

def print_element_resolution(evt: Dict):
    d = evt["details"]
    section("ELEMENT_RESOLUTION")
    info(f"Target label:       '{d.get('target_label')}'")
    info(f"Control type hint:  {d.get('control_type_hint', 'Any')}")
    info(f"Window hint:        {d.get('window_title_hint', 'None')}")
    info(f"Candidates found:   {d.get('candidates', 0)}")
    info(f"Resolution path:    {' → '.join(d.get('resolution_path', []))}")
    result = d.get('result', '')
    if 'no_match' in result:
        fail(f"Result:             {result}")
    elif 'resolved' in result:
        ok(f"Result:             {result}")
    else:
        info(f"Result:             {result}")

def print_semantic_complete(evt: Dict):
    d = evt["details"]
    section("SEMANTIC_ANALYSIS_COMPLETE")
    fulfilled = d.get('fulfilled')
    verdict_str = f"{GREEN}✓ FULFILLED{RESET}" if fulfilled else f"{RED}✗ NOT FULFILLED{RESET}"
    info(f"Verdict:            {verdict_str}")
    info(f"State summary:      \"{d.get('llm_verdict')}\"")
    info(f"Cycle time:         {d.get('cycle_ms', '?')}ms")
    info(f"Elements used:      {d.get('elements_used', [])}")

def run_perception_and_report(
    goal: str,
    expected: str,
    window_hint: str = "",
    resolve_label: Optional[str] = None,
    resolve_ctrl_type: Optional[str] = None,
):
    """
    Calls analyze_ui_semantics then optionally resolve_element,
    prints the full trace, and returns the result dict.
    """
    drain_events()  # Clear any stale events

    result = perception_semantic.analyze_ui_semantics(
        goal_context=goal,
        expected_outcome=expected,
        target_window_hint=window_hint,
    )

    events = drain_events()

    # Debug: show ALL captured events regardless of type
    if events:
        print(f"\n  [Raw captured events: {len(events)}]")
        for e in events:
            print(f"    {e['event']}: {json.dumps(e['details'], default=str)[:200]}")
    else:
        print(f"\n  [No events captured from structured_logger — check patch is applied correctly]")
    cycle_evts      = [e for e in events if e["event"] == "PERCEPTION_CYCLE"]
    complete_evts   = [e for e in events if e["event"] == "SEMANTIC_ANALYSIS_COMPLETE"]

    for evt in cycle_evts:
        print_perception_cycle(evt)

    if resolve_label and result.get("elements"):
        resolve_result = perception_semantic.resolve_element(
            elements=result["elements"],
            target_label=resolve_label,
            control_type=resolve_ctrl_type,
            window_title_hint=window_hint or None,
        )
        resolve_evts = drain_events()
        for evt in resolve_evts:
            if evt["event"] == "ELEMENT_RESOLUTION":
                print_element_resolution(evt)

        if resolve_result:
            section("Selected Element Attributes")
            for k, v in resolve_result.items():
                info(f"{k:<20}: {v}")
        else:
            section("Selected Element Attributes")
            fail("No element matched — would trigger fallback to OCR or replan.")
    else:
        if resolve_label:
            section("ELEMENT_RESOLUTION")
            warn("Skipped — accessibility tree returned empty, no elements to resolve against.")

    for evt in complete_evts:
        print_semantic_complete(evt)

    # Ambiguity check: did multiple candidates share same label?
    section("Ambiguity Analysis")
    elements = result.get("elements", [])
    if resolve_label:
        dupes = [e for e in elements if resolve_label.lower() in (e.get("label") or "").lower()]
        if len(dupes) > 1:
            warn(f"{len(dupes)} elements share label '{resolve_label}' — disambiguation required.")
            for d in dupes:
                info(f"  → type={d['control_type']}, parent='{d['parent_title']}', idx={d['index']}, src={d['source']}")
        elif len(dupes) == 1:
            ok(f"Unique match for '{resolve_label}' — no ambiguity.")
        else:
            warn(f"No candidates found for '{resolve_label}' at all.")
    else:
        info("No label resolution requested for this step.")

    return result


# ─── TEST SUITE 1: Deterministic Targeting ────────────────────────────────────

def suite_1_deterministic():
    divider("SUITE 1 — Deterministic Targeting")

    # ── Test 1A: Open Chrome (pre-launch state) ───────────────────────────────
    print(f"\n{BOLD}Test 1A: 'Open Chrome' — before launch{RESET}")
    print("  State: No Chrome window open yet.\n")
    run_perception_and_report(
        goal="open_application: Open Chrome",
        expected="Chrome browser is open and its window is visible on screen",
        window_hint="chrome",
        resolve_label=None,
    )

    # Open Chrome
    print(f"\n  → Launching Chrome...")
    open_chrome()

    # ── Test 1B: Open Chrome (post-launch state) ──────────────────────────────
    print(f"\n{BOLD}Test 1B: 'Open Chrome' — after launch{RESET}")
    print("  State: Chrome should be open on New Tab.\n")
    run_perception_and_report(
        goal="open_application: Open Chrome",
        expected="Chrome browser is open and its window is visible on screen",
        window_hint="chrome",
        resolve_label="Address and search bar",
        resolve_ctrl_type="Edit",
    )

    # ── Test 1C: Search YouTube ───────────────────────────────────────────────
    print(f"\n{BOLD}Test 1C: 'Search YouTube' — type URL in address bar{RESET}")
    print("  State: Chrome open, need to target address bar.\n")

    # Actually navigate to YouTube
    open_chrome("https://www.youtube.com")
    time.sleep(2.0)

    run_perception_and_report(
        goal="Navigate to YouTube.com via the address bar",
        expected="YouTube homepage is loaded and visible in Chrome",
        window_hint="YouTube",
        resolve_label="Search",
        resolve_ctrl_type="Edit",
    )

    # ── Test 1D: Type in search box ───────────────────────────────────────────
    print(f"\n{BOLD}Test 1D: 'Type hello in search box'{RESET}")
    print("  State: YouTube loaded, need to identify the search input.\n")
    run_perception_and_report(
        goal="Type 'hello' into the YouTube search input field",
        expected="YouTube search box is focused and contains text input capability",
        window_hint="YouTube",
        resolve_label="Search",
        resolve_ctrl_type="Edit",
    )


# ─── TEST SUITE 2: Ambiguity Handling ─────────────────────────────────────────

def suite_2_ambiguity():
    divider("SUITE 2 — Ambiguity Handling")

    print(f"\n{BOLD}Test 2A: 'Search something' — ambiguous label on YouTube{RESET}")
    print("  State: YouTube open. Two 'Search' elements expected (address bar + YouTube bar).\n")
    run_perception_and_report(
        goal="Search for something on YouTube",
        expected="YouTube search box is visible and ready for input",
        window_hint="YouTube",
        resolve_label="Search",
        resolve_ctrl_type=None,     # No type hint — should still resolve via parent_title
    )

    print(f"\n{BOLD}Test 2B: 'Click search' — minimal context{RESET}")
    print("  State: YouTube open. Only label='Search', no type/window hints.\n")
    run_perception_and_report(
        goal="Click the search element",
        expected="Search input is active",
        window_hint="",             # No window hint — harder resolution
        resolve_label="Search",
        resolve_ctrl_type=None,
    )


# ─── TEST SUITE 3: OS Interference ────────────────────────────────────────────

def suite_3_os_interference():
    divider("SUITE 3 — OS Interference Test (Notification During Execution)")

    print(f"\n{BOLD}Test 3A: Fire notification THEN call perception{RESET}")
    print("  State: YouTube open. Triggering Windows toast NOW.\n")

    # Fire notification first
    trigger_notification()
    # Perception runs while notification is potentially in foreground
    run_perception_and_report(
        goal="Search YouTube after notification appeared",
        expected="YouTube homepage is visible and search bar is accessible",
        window_hint="YouTube",
        resolve_label="Search",
        resolve_ctrl_type="Edit",
    )
    print()
    print(f"  {BOLD}Key observation:{RESET} Check PERCEPTION_CYCLE log above.")
    print(f"  If resolved_window contains 'YouTube' (not 'DV2ControlHost'), the overlay filter worked.")


# --- MAIN ---------------------------------------------------------------------

def main():
    print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}{CYAN}  JARVIS v6 -- Semantic Perception Layer Validation{RESET}")
    print(f"{BOLD}{CYAN}  Feature flag: USE_SEMANTIC_PERCEPTION = {settings.use_semantic_perception}{RESET}")
    print(f"{BOLD}{CYAN}{'='*70}{RESET}\n")

    if not settings.use_semantic_perception:
        fail("USE_SEMANTIC_PERCEPTION is False. Enable it first.")
        sys.exit(1)

    if NO_LLM:
        warn("--no-llm mode: Stage 3 (GPT-4o) is STUBBED. Testing Stages 1 & 2 only.")
        # Monkeypatch _reason_with_llm to return a deterministic stub
        def _stub_llm(self, ui_payload, img_bytes, goal, expected):
            fulfilled = len(ui_payload.get("elements", [])) > 0
            return {
                "state_summary": f"[STUB] {len(ui_payload.get('elements',[]))} elements found in '{ui_payload.get('focused_window','?')}'.",
                "expected_fulfilled": fulfilled,
                "critical_elements_visible": [e["label"] for e in ui_payload.get("elements", [])[:3]],
            }
        SemanticPerception._reason_with_llm = _stub_llm
        # Also force it enabled since API key check disables the entire pipeline
        perception_semantic.is_enabled = True
    elif not settings.openai_api_key or settings.openai_api_key.startswith("your_"):
        fail("OpenAI API key is missing/placeholder. Set OPENAI_API_KEY in .env or pass --no-llm.")
        sys.exit(1)

    ok(f"Semantic perception ENABLED.")
    ok(f"Other flags: hierarchical={settings.use_hierarchical_planner}, "
       f"simulation={settings.use_simulation}, critic={settings.use_critic_agent}")

    # Cleanup any stale Chrome instances
    close_chrome()
    time.sleep(1.0)

    try:
        suite_1_deterministic()
        suite_2_ambiguity()
        suite_3_os_interference()
    finally:
        # Always close Chrome when done
        print()
        divider("Cleanup")
        close_chrome()

    print(f"\n{BOLD}{GREEN}{'='*70}{RESET}")
    print(f"{BOLD}{GREEN}  Test suite complete.{RESET}")
    print(f"{BOLD}{GREEN}{'='*70}{RESET}\n")


if __name__ == "__main__":
    main()
