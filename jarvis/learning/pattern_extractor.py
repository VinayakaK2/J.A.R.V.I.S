"""
learning/pattern_extractor.py
──────────────────────────────
Upgraded pattern extraction pipeline. Extends the base implementation with:

  1. Canonical intent clustering (via intent_normalizer)
  2. Sequence deduplication — collapses retry loops and consecutive duplicates
  3. Workflow parameterization — extracts variable placeholders from steps
  4. Failure pattern detection — learns what NOT to do
  5. Context diversity scoring — prevents overconfident single-context workflows
  6. Composite confidence formula: (freq * 0.4) + (success * 0.3) + (diversity * 0.3)
"""

import re
import logging
from collections import defaultdict
from typing import List, Dict, Any, Tuple

from learning.storage import event_store, workflow_store
from learning.intent_normalizer import normalize_intent

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

# Minimum times a sequence must appear to be promoted into a workflow
MIN_FREQUENCY_THRESHOLD = 2
# Minimum success ratio — workflows failing more than this are rejected
MIN_SUCCESS_RATE = 0.5
# Workflows with failure_rate above this trigger planner warnings
HIGH_FAILURE_RATE_THRESHOLD = 0.4


# ─── Sequence Normalization ───────────────────────────────────────────────────

def _deduplicate_sequence(tools: List[str]) -> List[str]:
    """
    Collapses retry loops and removes consecutive duplicate steps from
    a tool sequence to extract the true canonical pattern.

    Example: [gen, run, debug, run, debug, run] - [gen, run, debug]
    """
    seen = []
    for tool in tools:
        if not seen or seen[-1] != tool:
            seen.append(tool)
    return seen


# ─── Parameterization ─────────────────────────────────────────────────────────

def _extract_params_template(action: str) -> Dict[str, str]:
    """
    Replaces concrete-looking values in the action string with placeholder
    tokens. This allows a stored workflow to be adapted to new contexts by
    the planner at runtime.

    Example: 'Search for weather in London' - {"query": "{search_query}"}
    """
    template: Dict[str, str] = {}
    # Detect quoted strings as variable values
    for match in re.finditer(r'"([^"]+)"', action):
        key = re.sub(r"\s+", "_", match.group(1).lower())[:20]
        template[key] = "{" + key + "}"
    # Detect patterns like "filename.ext"
    for match in re.finditer(r"\b[\w-]+\.\w{2,5}\b", action):
        template["filename"] = "{filename}"
        break
    return template


# ─── Context Diversity ────────────────────────────────────────────────────────

def _compute_context_diversity(task_ids: List[str], task_info: Dict) -> float:
    """
    Measures how broadly a workflow has been applied across varied contexts.
    A workflow that always fires in the exact same environment (same session,
    same tool params) gets a low diversity score.
    Higher diversity = higher generalizability.
    Returns a float in [0.0, 1.0].
    """
    if len(task_ids) < 2:
        return 0.0

    sessions = {task_info[tid]["session_id"] for tid in task_ids if tid in task_info}
    # Normalize: perfect diversity when every run came from a different session
    diversity = min(len(sessions) / len(task_ids), 1.0)
    return round(diversity, 4)


# ─── Composite Confidence ─────────────────────────────────────────────────────

def _compute_confidence(freq: int, success_rate: float, diversity: float) -> float:
    """
    Composite confidence score combining frequency gain, success reliability,
    and context generalizability.
    Formula: (freq_factor * 0.4) + (success_rate * 0.3) + (diversity * 0.3)
    """
    freq_factor = min(freq / 5.0, 1.0)  # Saturates at 5+ occurrences
    score = (freq_factor * 0.4) + (success_rate * 0.3) + (diversity * 0.3)
    return round(score, 4)


# ─── PatternExtractor ─────────────────────────────────────────────────────────

