"""
safety/guard.py
───────────────
Validates every tool call before the executor runs it.
Enforces permission levels, path sandboxing, parameter sanitization,
and — new in v7 — role-based access control (owner vs. guest).

Permission levels:
  AUTO_ALLOW    → execute immediately
  ASK_USER      → owner must pass confirmed=True
  BLOCK         → reject unconditionally

Role-based rules (new):
  guest         → may only call GUEST_SAFE_TOOLS; everything else is blocked
  owner         → full permission table applies
"""

import re
import logging
from enum import Enum
from pathlib import Path
from typing import Dict, Tuple

from config.settings import settings

logger = logging.getLogger(__name__)

# ─── Permission levels ────────────────────────────────────────────────────────

class PermissionLevel(Enum):
    AUTO_ALLOW = 1   # Execute immediately
    ASK_USER   = 2   # Require an explicit user confirmation flag
    BLOCK      = 3   # Reject unconditionally


# ─── Tools a guest is allowed to call regardless of the permission table ──────
# Only safe, read-only, non-destructive tools are accessible to anonymous callers.
GUEST_SAFE_TOOLS: frozenset = frozenset({
    "search_web",
    "general_query",
})


# ─── Safety Guard ─────────────────────────────────────────────────────────────

class SafetyGuard:
    """
    Central safety gate between the orchestrator and the tool executor.
    Every proposed tool call passes through evaluate_action() before it runs.
    """

    # Per-tool baseline permission level (owner-only context)
    _PERMISSIONS: Dict[str, PermissionLevel] = {
        # File tools
        "read_file":            PermissionLevel.AUTO_ALLOW,
        "create_file":          PermissionLevel.AUTO_ALLOW,
        # Information / search
        "search_web":           PermissionLevel.AUTO_ALLOW,
        "run_code_sandbox":     PermissionLevel.AUTO_ALLOW,
        # External messaging — require confirmation from owner
        "send_whatsapp":        PermissionLevel.ASK_USER,
        "send_telegram":        PermissionLevel.ASK_USER,

        # ── Desktop Agent (Level 1) — auto-allow for owner ────────────────
        "open_application":     PermissionLevel.AUTO_ALLOW,
        "open_website":         PermissionLevel.AUTO_ALLOW,
        "open_url":             PermissionLevel.AUTO_ALLOW,
        "search_google":        PermissionLevel.AUTO_ALLOW,
        "click_selector":       PermissionLevel.AUTO_ALLOW,
        "fill_input":           PermissionLevel.AUTO_ALLOW,
        "click_native_element": PermissionLevel.AUTO_ALLOW,
        "fill_native_input":    PermissionLevel.AUTO_ALLOW,

        # ── Desktop Agent (Level 2) — explicit confirmation required ─────
        "type_text":            PermissionLevel.ASK_USER,
        "click":                PermissionLevel.ASK_USER,
        "press_keys":           PermissionLevel.ASK_USER,

        # ── Destructive / system-level — permanently blocked ─────────────
        "delete_file":              PermissionLevel.BLOCK,
        "execute_system_command":   PermissionLevel.BLOCK,
        "rm":                       PermissionLevel.BLOCK,
        "rmdir":                    PermissionLevel.BLOCK,
    }

    # Characters that indicate injection attempts in parameter values
    _DANGEROUS_PATTERNS = re.compile(
        r"(;|\||\&\&|`|\$\(|<script|DROP TABLE|INSERT INTO|--|\.\.\/)",
        re.IGNORECASE,
    )

    def __init__(self):
        # Resolve workspace path once at construction time
        self.workspace = Path(settings.workspace_dir).resolve()

    # ── Parameter sanitisation ────────────────────────────────────────────────

    def _sanitize_params(self, tool_name: str, params: dict) -> Tuple[bool, str]:
        """Scan every string param for injection / path-traversal patterns."""
        for key, value in params.items():
            if not isinstance(value, str):
                continue
            if self._DANGEROUS_PATTERNS.search(value):
                return False, f"Param '{key}' contains a potentially dangerous pattern."
        return True, ""

    # ── Path sandbox enforcement ──────────────────────────────────────────────

    def _validate_file_path(self, tool_name: str, params: dict) -> Tuple[bool, str]:
        """Ensure file operation paths resolve inside the workspace sandbox."""
        for key in ("name", "path"):
            raw = params.get(key)
            if not raw or not isinstance(raw, str):
                continue
            # Strip any directory components before resolving
            safe_name = Path(raw).name
            resolved = (self.workspace / safe_name).resolve()
            if not str(resolved).startswith(str(self.workspace)):
                return False, (
                    f"Path '{raw}' in param '{key}' escapes the workspace sandbox."
                )
        return True, ""

    # ── Role-based access control (new in v7) ────────────────────────────────

    def _check_role_permission(self, tool_name: str, role: str) -> Tuple[bool, str]:
        """
        Guests may only call tools explicitly listed in GUEST_SAFE_TOOLS.
        All other callers (owners) bypass this check and fall through to the
        standard permission table.
        """
        if role == "guest" and tool_name not in GUEST_SAFE_TOOLS:
            reason = (
                f"I don't have permission for that action. "
                f"Tool '{tool_name}' requires owner-level access."
            )
            return False, reason
        return True, ""

    # ── Main evaluation entry point ───────────────────────────────────────────

    def evaluate_action(
        self,
        tool_name: str,
        params: dict,
        role: str = "guest",
    ) -> Tuple[PermissionLevel, str]:
        """
        Evaluate a proposed tool call and return (PermissionLevel, reason).

        Steps:
          1. Role check — guests are blocked from non-safe tools immediately.
          2. Baseline permission table lookup.
          3. Parameter sanitisation.
          4. File path sandbox check (for file tools).
          5. ASK_USER flow — auto-approve if confirmed=True.
        """

        # ── Step 1: Role-based gate ────────────────────────────────────────
        role_ok, role_reason = self._check_role_permission(tool_name, role)
        if not role_ok:
            logger.warning(f"[Guard] ROLE BLOCKED ({role}): {role_reason}")
            return PermissionLevel.BLOCK, role_reason

        # ── Step 2: Permission table lookup ───────────────────────────────
        # Default to BLOCK for any unregistered tool — fail-safe design
        level = self._PERMISSIONS.get(tool_name, PermissionLevel.BLOCK)

        if level == PermissionLevel.BLOCK:
            reason = f"Tool '{tool_name}' is permanently blocked by safety policy."
            logger.warning(f"[Guard] BLOCKED: {reason}")
            return level, reason

        # ── Step 3: Parameter sanitisation ────────────────────────────────
        safe, reason = self._sanitize_params(tool_name, params)
        if not safe:
            logger.warning(f"[Guard] BLOCKED (sanitization): {reason}")
            return PermissionLevel.BLOCK, reason

        # ── Step 4: File path sandbox check ───────────────────────────────
        if tool_name in ("create_file", "read_file"):
            safe, reason = self._validate_file_path(tool_name, params)
            if not safe:
                logger.warning(f"[Guard] BLOCKED (path traversal): {reason}")
                return PermissionLevel.BLOCK, reason

        # ── Step 5: ASK_USER — honour explicit confirmation ────────────────
        if level == PermissionLevel.ASK_USER:
            if params.get("confirmed") is True:
                logger.info(f"[Guard] APPROVED (confirmed=true): {tool_name}")
                return PermissionLevel.AUTO_ALLOW, f"Tool '{tool_name}' approved by user."
            reason = (
                f"Tool '{tool_name}' sends external messages and requires owner approval. "
                "Pass confirmed=true to proceed."
            )
            logger.info(f"[Guard] ASK_USER: {reason}")
            return level, reason

        # ── All checks passed — allow execution ────────────────────────────
        return PermissionLevel.AUTO_ALLOW, f"Tool '{tool_name}' is safe to execute."
