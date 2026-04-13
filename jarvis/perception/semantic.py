"""
perception/semantic.py — JARVIS v6 Semantic UI Understanding Layer

Three-stage pipeline bound by strict priority order:
  1. PyWinAuto Accessibility Tree  (authoritative, deterministic)
  2. Tesseract OCR Bounding Boxes  (fallback only if tree is empty)
  3. GPT-4o Vision Reasoning       (interpretation only, grounded by 1 or 2)

Key design guarantees:
  - State-based readiness polling (no fixed sleeps)
  - Active window resolved by OS foreground handle (not focus heuristic)
  - Multi-attribute element schema with deterministic disambiguation
  - Full per-cycle debug logging emitted via structured_logger
"""

import time
import json
import base64
import logging
import platform
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
import observability.logger as _obs_logger_mod

def _get_logger():
    """Late-binding accessor — always returns the current structured_logger object.
    This ensures test patches and runtime replacements take effect immediately."""
    return _obs_logger_mod.structured_logger

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

# Windows class names known to be transient overlays (notifications, menus)
# We skip these when resolving the active application window.
TRANSIENT_OVERLAY_CLASSES = {
    "Windows.UI.Core.CoreWindow",   # Action Center, Start overlay
    "Shell_TrayWnd",                # Taskbar
    "NotifyIconOverflowWindow",     # System tray overflow
    "XamlWindow",                   # UWP notification surface
    "tooltips_class32",             # Tooltips
    "DV2ControlHost",               # Windows notification toast
}

# UIA control types we care about — mapped to human-readable labels
MEANINGFUL_CONTROL_TYPES = {
    "Button": "button",
    "Edit": "input",
    "ComboBox": "dropdown",
    "CheckBox": "checkbox",
    "Hyperlink": "link",
    "ListItem": "list_item",
    "MenuItem": "menu_item",
    "TabItem": "tab",
    "Document": "document",
}

# Exponential backoff base delay (seconds)
BACKOFF_BASE = 0.25


# ─── State-Based Readiness Wait ───────────────────────────────────────────────

def wait_for_ui_ready(
    condition_fn,
    timeout: float = 5.0,
    label: str = "ui_ready",
) -> Tuple[bool, Any]:
    """
    Polls condition_fn() with exponential backoff until it returns a truthy result
    or the timeout expires. Returns (success: bool, last_result: Any).

    condition_fn must return a falsy value (empty list/dict/None) to signal "not ready".
    Exponential backoff: 0.25s, 0.5s, 1.0s, 2.0s, capped at 2.0s.
    """
    deadline = time.monotonic() + timeout
    attempt = 0
    last_result = None

    while time.monotonic() < deadline:
        attempt += 1
        try:
            result = condition_fn()
            if result:
                logger.debug(f"[Semantic][{label}] Ready after {attempt} attempt(s).")
                return True, result
        except Exception as exc:
            logger.debug(f"[Semantic][{label}] Attempt {attempt} raised: {exc}")

        delay = min(BACKOFF_BASE * (2 ** (attempt - 1)), 2.0)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(delay, remaining))

    logger.warning(f"[Semantic][{label}] Timed out after {timeout}s ({attempt} attempts).")
    return False, last_result


# ─── Active Window Resolution ─────────────────────────────────────────────────

def _get_foreground_window_by_handle() -> Optional[object]:
    """
    Resolves the true foreground application window using the OS handle,
    ignoring transient overlays (notifications, tooltips, taskbar).
    Returns a pywinauto WindowSpecification or None.
    """
    try:
        import ctypes
        from pywinauto import Desktop

        # GetForegroundWindow via ctypes is the authoritative OS call
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None

        # Resolve class name to filter out transient surfaces
        class_buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
        win_class = class_buf.value

        if win_class in TRANSIENT_OVERLAY_CLASSES:
            logger.info(
                f"[Semantic] Foreground hwnd={hwnd} class='{win_class}' is a transient overlay. Scanning downward."
            )
            # Walk the Z-order to find the next non-overlay window
            desktop = Desktop(backend="uia")
            for win in desktop.windows():
                try:
                    wc = win.element_info.class_name
                    if wc not in TRANSIENT_OVERLAY_CLASSES and win.is_visible():
                        logger.info(f"[Semantic] Resolved to: '{win.window_text()}' ({wc})")
                        return win
                except Exception:
                    continue
            return None

        # Bind pywinauto to the resolved handle directly
        desktop = Desktop(backend="uia")
        for win in desktop.windows():
            try:
                if win.element_info.handle == hwnd:
                    return win
            except Exception:
                continue

        # Handle mismatch fallback — return by class/title if handle lookup missed
        logger.debug(f"[Semantic] Handle {hwnd} not matched in window list; returning None.")
        return None

    except Exception as exc:
        logger.error(f"[Semantic] Foreground window resolution failed: {exc}")
        return None


