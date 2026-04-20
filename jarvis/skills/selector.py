import json
import logging
from typing import List, Dict, Any

from config.settings import settings
from skills.registry import skill_registry, Skill
from skills.metrics import skill_metrics
from skills.context import classify_task
import random
import uuid
import itertools
import math
from observability.logger import structured_logger

logger = logging.getLogger(__name__)

def _keyword_select(user_input: str) -> List[Skill]:
    lower_input = user_input.lower()
    selected = set()
    for skill in skill_registry.get_all_skills():
        for kw in skill.when_to_use:
            if kw.lower() in lower_input:
                selected.add(skill.name)
                break
    return [skill_registry.get_skill(name) for name in selected]

def _llm_select(user_input: str, context: str) -> List[Skill]:
    is_enabled = bool(settings.openai_api_key and not settings.openai_api_key.startswith("your_"))
    if not is_enabled:
        return []
        
    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        
        all_skills = skill_registry.get_all_skills()
        skill_summaries = "\n".join([f"- {s.name}: {s.description} (When to use: {', '.join(s.when_to_use)})" for s in all_skills])
        
        prompt = f"""You are the JARVIS Skill Selector.
You have the following skills available:
{skill_summaries}

Select the most relevant skills for this task:
Task: "{user_input}"
Context: "{context}"

Return ONLY a JSON list of skill names. Example: ["code_debugging", "file_handling"]"""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```json"): raw = raw.split("```json")[1].split("```")[0]
        elif raw.startswith("```"): raw = raw.split("```")[1]
        
        selected_names = json.loads(raw.strip())
        if not isinstance(selected_names, list):
            return []
            
        selected_skills = []
        for name in selected_names:
            sk = skill_registry.get_skill(name)
            if sk:
                selected_skills.append(sk)
        return selected_skills
    except Exception as e:
        logger.error(f"[Skill Selector] LLM selection failed: {e}")
        return []

def _score_skill(skill: Skill, task_type: str = None) -> float:
    """Computes a dynamic context-aware performance score for a skill."""
    stats = skill_metrics.get_skill_stats(skill.name, task_type=task_type)
    
    # PART 6 — COLD START HANDLING
    base_priority = getattr(skill, "priority", 1.0)
    
    if stats["usage_count"] < 3:
        # Heavily artificially bias new skills based on hardcoded priority 
        # combined with baseline defaults to ensure they get utilized enough to produce signal
        return base_priority * 1.5 + 0.5 

    score = (
        base_priority
        + (stats["success_rate"] * 0.4)
        + (stats["avg_plan_quality"] * 0.3)
        - (stats["avg_retry_count"] * 0.2)
        + (stats["recent_performance"] * 0.3)
    )
    
    # PART 5 — SOFT SUPPRESSION
    if stats["success_rate"] < 0.4 or stats["avg_retry_count"] > 2.0:
        # Use penalty factor inside (0.2–0.5) bounding instead of hard cutoff
        # Let's scale the penalty inversely proportional to how much it fails
        penalty_factor = max(0.2, min(0.5, stats["success_rate"]))
        score *= penalty_factor
        
    return score

def select_skills(user_input: str, context_str: str = "") -> List[Skill]:
    try:
        skill_metrics.evaluate_exploratory_skills()
    except Exception as e:
        logger.error(f"[Skill Selector] Metric evaluation hook failed: {e}")

    # STEP 0: Context Classification (PART 1)
    task_context = classify_task(user_input)
    task_type = task_context.get("task_type", "general")
    complexity = task_context.get("complexity", "low")

    # STEP 1: Get candidate skills using Hybrid logic
    selected = _keyword_select(user_input)
    
    # Fallback to LLM if ambiguous
    if not selected or len(selected) > 3:
        logger.debug("[Skill Selector] Ambiguous keyword match. Falling back to LLM selection.")
        llm_selected = _llm_select(user_input, context_str)
        if llm_selected:
            selected = llm_selected

    if not selected:
        return []

    # Remove duplicates preserving objects
    unique_selected = []
    seen = set()
    import random
    import math
    for s in selected:
        if s.name not in seen:
            seen.add(s.name)
            
            # Adaptive Canary Routing
            if s.mitigation_stage == 2:
                # Dynamic canary routing based on uncertainty factors
                stats = skill_metrics.get_skill_stats(s.name)
                usage = max(1, stats["usage_count"])
                t_use = max(1, skill_metrics.get_task_total_usage())
                uncertainty = math.sqrt(math.log(t_use) / usage)
                
                # Baseline 15% traffic, scales down by uncertainty
                traffic_pct = max(0.01, 0.15 - (uncertainty * 0.05))
                
                if random.random() > traffic_pct:
                    logger.debug(f"[Skill Selector] Bypassing canary skill: {s.name} (Traffic: {traffic_pct:.1%})")
                    continue
            
            # Filter Stage 3 entirely (just in case registry lookup missed it)
            if s.mitigation_stage == 3 or s.status == "archived":
                continue

            unique_selected.append(s)
            
    # Filter to top-K individual candidates to prevent combo explosion
    K = 5
    scored_indiv = [(sk, _score_skill(sk, task_type)) for sk in unique_selected]
    scored_indiv.sort(key=lambda x: x[1], reverse=True)
    top_k_candidates = [sk for sk, _ in scored_indiv[:K]]

    # STEP 2 & 3: Compute COMBINATION score using normalized avg, penalty, and confidence bonus
    combinations = []
    # Test combinations of 1 up to 3 length
    for r in range(1, min(4, len(top_k_candidates) + 1)):
        for combo in itertools.combinations(top_k_candidates, r):
            # Version Control: Ensure max 1 version of a skill family per combination
            families = [sk.family for sk in combo if sk.family]
            if len(families) > len(set(families)):
                continue # Skip conflicting versions
            combinations.append(combo)

    alpha = 0.4
    best_known_combo_names = skill_metrics.get_best_combination_memory(task_type)
    task_total_usage = skill_metrics.get_task_total_usage(task_type)

    scored_combinations = []
    for combo in combinations:
        
        # Base independent individual average
        avg_score = sum(_score_skill(sk, task_type) for sk in combo) / len(combo)
        
        # Diminishing returns penalty
        size_penalty = 1.0 * (len(combo) - 1)
        
        # Composition tracking
        combo_names = tuple(sorted(sk.name for sk in combo))
        c_stats = skill_metrics.get_combination_stats(combo_names, task_type)
        
        bonus = 0.0
        if c_stats["usage_count"] > 0:
            # PART 1 & 2 - Uncertainty estimation and LCB calculation
            t_use = max(1, task_total_usage)
            c_use = c_stats["usage_count"]
            uncertainty_penalty = math.sqrt(math.log(t_use) / c_use)
            LCB = c_stats["success_rate"] - uncertainty_penalty
            
            # PART 3 - Risk-aware scoring using LCB
            success_delta = LCB - 0.5
            confidence = math.log1p(c_stats["usage_count"])
            bonus = success_delta * confidence * 2.0
            
            uncertainty = uncertainty_penalty
        else:
            LCB = 0.0
            uncertainty = 1.0 # High uncertainty
            bonus = 0.0
                
        computed_score = avg_score - size_penalty + bonus
        
        # Memory blending
        memory_score = 5.0 if combo_names == best_known_combo_names else 0.0
        total_score = (alpha * memory_score) + ((1 - alpha) * computed_score)
        
        scored_combinations.append((combo, total_score, c_stats, size_penalty, (alpha * memory_score), uncertainty, LCB))

    scored_combinations.sort(key=lambda x: x[1], reverse=True)
    
    # PART 4 & 5 Strategy formulation with Uncertainty Exploration vs Exploitation
    if random.random() < 0.8 and scored_combinations:
        # Exploitation
        selected_combo = scored_combinations[0][0]
        if scored_combinations[0][4] > 0:
            reason = f"Exploitation driven strongly by Memory Blending for {task_type}"
        elif scored_combinations[0][5] < 0.3:
            reason = "Safe exploitation (low uncertainty, high confidence)"
        else:
            reason = "Risk-accepted exploitation (high score despite uncertainty)"
    else:
        # PART 4 - Exploration guided by uncertainty
        if scored_combinations:
            # Prefer combinations with High uncertainty + Promising base averages
            exploration_pool = sorted(scored_combinations, key=lambda x: x[5] + x[1]*0.2, reverse=True)
            selected_combo = exploration_pool[0][0]
            reason = "Uncertainty-guided exploration (high variance/low data + promising baseline)"
        else:
            selected_combo = ()
            reason = "Empty candidate pool fallback"
            
    top_skills = list(selected_combo)
    
    # PART 5 — OBSERVABILITY EMISSION
    debug_id = str(uuid.uuid4())[:8]
    scores_debug = [
        {
            "size": len(combo),
            "combination": [sk.name for sk in combo],
            "score": round(score, 3), 
            "memory_influence": round(mem_infl, 3),
            "penalty": round(pen, 3),
            "uncertainty": round(unc, 3),
            "LCB": round(lcb, 3),
            "stats": {
                "usage_count": stats["usage_count"],
                "success_rate": round(stats["success_rate"], 3)
            }
        } for combo, score, stats, pen, mem_infl, unc, lcb in scored_combinations[:10]  # Just top 10 debug logs to prevent console bloat
    ]
    
    health_summary = getattr(skill_metrics, 'last_health_summary', {})
    
    structured_logger.log_event("SKILL_SELECTION_DEBUG", {
        "debug_id": debug_id,
        "input": user_input,
        "classification": task_context,
        "task_type": task_type,
        "complexity": complexity,
        "system_health": health_summary,
        "combination_scores": scores_debug,
        "selected": [s.name for s in top_skills],
        "reason": reason
    })
    
    logger.info(f"[Skill Selector Debug] Trace ID: {debug_id} | Class: {task_type} | Combo Selected: {[s.name for s in top_skills]}")
    logger.info(f"[Skill Selector] Selected skills: {[s.name for s in top_skills]}")
    return top_skills
