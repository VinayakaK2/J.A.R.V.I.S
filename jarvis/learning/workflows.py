"""
learning/workflows.py
──────────────────────
Smart workflow matching interface used by the Planner and Orchestrator.

match_workflow() returns a result bundle instead of a raw workflow dict,
enabling the Orchestrator to implement the 3-mode confidence routing cleanly:
  - High confidence + high semantic similarity -> auto apply
  - Medium -> ask user
  - Low -> fallback to LLM planner
"""

from typing import Dict, Any, Optional
from learning.storage import workflow_store
from learning.intent_normalizer import normalize_intent
from learning.pattern_extractor import HIGH_FAILURE_RATE_THRESHOLD

# ─── Thresholds (used by Orchestrator) ───────────────────────────────────────

# Auto-apply only when both scores exceed these thresholds simultaneously
AUTO_APPLY_CONFIDENCE = 0.85
AUTO_APPLY_SEMANTIC   = 0.90

# Above this threshold but below auto-apply -> ask user for confirmation
ASK_USER_CONFIDENCE   = 0.60


def _compute_semantic_similarity(intent_a: str, intent_b: str) -> float:
    """
    Computes token-overlap-based semantic similarity between two canonical
    intent strings. Returns a float in [0.0, 1.0].

    This is a lightweight alternative to full embedding similarity, keeping
    the system dependency-free while still being effective for canonical slugs.
    Uses Jaccard similarity on word tokens.
    """
    a_tokens = set(intent_a.lower().replace("_", " ").split())
    b_tokens = set(intent_b.lower().replace("_", " ").split())
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    return round(intersection / union, 4) if union else 0.0


def match_workflow(
    intent: str, context: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Finds the best matching learned workflow for a given user intent.

    Returns a result bundle:
    {
        "workflow": <workflow_doc>,
        "confidence": 0.87,
        "semantic_similarity": 0.92,
        "mode": "auto" | "ask" | "fallback"
    }
    or None if no suitable workflow exists.
    """
    canonical = normalize_intent(intent)
    all_workflows = workflow_store.load_all()

    best_match = None
    best_combined = 0.0

    for w in all_workflows:
        # Skip workflows with dangerously high failure rates (Part 4 safety gate)
        if w.get("failure_rate", 0.0) > HIGH_FAILURE_RATE_THRESHOLD:
            continue

        w_canonical = w.get("canonical_intent", "")
        semantic_sim = _compute_semantic_similarity(canonical, w_canonical)

        # Broaden matching via associated_intents aliases
        for alias in w.get("associated_intents", []):
            alias_sim = _compute_semantic_similarity(canonical, alias)
            semantic_sim = max(semantic_sim, alias_sim)

        confidence = w.get("confidence", 0.0)
        # Combined ranking weights confidence and similarity equally
        combined = (confidence * 0.5) + (semantic_sim * 0.5)

        if combined > best_combined:
            best_combined = combined
            best_match = (w, confidence, semantic_sim)

    if not best_match:
        return None

    workflow, confidence, semantic_sim = best_match

    # ── 3-Mode Routing Classification ─────────────────────────────────────────
    if confidence >= AUTO_APPLY_CONFIDENCE and semantic_sim >= AUTO_APPLY_SEMANTIC:
        mode = "auto"
    elif confidence >= ASK_USER_CONFIDENCE:
        mode = "ask"
    else:
        mode = "fallback"

    return {
        "workflow": workflow,
        "confidence": confidence,
        "semantic_similarity": semantic_sim,
        "mode": mode,
    }


def get_all_workflows() -> list:
    """Exposes the raw store for CLI debug/admin tooling."""
    return workflow_store.load_all()