def _find_window_by_hint(hint: str) -> Optional[object]:
    """
    Scans ALL visible desktop windows and returns the best match for a given hint.

    Matching: title substring OR UIA class name substring (case-insensitive).
    When multiple windows match (e.g. two Chrome tabs), the one with the most
    interactive UIA descendants wins — this prevents a minimal browser tab
    (3 window-control buttons) from winning over the rich target app window.

    Falls back to the OS foreground window when no hint is given.
    """
    if not hint:
        return _get_foreground_window_by_handle()
    try:
        from pywinauto import Desktop
        desktop = Desktop(backend="uia")
        hint_lower = hint.lower()
        candidates = []

        for win in desktop.windows():
            try:
                if not win.is_visible():
                    continue
                title = (win.window_text() or "").lower()
                cls   = (win.element_info.class_name or "").lower()
                if hint_lower in title or hint_lower in cls:
                    candidates.append(win)
            except Exception:
                continue

        if not candidates:
            logger.warning(f"[Semantic] No window found matching hint='{hint}'")
            return None

        if len(candidates) == 1:
            logger.debug(f"[Semantic] Unique match for hint='{hint}': '{candidates[0].window_text()}'")
            return candidates[0]

        # Multiple Chrome windows — score by number of meaningful interactive controls
        # +1000 bonus if the hint is actually in the title (to prefer actual Chrome over VS Code)
        def _score_window(win) -> int:
            score = 0
            title = (win.window_text() or "").lower()
            if hint_lower in title:
                score += 1000
                
            for ct in ("Button", "Edit", "ComboBox", "Hyperlink", "ListItem"):
                try:
                    score += len(win.descendants(control_type=ct))
                except Exception:
                    pass
            return score

        scored = sorted(candidates, key=_score_window, reverse=True)
        best = scored[0]
        logger.info(
            f"[Semantic] hint='{hint}' matched {len(candidates)} windows. "
            f"Selected richest: '{best.window_text()}'"
        )
        return best

    except Exception as exc:
        logger.error(f"[Semantic] Window hint scan failed: {exc}")
        return None


# ─── Multi-Attribute Element Extraction ───────────────────────────────────────

def _extract_element_rich(ctrl, window_title: str, index: int) -> Optional[Dict[str, Any]]:
    """
    Builds a rich, multi-attribute element dictionary from a UIA control.
    Returns None if the control is stale or irrelevant.

    Attribute priority for downstream disambiguation matching:
      1. window_title
      2. control_type
      3. label
      4. parent_title
      5. index (fallback)
    """
    try:
        friendly = ctrl.friendly_class_name()
        # Only emit control types we know are meaningful
        raw_type = next(
            (k for k in MEANINGFUL_CONTROL_TYPES if k in friendly),
            None
        )
        if raw_type is None:
            return None

        rect = ctrl.rectangle()
        label = (ctrl.window_text() or "").strip()

        # Resolve immediate parent label for hierarchy context
        parent_title = ""
        try:
            parent = ctrl.parent()
            parent_title = (parent.window_text() or "").strip()
        except Exception:
            pass

        return {
            "type":         MEANINGFUL_CONTROL_TYPES[raw_type],
            "label":        label,
            "window_title": window_title,
            "control_type": raw_type,           # Raw UIA class (Button/Edit/etc.)
            "parent_title": parent_title,        # Immediate parent window text
            "index":        index,               # Positional fallback for disambiguation
            "clickable":    ctrl.is_enabled(),
            "position":     [rect.left, rect.top],
            "size":         [rect.width(), rect.height()],
            "source":       "accessibility",
        }
    except Exception:
        return None


# ─── Accessibility Tree Extraction ────────────────────────────────────────────