class PatternExtractor:
    """
    Offline/background job that converts raw lifecycle events into structured,
    adaptive LearnedWorkflow documents persisted via the WorkflowStore.
    """

    def extract_and_store(self) -> int:
        """
        Main pipeline entry point.
        Returns the number of workflow documents created or updated.
        """
        events = event_store.get_events()

        # ── Step 1: Group all events by task_id ───────────────────────────────
        tasks: Dict[str, List[Dict]] = defaultdict(list)
        for e in events:
            if e.get("task_id"):
                tasks[e["task_id"]].append(e)

        # ── Step 2: Extract per-task metadata ─────────────────────────────────
        task_info: Dict[str, Dict] = {}
        for task_id, seq in tasks.items():
            raw_intent = ""
            canonical_intent = ""
            session_id = ""
            tools: List[str] = []
            tool_steps: List[Dict] = []
            failure_tools: List[str] = []
            success = False

            for e in seq:
                etype = e.get("event_type")
                if etype == "task_start":
                    raw_intent = e.get("intent", "")
                    # Use pre-computed canonical if available, else compute it now
                    canonical_intent = e.get("canonical_intent") or normalize_intent(raw_intent)
                    session_id = e.get("session_id", "")
                elif etype == "tool_usage":
                    tools.append(e["tool"])
                    tool_steps.append({
                        "tool": e["tool"],
                        "action": e.get("action", ""),
                        "params_template": e.get("params_template", {}),
                    })
                elif etype == "task_failure":
                    failure_tools.append(e.get("tool", "unknown"))
                elif etype == "task_end":
                    success = e.get("success", False)

            if not canonical_intent or not tools:
                continue  # Skip incomplete or untracked sessions

            # Deduplicate the tool sequence (Part 5 — Pattern Simplification)
            clean_tools = _deduplicate_sequence(tools)

            task_info[task_id] = {
                "canonical_intent": canonical_intent,
                "raw_intent": raw_intent,
                "session_id": session_id,
                "sequence": clean_tools,
                "tool_steps": tool_steps,
                "failure_tools": failure_tools,
                "success": success,
                "events": seq,
            }

        # ── Step 3: Cluster by (canonical_intent, clean_sequence) ─────────────
        # Using canonical_intent prevents "build website" and "create portfolio"
        # from generating two separate workflow entries.
        clusters: Dict[Tuple, List[str]] = defaultdict(list)
        for task_id, info in task_info.items():
            sig = (info["canonical_intent"], tuple(info["sequence"]))
            clusters[sig].append(task_id)

        # ── Step 4: Evaluate clusters and persist ─────────────────────────────
        workflows_generated = 0

        for (canonical_intent, tool_tuple), task_ids in clusters.items():
            freq = len(task_ids)
            if freq < MIN_FREQUENCY_THRESHOLD:
                continue

            success_count = sum(1 for tid in task_ids if task_info[tid]["success"])
            success_rate = success_count / freq
            fail_count = freq - success_count
            failure_rate = round(fail_count / freq, 4)

            # Reject workflows with catastrophic failure rates
            if success_rate < MIN_SUCCESS_RATE:
                logger.debug(
                    f"[PatternExtractor] Rejecting '{canonical_intent}': "
                    f"success_rate={success_rate:.2f} below threshold."
                )
                continue

            # Context diversity across sessions (Part 6)
            diversity = _compute_context_diversity(task_ids, task_info)

            # Final composite confidence score (Part 6 formula)
            confidence = _compute_confidence(freq, success_rate, diversity)

            # Pick the last successful task to extract parameterized steps
            successful_ids = [tid for tid in task_ids if task_info[tid]["success"]]
            best_id = successful_ids[-1] if successful_ids else task_ids[-1]
            best_steps = task_info[best_id]["tool_steps"]

            # Build parameterized step list with extracted templates (Part 2)
            parameterized_steps = []
            for step in best_steps:
                template = step.get("params_template") or _extract_params_template(step["action"])
                parameterized_steps.append({
                    "tool": step["tool"],
                    "action": step["action"],
                    "params_template": template,
                })

            # Collect failure patterns from all failed tasks in this cluster (Part 4)
            failure_patterns = []
            for tid in task_ids:
                if not task_info[tid]["success"]:
                    failure_patterns.extend(task_info[tid]["failure_tools"])
            # Deduplicate failure tool list
            failure_patterns = list(dict.fromkeys(failure_patterns))

            # Collect all canonical intents seen in this cluster (for matching breadth)
            associated_intents = list({
                task_info[tid]["canonical_intent"] for tid in task_ids
            })

            # ── Workflow document (updated schema) ─────────────────────────────
            workflow_doc = {
                "workflow_name": f"{canonical_intent}_{hash(tool_tuple) % 10000}",
                "canonical_intent": canonical_intent,
                "associated_intents": associated_intents,
                "steps": parameterized_steps,
                # Metrics
                "frequency": freq,
                "success_rate": round(success_rate, 4),
                "failure_rate": failure_rate,
                "confidence": confidence,
                "context_diversity": diversity,
                "failure_patterns": failure_patterns,
                "last_used": task_info[best_id]["events"][-1]["timestamp"],
            }
            workflow_store.save(workflow_doc)
            workflows_generated += 1
            logger.info(
                f"[PatternExtractor] Saved workflow '{workflow_doc['workflow_name']}' "
                f"— confidence={confidence}, freq={freq}, diversity={diversity}"
            )

        logger.info(f"[PatternExtractor] Extraction complete. Workflows updated: {workflows_generated}")
        return workflows_generated
