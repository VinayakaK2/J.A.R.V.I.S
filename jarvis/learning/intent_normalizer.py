"""
learning/intent_normalizer.py
──────────────────────────────
Converts raw, inconsistent user intents into stable canonical intent identifiers.

Strategy:
- Primary: TF-IDF keyword scoring against known canonical intent clusters.
- Fallback: LLM classification (only when an OpenAI key is configured).

This ensures "build website", "create portfolio", "make landing page" all
resolve to the same canonical key "website_building", enabling the Pattern
Extractor to cluster them correctly.
"""

import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Canonical Intent Registry ───────────────────────────────────────────────
# Maps a canonical intent name to a list of representative keywords.
# The normalizer scores each canonical intent against the user input via
# token overlap and picks the highest-scoring match.
CANONICAL_INTENT_MAP: Dict[str, List[str]] = {
    "website_building": [
        "website", "landing page", "portfolio", "html", "web page",
        "site", "frontend", "build site", "create page", "homepage",
    ],
    "data_analysis": [
        "analyze", "data", "csv", "excel", "statistics", "chart",
        "graph", "pandas", "report", "insights", "dataset",
    ],
    "code_debugging": [
        "debug", "fix", "error", "bug", "exception", "traceback",
        "crash", "broken", "not working", "failing",
    ],
    "file_management": [
        "file", "read", "write", "open", "save", "create file",
        "delete", "rename", "copy", "move", "folder",
    ],
    "web_search": [
        "search", "find", "lookup", "google", "look up", "browse",
        "what is", "who is", "when", "where",
    ],
    "messaging": [
        "message", "send", "whatsapp", "telegram", "notify", "text",
        "ping", "alert", "contact",
    ],
    "scheduling": [
        "remind", "schedule", "timer", "alarm", "later", "every day",
        "daily", "hourly", "at 8", "tomorrow",
    ],
    "code_generation": [
        "write code", "generate code", "create script", "python script",
        "function", "class", "api", "implement", "code for",
    ],
    "deployment": [
        "deploy", "server", "run", "start", "launch", "production",
        "docker", "kubernetes", "cloud", "hosting",
    ],
}

# Minimum overlap score to accept a canonical match (0.0 – 1.0 range)
MIN_MATCH_SCORE = 0.1


def _compute_token_overlap(user_tokens: set, keywords: List[str]) -> float:
    """
    Computes a relevance score (0..1) as the fraction of keyword phrases
    found as substrings within the normalized user input.
    """
    if not keywords:
        return 0.0
    user_input_str = " ".join(user_tokens)
    hit_count = sum(1 for kw in keywords if kw in user_input_str)
    return hit_count / len(keywords)


def normalize_intent(user_input: str) -> str:
    """
    Converts a raw user input string into a stable canonical intent name.

    Returns the canonical intent if a match exceeds MIN_MATCH_SCORE,
    otherwise falls back to a cleaned slug of the raw input.
    """
    cleaned = re.sub(r"[^\w\s]", " ", user_input.lower()).strip()
    tokens = set(cleaned.split())

    best_intent: Optional[str] = None
    best_score: float = 0.0

    for canonical, keywords in CANONICAL_INTENT_MAP.items():
        score = _compute_token_overlap(tokens, keywords)
        if score > best_score:
            best_score = score
            best_intent = canonical

    if best_score >= MIN_MATCH_SCORE and best_intent:
        logger.debug(
            f"[IntentNormalizer] '{user_input}' → '{best_intent}' (score={best_score:.2f})"
        )
        return best_intent

    # Graceful fallback: slugify the raw input to ensure a stable key
    fallback = re.sub(r"\s+", "_", cleaned)[:40]
    logger.debug(
        f"[IntentNormalizer] No strong match for '{user_input}'. Fallback slug: '{fallback}'"
    )
    return fallback


def normalize_intent_llm(user_input: str, openai_api_key: str) -> str:
    """
    Optional LLM-based normalizer for higher accuracy when an API key is available.
    Falls back to the keyword-based normalizer if the LLM call fails.
    """
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        canonical_list = ", ".join(CANONICAL_INTENT_MAP.keys())
        llm = ChatOpenAI(api_key=openai_api_key, model="gpt-4o-mini", temperature=0)
        messages = [
            SystemMessage(content=(
                f"You are an intent classifier. Map the user's request to ONE of these canonical intents: "
                f"{canonical_list}.\n"
                "If none match, return a short snake_case slug (max 3 words) describing the intent. "
                "Return ONLY the canonical intent string, nothing else."
            )),
            HumanMessage(content=user_input),
        ]
        result = llm.invoke(messages).content.strip().lower().replace(" ", "_")
        logger.debug(f"[IntentNormalizer] LLM mapped '{user_input}' → '{result}'")
        return result
    except Exception as e:
        logger.warning(f"[IntentNormalizer] LLM fallback failed: {e}. Using keyword normalizer.")
        return normalize_intent(user_input)