def _extract_tree_from_window(win) -> List[Dict[str, Any]]:
    """
    Extracts enriched interactive elements from a specific window object.
    Limits to 40 most relevant controls to stay within LLM context bounds.
    """
    elements: List[Dict[str, Any]] = []
    window_title = ""
    try:
        window_title = win.window_text() or ""

        # Collect relevant UIA control types
        controls = []
        for ct in MEANINGFUL_CONTROL_TYPES:
            try:
                controls += win.descendants(control_type=ct)
            except Exception:
                pass

        # Deduplicate by handle to avoid UIA tree duplicates
        seen_handles = set()
        for idx, ctrl in enumerate(controls):
            try:
                handle = ctrl.element_info.handle
                if handle and handle in seen_handles:
                    continue
                seen_handles.add(handle)
            except Exception:
                pass

            elem = _extract_element_rich(ctrl, window_title, idx)
            if elem:
                elements.append(elem)
            if len(elements) >= 40:
                break

    except Exception as exc:
        logger.warning(f"[Semantic] Element extraction failed for '{window_title}': {exc}")

    return elements


# ─── Main Semantic Perception Class ───────────────────────────────────────────

class SemanticPerception:
    """
    Orchestrates the three-stage semantic perception pipeline.
    All public methods emit structured debug logs consumed by /analysis/{task_id}.
    """

    def __init__(self):
        self.is_windows = platform.system() == "Windows"
        self.is_enabled = bool(
            settings.openai_api_key and not settings.openai_api_key.startswith("your_")
        )

    # ── Stage 1: Accessibility Tree ───────────────────────────────────────────

    def extract_accessibility_tree(
        self, target_window_hint: str = ""
    ) -> Dict[str, Any]:
        """
        Resolves the active foreground window (ignoring overlays),
        waits for its UIA tree to become queryable, then extracts enriched elements.

        Args:
            target_window_hint: Optional substring to validate the resolved window title.
        """
        cycle_log: Dict[str, Any] = {
            "stage": "accessibility",
            "target_window_hint": target_window_hint,
            "resolved_window": None,
            "resolution_method": None,
            "elements_found": 0,
            "fallback_triggered": False,
        }

        if not self.is_windows:
            cycle_log["error"] = "Non-Windows platform"
            _get_logger().log_event("PERCEPTION_CYCLE", cycle_log)
            return {"elements": [], "apps_open": [], "focused_window": "", "_log": cycle_log}

        # Step A: Resolve target window by hint (title OR class) with state-based polling
        def _resolve_target_window():
            win = _find_window_by_hint(target_window_hint)
            if win is None:
                return None
            return win

        win_ready, target_win = wait_for_ui_ready(
            _resolve_target_window,
            timeout=6.0,
            label=f"target_window(hint='{target_window_hint}')",
        )

        if not win_ready or target_win is None:
            cycle_log["error"] = f"Could not resolve foreground window matching hint='{target_window_hint}'"
            cycle_log["fallback_triggered"] = True
            _get_logger().log_event("PERCEPTION_CYCLE", cycle_log)
            return {"elements": [], "apps_open": [], "focused_window": "", "_log": cycle_log}

        resolved_title = target_win.window_text() or ""
        cycle_log["resolved_window"] = resolved_title
        cycle_log["resolution_method"] = "os_foreground_handle"

        # Step B: Wait for UIA tree to be queryable (catches launch race condition)
        def _has_elements():
            elems = _extract_tree_from_window(target_win)
            return elems if len(elems) > 0 else None

        tree_ready, elements = wait_for_ui_ready(
            _has_elements,
            timeout=5.0,
            label=f"uia_tree('{resolved_title}')",
        )

        if not tree_ready or not elements:
            cycle_log["error"] = "UIA tree empty after wait — using OCR fallback path"
            cycle_log["fallback_triggered"] = True
            cycle_log["elements_found"] = 0
            _get_logger().log_event("PERCEPTION_CYCLE", cycle_log)
            return {"elements": [], "apps_open": [resolved_title], "focused_window": resolved_title, "_log": cycle_log}

        # Step C: Enumerate all visible app names for 'apps_open' field
        apps_open: List[str] = []
        try:
            from pywinauto import Desktop
            for win in Desktop(backend="uia").windows():
                try:
                    if win.is_visible() and win.window_text():
                        apps_open.append(win.window_text())
                except Exception:
                    pass
        except Exception:
            pass

        cycle_log["elements_found"] = len(elements)
        cycle_log["element_labels"] = [e["label"] for e in elements]
        _get_logger().log_event("PERCEPTION_CYCLE", cycle_log)

        logger.info(
            f"[Semantic] Accessibility tree: window='{resolved_title}', "
            f"{len(elements)} elements extracted."
        )

        return {
            "elements":       elements,
            "apps_open":      apps_open,
            "focused_window": resolved_title,
            "_log":           cycle_log,
        }

    # ── Deterministic Element Resolver ────────────────────────────────────────

    def resolve_element(
        self,
        elements: List[Dict[str, Any]],
        target_label: str,
        control_type: Optional[str] = None,
        window_title_hint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Deterministically selects a single element from the extracted list.

        Priority chain:
          1. window_title match (exact substring, case-insensitive)
          2. control_type match
          3. label match
          4. parent_title context
          5. index (lowest index wins as final tiebreaker)

        Returns the best match, or None if no candidates found.
        """
        candidates = [e for e in elements if target_label.lower() in (e["label"] or "").lower()]

        resolution_log = {
            "target_label":      target_label,
            "control_type_hint": control_type,
            "window_title_hint": window_title_hint,
            "candidates":        len(candidates),
            "resolution_path":   [],
        }

        if not candidates:
            resolution_log["result"] = "no_match"
            _get_logger().log_event("ELEMENT_RESOLUTION", resolution_log)
            return None

        if len(candidates) == 1:
            resolution_log["result"] = "unique_match"
            resolution_log["resolution_path"] = ["label_unique"]
            _get_logger().log_event("ELEMENT_RESOLUTION", resolution_log)
            return candidates[0]

        # Multiple candidates — apply priority chain
        filtered = candidates

        # Priority 1: window_title
        if window_title_hint:
            p1 = [e for e in filtered if window_title_hint.lower() in (e["window_title"] or "").lower()]
            if p1:
                filtered = p1
                resolution_log["resolution_path"].append("window_title")

        # Priority 2: control_type
        if control_type and len(filtered) > 1:
            p2 = [e for e in filtered if e.get("control_type", "").lower() == control_type.lower()]
            if p2:
                filtered = p2
                resolution_log["resolution_path"].append("control_type")

        # Priority 3: parent_title — exclude elements whose parent implies a different context
        if len(filtered) > 1:
            p3 = [e for e in filtered if (e.get("parent_title") or "").lower() != ""]
            if p3:
                filtered = p3
                resolution_log["resolution_path"].append("parent_title_ctx")

        # Priority 5: index tiebreaker — lowest index = topmost in UIA tree = most likely target
        filtered.sort(key=lambda e: e.get("index", 999))
        result = filtered[0]

        resolution_log["resolution_path"].append("index_tiebreak")
        resolution_log["result"] = f"resolved: label='{result['label']}' type='{result['control_type']}' idx={result['index']}"
        _get_logger().log_event("ELEMENT_RESOLUTION", resolution_log)

        logger.info(f"[Semantic] Resolved '{target_label}' → {resolution_log['result']}")
        return result

    # ── Stage 2: OCR Fallback ─────────────────────────────────────────────────

    def _fallback_to_ocr(self, img_bytes: bytes, cycle_log: Dict) -> List[Dict[str, Any]]:
        """
        Extracts UI elements via Tesseract OCR when the accessibility tree is empty.
        Marks all elements with source='ocr' so downstream logic can deprioritize them.
        """
        logger.info("[Semantic] Accessibility tree empty — activating OCR fallback.")
        cycle_log["fallback_triggered"] = True
        cycle_log["fallback_source"] = "ocr"

        from perception.hybrid import perception_hybrid
        ocr_data = perception_hybrid.extract_ocr(img_bytes)

        elements = []
        for idx, node in enumerate(ocr_data.get("texts", [])[:30]):
            text = (node.get("text") or "").strip()
            if not text:
                continue
            elements.append({
                "type":         "text",
                "label":        text,
                "window_title": "",
                "control_type": "Unknown",
                "parent_title": "",
                "index":        idx,
                "clickable":    True,           # OCR cannot verify — assume clickable
                "position":     [node.get("x", 0), node.get("y", 0)],
                "size":         [node.get("w", 0), node.get("h", 0)],
                "source":       "ocr",
            })

        cycle_log["ocr_elements_found"] = len(elements)
        return elements

    # ── Stage 3: LLM Reasoning ────────────────────────────────────────────────

    def _reason_with_llm(
        self,
        ui_payload: Dict[str, Any],
        img_bytes: bytes,
        goal_context: str,
        expected_outcome: str,
    ) -> Dict[str, Any]:
        """
        Sends the structured UI topology + screenshot to GPT-4o for semantic interpretation.
        LLM is explicitly prohibited from hallucinating beyond the provided element list.
        """
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)

            base64_image = base64.b64encode(img_bytes).decode("utf-8")

            prompt = f"""You are the Semantic Reasoning Layer for JARVIS v6.

Goal Context: "{goal_context}"
Expected Outcome Check: "{expected_outcome}"

Raw UI Topology — do NOT hallucinate ANY elements not listed here:
{json.dumps(ui_payload, indent=2)}

Reasoning rules:
- Prefer elements with source="accessibility" over source="ocr".
- An expected_outcome is met ONLY if an element directly confirms it is visible.
- Be conservative: if uncertain, set expected_fulfilled=false.

Reply strictly as JSON (no markdown fences):
{{
  "state_summary": "<1 sentence describing what is currently on screen>",
  "expected_fulfilled": true or false,
  "critical_elements_visible": ["<label>", "..."]
}}"""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text",      "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                    ],
                }],
                max_tokens=300,
                temperature=0.0,
            )

            raw = response.choices[0].message.content.strip()
            # Strip any accidental markdown code fences
            for fence in ("```json", "```"):
                if raw.startswith(fence):
                    raw = raw.split(fence, 1)[1]
                    if "```" in raw:
                        raw = raw.split("```")[0]
                    break

            return json.loads(raw.strip())

        except Exception as exc:
            logger.error(f"[Semantic] LLM reasoning failed: {exc}")
            return {
                "state_summary": f"LLM failed: {exc}",
                "expected_fulfilled": False,
                "critical_elements_visible": [],
            }

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze_ui_semantics(
        self,
        goal_context: str = "",
        expected_outcome: str = "",
        target_window_hint: str = "",
    ) -> Dict[str, Any]:
        """
        Main entry point. Runs all three stages and returns a merged semantic state.

        Args:
            goal_context:        What action was just attempted (for LLM grounding).
            expected_outcome:    What state proves the step succeeded.
            target_window_hint:  Optional window title substring to target (e.g. "Chrome").
        """
        if not self.is_enabled:
            return {"state_summary": "Vision disabled (no API key)", "expected_fulfilled": True}

        cycle_start = time.monotonic()
        master_log: Dict[str, Any] = {
            "goal_context":      goal_context,
            "expected_outcome":  expected_outcome,
            "target_window":     target_window_hint,
        }

        # ── Stage 1: Accessibility
        acc_data = self.extract_accessibility_tree(target_window_hint=target_window_hint)
        elements  = acc_data.get("elements", [])
        master_log.update(acc_data.get("_log", {}))

        # ── Screenshot (needed for both Stage 2 and Stage 3)
        from perception.hybrid import perception_hybrid
        img_bytes = perception_hybrid.capture_screenshot()

        # ── Stage 2: OCR Fallback (only if accessibility returned nothing)
        if not elements:
            elements = self._fallback_to_ocr(img_bytes, master_log)

        # ── Stage 3: LLM Reasoning
        ui_payload = {
            "elements":       elements,
            "apps_open":      acc_data.get("apps_open", []),
            "focused_window": acc_data.get("focused_window", ""),
        }

        llm_result = self._reason_with_llm(ui_payload, img_bytes, goal_context, expected_outcome)

        # ── Merge and emit final cycle log
        master_log["llm_verdict"]   = llm_result.get("state_summary")
        master_log["fulfilled"]     = llm_result.get("expected_fulfilled")
        master_log["cycle_ms"]      = round((time.monotonic() - cycle_start) * 1000)
        master_log["elements_used"] = [e["label"] for e in elements[:10]]

        _get_logger().log_event("SEMANTIC_ANALYSIS_COMPLETE", master_log)

        logger.info(
            f"[Semantic] Cycle complete in {master_log['cycle_ms']}ms. "
            f"fulfilled={llm_result.get('expected_fulfilled')} | "
            f"{llm_result.get('state_summary')}"
        )

        return {
            **llm_result,
            "elements":      elements,
            "focused_window": acc_data.get("focused_window", ""),
            "_perception_log": master_log,
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

perception_semantic = SemanticPerception()
