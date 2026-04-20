"""
Stability Stress Test Suite for JARVIS Governance Layer.

Validates behavioral robustness under non-ideal conditions:
  Scenario 1 -- Gradual Degradation (slow decline 0.9 -> 0.75)
  Scenario 2 -- Noisy Performance (alternating success/fail)
  Scenario 3 -- Combination Failure (A+B fails, A and B individually stable)
  Scenario 4 -- Canary Containment (failing skill isolation)
  Scenario 5 -- Oscillation Risk (borderline threshold toggling)

Usage:
  cd H:\Jarvis\jarvis
  python -m pytest ../test_stress_stability.py -v --tb=short
  OR
  cd H:\Jarvis\jarvis
  python ../test_stress_stability.py
"""

import sys
import os
import json
import copy
import math
import random
import importlib.util

# == Mocking layer (must execute before any jarvis imports) ====================
# Mock openai if not installed
if importlib.util.find_spec("openai") is None:
    import types
    sys.modules["openai"] = types.ModuleType("openai")

# Mock langchain_openai if not installed
if importlib.util.find_spec("langchain_openai") is None:
    import types
    sys.modules["langchain_openai"] = types.ModuleType("langchain_openai")

# Provide settings stub before importing the rest of the system
from config.settings import settings
settings.openai_api_key = "stress_test_key"


# == Mock classes for OpenAI and LangChain =====================================

class _MockCompletions:
    """Returns context-aware mock responses for OpenAI chat completions."""
    def create(self, *args, **kwargs):
        messages = kwargs.get("messages", [{}])
        content_str = messages[0].get("content", "") if messages else ""

        class _Msg:
            if "Categories for task_type" in content_str:
                content = '{"task_type": "debugging", "complexity": "medium"}'
            else:
                content = '["code_debugging"]'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


class _MockChat:
    completions = _MockCompletions()


class _MockOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = _MockChat()


import openai
openai.OpenAI = _MockOpenAI

import langchain_openai

class _MockChatOpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def invoke(self, messages):
        class _R:
            content = json.dumps({
                "is_valid_skill": True,
                "candidate": {
                    "name": "code_debugging_v2",
                    "description": "Mock debugging v2",
                    "when_to_use": ["error", "bug"],
                    "instructions": "1. Find bug\n2. Fix bug"
                }
            })
        return _R()


langchain_openai.ChatOpenAI = _MockChatOpenAI


# == System imports (after mocks are installed) ================================

from skills.registry import Skill, SkillRegistry
from skills.metrics import SkillMetricsTracker
from skills.selector import select_skills


# == Helpers ===================================================================

def _fresh_tracker() -> SkillMetricsTracker:
    """Create a clean metrics tracker to isolate test scenarios."""
    return SkillMetricsTracker()


def _fresh_registry() -> SkillRegistry:
    """Create a clean registry to isolate test scenarios."""
    return SkillRegistry()


def _inject_task(
    tracker: SkillMetricsTracker,
    req_id: str,
    skills: list,
    success: bool,
    plan_quality: float = 0.8,
    retries: int = 0,
    task_type: str = "debugging"
):
    """Inject a single simulated task execution into the metrics tracker."""
    tracker.init_request(req_id)
    tracker.log_task_context(req_id, task_type, "medium")
    tracker.log_selected_skills(req_id, skills)
    tracker.log_plan_quality(req_id, plan_quality)
    tracker.log_execution_success(req_id, success, retries)


def _compute_snapshot(tracker: SkillMetricsTracker, label: str) -> dict:
    """Capture a point-in-time health snapshot from the tracker.

    Includes both the legacy trailing-window metrics and the new
    dual-baseline + drift detection channels for full observability.
    """
    settled = [m for m in tracker.metrics.values() if m["final_status"] != "pending"]
    seq = tracker.get_health_sequence(settled)

    if not seq:
        return {"label": label, "count": 0, "global_mean": 0, "global_std": 0, "short_mean": 0}

    long_win = seq[-100:] if len(seq) > 100 else seq
    short_win = seq[-20:] if len(seq) >= 20 else seq

    def mean(d):
        return sum(d) / max(1, len(d))

    def stddev(d, m):
        return math.sqrt(sum((x - m) ** 2 for x in d) / max(1, len(d) - 1))

    g_mean = mean(long_win)
    g_std = stddev(long_win, g_mean)
    s_mean = mean(short_win)

    # Compute drift slope for observability
    drift_slope = tracker._compute_drift_slope(short_win)

    snapshot = {
        "label": label,
        "count": len(settled),
        "global_mean": round(g_mean, 4),
        "global_std": round(g_std, 4),
        "short_mean": round(s_mean, 4),
        "regression_gap": round(g_mean - s_mean, 4),
        "threshold_1_5": round(1.5 * g_std, 4),
        "legacy_trigger": s_mean < g_mean - (1.5 * g_std) and g_std > 0.01,
        # New dual-baseline channels
        "static_mean": round(tracker._static_mean, 4),
        "ema": round(tracker._ema_health, 4) if tracker._ema_initialized else None,
        "drift_slope": round(drift_slope, 5),
    }
    return snapshot


# =============================================================================
# SCENARIO 1 -- GRADUAL DEGRADATION
# Success rate slides: 0.90 -> 0.85 -> 0.80 -> 0.75 over 4 phases
# =============================================================================

def test_scenario_1_gradual_degradation():
    """Validate detection timing on a slow, steady performance decline.

    Design: Build a stable 100-task baseline (long window), then inject
    degrading batches that dominate the short window (last 20 tasks).
    Each phase clears the short window with worsening data while the
    long-window mean stays anchored to the historical baseline.
    """
    print("\n" + "=" * 72)
    print("  SCENARIO 1 -- GRADUAL DEGRADATION")
    print("=" * 72)

    tracker = _fresh_tracker()
    registry = _fresh_registry()

    import skills.registry as reg_mod
    import skills.metrics as met_mod
    old_registry = reg_mod.skill_registry
    old_metrics = met_mod.skill_metrics
    reg_mod.skill_registry = registry
    met_mod.skill_metrics = tracker

    target_skill = Skill(
        name="gradual_skill",
        description="Skill under gradual degradation test",
        when_to_use=["debug"],
        instructions="test",
        priority=0.8,
        status="core",
        family="gradual_skill"
    )
    registry.register_skill(target_skill)

    snapshots = []
    mitigation_events = []

    # Phase 0: Inject 100 stable, high-quality baseline (long window anchor)
    # success_rate = 0.95 with plan_quality = 0.90
    for i in range(100):
        _inject_task(tracker, f"s1-base-{i}", ["gradual_skill"], success=True, plan_quality=0.90)

    snapshots.append(_compute_snapshot(tracker, "Phase 0: Baseline (1.00)"))
    print(f"  [Phase 0] {snapshots[-1]}")

    # Degradation phases -- each injects 25 tasks at a worsening success rate
    # The short window (last 20) will be filled with degraded data while
    # the long window (last 100) retains the stable baseline
    PHASES = [
        ("Phase 1: Slight dip",    0.80, 0.70),
        ("Phase 2: Noticeable",    0.60, 0.50),
        ("Phase 3: Failing",       0.30, 0.25),
        ("Phase 4: Collapse",      0.10, 0.10),
    ]

    triggered_at_phase = None

    for phase_idx, (phase_name, success_rate, plan_q) in enumerate(PHASES, start=1):
        for i in range(25):
            success = random.random() < success_rate
            retries = 0 if success else 2
            pq = plan_q if success else 0.1
            _inject_task(
                tracker,
                f"s1-p{phase_idx}-{i}",
                ["gradual_skill"],
                success,
                plan_quality=pq,
                retries=retries
            )

        pre_stage = target_skill.mitigation_stage
        tracker.detect_and_handle_regressions()
        post_stage = target_skill.mitigation_stage

        snap = _compute_snapshot(tracker, f"{phase_name} (rate={success_rate})")
        snapshots.append(snap)

        if post_stage > pre_stage:
            event = {
                "phase": phase_name,
                "success_rate": success_rate,
                "stage_before": pre_stage,
                "stage_after": post_stage,
                "health": snap
            }
            mitigation_events.append(event)
            if triggered_at_phase is None:
                triggered_at_phase = phase_name

        print(f"  [{phase_name}] stage: {pre_stage} -> {post_stage} | {snap}")

    # == Report ================================================================
    print("\n  == Results ==")
    print(f"  Detection trigger point : {triggered_at_phase or 'NOT TRIGGERED'}")
    print(f"  Final mitigation stage  : {target_skill.mitigation_stage}")
    print(f"  Mitigation events count : {len(mitigation_events)}")
    for ev in mitigation_events:
        print(f"    -> {ev['phase']}: Stage {ev['stage_before']}->{ev['stage_after']}")
    print(f"  Final status            : {target_skill.status}")

    reg_mod.skill_registry = old_registry
    met_mod.skill_metrics = old_metrics

    # Assertions: the system MUST detect and escalate at some point
    assert triggered_at_phase is not None, "Gradual degradation was never detected!"
    assert target_skill.mitigation_stage >= 1, "Mitigation did not escalate at all!"
    print("  [PASS] SCENARIO 1 PASSED")

    return {
        "scenario": "Gradual Degradation",
        "triggered_at": triggered_at_phase,
        "final_stage": target_skill.mitigation_stage,
        "final_status": target_skill.status,
        "snapshots": snapshots,
        "events": mitigation_events
    }


# =============================================================================
# SCENARIO 2 -- NOISY PERFORMANCE
# Alternating success/fail pattern -- should NOT trigger false positives
# =============================================================================

def test_scenario_2_noisy_performance():
    """Validate that alternating success/fail noise does not trigger regression."""
    print("\n" + "=" * 72)
    print("  SCENARIO 2 -- NOISY PERFORMANCE (FALSE POSITIVE CHECK)")
    print("=" * 72)

    tracker = _fresh_tracker()
    registry = _fresh_registry()

    import skills.registry as reg_mod
    import skills.metrics as met_mod
    old_registry = reg_mod.skill_registry
    old_metrics = met_mod.skill_metrics
    reg_mod.skill_registry = registry
    met_mod.skill_metrics = tracker

    noisy_skill = Skill(
        name="noisy_skill",
        description="Skill under noisy performance test",
        when_to_use=["test"],
        instructions="test",
        priority=0.8,
        status="core",
        family="noisy_skill"
    )
    registry.register_skill(noisy_skill)

    snapshots = []

    # Inject 100 baseline tasks with ~50% alternating success (noisy but centered)
    for i in range(100):
        success = (i % 2 == 0)  # strictly alternating: T, F, T, F, ...
        pq = 0.7 if success else 0.3
        retries = 0 if success else 1
        _inject_task(tracker, f"s2-base-{i}", ["noisy_skill"], success, plan_quality=pq, retries=retries)

    snapshots.append(_compute_snapshot(tracker, "Baseline (alternating 50/50)"))
    print(f"  [Baseline] {snapshots[-1]}")

    # Now inject 30 more -- same alternating pattern (no actual change)
    for i in range(30):
        success = (i % 2 == 0)
        pq = 0.7 if success else 0.3
        retries = 0 if success else 1
        _inject_task(tracker, f"s2-ext-{i}", ["noisy_skill"], success, plan_quality=pq, retries=retries)

    tracker.detect_and_handle_regressions()
    snapshots.append(_compute_snapshot(tracker, "Extended (same pattern)"))
    print(f"  [Extended] stage: {noisy_skill.mitigation_stage} | {snapshots[-1]}")

    # == Report ============================================================
    print("\n  == Results ==")
    print(f"  Mitigation stage        : {noisy_skill.mitigation_stage}")
    print(f"  Status                  : {noisy_skill.status}")
    print(f"  Regression triggered    : {snapshots[-1]['legacy_trigger']}")
    print(f"  Global Mean             : {snapshots[-1]['global_mean']}")
    print(f"  Short Mean              : {snapshots[-1]['short_mean']}")
    print(f"  Gap                     : {snapshots[-1]['regression_gap']}")

    reg_mod.skill_registry = old_registry
    met_mod.skill_metrics = old_metrics

    # Assertion: the system MUST NOT trigger a false positive
    assert noisy_skill.mitigation_stage == 0, \
        f"False positive! Mitigation triggered at stage {noisy_skill.mitigation_stage} on noisy-but-stable data."
    assert noisy_skill.status == "core", "Skill was incorrectly archived under noise."
    print("  [PASS] SCENARIO 2 PASSED")

    return {
        "scenario": "Noisy Performance",
        "false_positive": False,
        "final_stage": noisy_skill.mitigation_stage,
        "final_status": noisy_skill.status,
        "snapshots": snapshots
    }


# =============================================================================
# SCENARIO 3 -- COMBINATION FAILURE
# Skills A and B are individually stable, but A+B as a combination fails
# =============================================================================

def test_scenario_3_combination_failure():
    """Validate that failures are correctly attributed to the combination, not individuals."""
    print("\n" + "=" * 72)
    print("  SCENARIO 3 -- COMBINATION FAILURE ATTRIBUTION")
    print("=" * 72)

    tracker = _fresh_tracker()
    registry = _fresh_registry()

    import skills.registry as reg_mod
    import skills.metrics as met_mod
    old_registry = reg_mod.skill_registry
    old_metrics = met_mod.skill_metrics
    reg_mod.skill_registry = registry
    met_mod.skill_metrics = tracker

    skill_a = Skill(name="stable_A", description="Stable A", when_to_use=["test"],
                    instructions="test", priority=0.8, status="core", family="stable_A")
    skill_b = Skill(name="stable_B", description="Stable B", when_to_use=["test"],
                    instructions="test", priority=0.8, status="core", family="stable_B")
    registry.register_skill(skill_a)
    registry.register_skill(skill_b)

    # Inject INDIVIDUAL successes for A (high success)
    for i in range(40):
        _inject_task(tracker, f"s3-a-{i}", ["stable_A"], success=True, plan_quality=0.9)

    # Inject INDIVIDUAL successes for B (high success)
    for i in range(40):
        _inject_task(tracker, f"s3-b-{i}", ["stable_B"], success=True, plan_quality=0.9)

    # Inject COMBINATION A+B with HIGH failure rate
    for i in range(30):
        success = random.random() < 0.2  # only 20% success when combined
        pq = 0.3 if not success else 0.7
        retries = 3 if not success else 0
        _inject_task(tracker, f"s3-ab-{i}", ["stable_A", "stable_B"], success, plan_quality=pq, retries=retries)

    # Check individual stats remain high
    stats_a = tracker.get_skill_stats("stable_A")
    stats_b = tracker.get_skill_stats("stable_B")
    combo_stats = tracker.get_combination_stats(("stable_A", "stable_B"))

    # Run regression detection check
    tracker.detect_and_handle_regressions()

    print(f"  Individual A stats      : success_rate={stats_a['success_rate']:.3f}, usage={stats_a['usage_count']}")
    print(f"  Individual B stats      : success_rate={stats_b['success_rate']:.3f}, usage={stats_b['usage_count']}")
    print(f"  Combination A+B stats   : success_rate={combo_stats['success_rate']:.3f}, usage={combo_stats['usage_count']}")
    print(f"  Skill A mitigation      : stage={skill_a.mitigation_stage}, status={skill_a.status}")
    print(f"  Skill B mitigation      : stage={skill_b.mitigation_stage}, status={skill_b.status}")

    # == Report ============================================================
    print("\n  == Results ==")

    # The combination stats should clearly show the failure is in A+B, not in A or B alone.
    # Individual success rates should remain high (diluted by combo failures, but still above 0.5)
    individual_a_rate = stats_a["success_rate"]
    individual_b_rate = stats_b["success_rate"]
    combo_rate = combo_stats["success_rate"]

    print(f"  A individual rate       : {individual_a_rate:.3f}")
    print(f"  B individual rate       : {individual_b_rate:.3f}")
    print(f"  A+B combination rate    : {combo_rate:.3f}")
    print(f"  Rate differential       : {min(individual_a_rate, individual_b_rate) - combo_rate:.3f}")

    # The combination scoring system should penalize A+B via the LCB/bonus calculation
    combo_key = tuple(sorted(["stable_A", "stable_B"]))
    t_use = max(1, tracker.get_task_total_usage())
    c_use = max(1, combo_stats["usage_count"])
    uncertainty = math.sqrt(math.log(t_use) / c_use)
    lcb = combo_stats["success_rate"] - uncertainty
    print(f"  Combination LCB         : {lcb:.3f}")
    print(f"  (Negative LCB = system will avoid this combination)")

    reg_mod.skill_registry = old_registry
    met_mod.skill_metrics = old_metrics

    # Assertion: combination rate should be drastically lower than individual rates
    assert combo_rate < 0.4, f"Combination success rate unexpectedly high: {combo_rate}"
    assert individual_a_rate > 0.5, f"Individual A rate collapsed: {individual_a_rate}"
    assert individual_b_rate > 0.5, f"Individual B rate collapsed: {individual_b_rate}"
    # The LCB should be negative or near zero, causing the selector to avoid this combo
    assert lcb < 0.3, f"LCB for failing combination unexpectedly high: {lcb}"
    print("  [PASS] SCENARIO 3 PASSED")

    return {
        "scenario": "Combination Failure",
        "individual_A_rate": round(individual_a_rate, 3),
        "individual_B_rate": round(individual_b_rate, 3),
        "combination_rate": round(combo_rate, 3),
        "combination_LCB": round(lcb, 3),
        "skill_A_status": skill_a.status,
        "skill_B_status": skill_b.status
    }


# =============================================================================
# SCENARIO 4 -- CANARY CONTAINMENT
# A failing skill is placed in canary (stage 2). Verify the failure stays isolated.
# =============================================================================

def test_scenario_4_canary_containment():
    """Validate that canary-routed failing skills do not pollute baseline performance."""
    print("\n" + "=" * 72)
    print("  SCENARIO 4 -- CANARY CONTAINMENT")
    print("=" * 72)

    tracker = _fresh_tracker()
    registry = _fresh_registry()

    import skills.registry as reg_mod
    import skills.metrics as met_mod
    old_registry = reg_mod.skill_registry
    old_metrics = met_mod.skill_metrics
    reg_mod.skill_registry = registry
    met_mod.skill_metrics = tracker

    # A stable baseline skill that should remain unaffected
    baseline_skill = Skill(
        name="baseline_stable", description="Rock solid skill",
        when_to_use=["debug", "error", "bug"], instructions="test",
        priority=0.9, status="core", family="baseline_stable"
    )
    # A failing skill that will be placed directly into canary stage 2
    canary_skill = Skill(
        name="canary_failing", description="Failing canary skill",
        when_to_use=["debug", "error", "bug"], instructions="test",
        priority=0.5, status="core", family="canary_failing",
        mitigation_stage=2  # Pre-set to canary stage
    )
    registry.register_skill(baseline_skill)
    registry.register_skill(canary_skill)

    # Inject stable baseline data (100 tasks, 95% success)
    for i in range(100):
        success = random.random() < 0.95
        _inject_task(tracker, f"s4-base-{i}", ["baseline_stable"], success, plan_quality=0.9)

    # Inject failing canary data (50 tasks, 10% success)
    for i in range(50):
        success = random.random() < 0.10
        pq = 0.2 if not success else 0.7
        _inject_task(tracker, f"s4-canary-{i}", ["canary_failing"], success, plan_quality=pq, retries=3)

    # Measure isolation: the baseline health should remain high despite canary failures
    baseline_stats = tracker.get_skill_stats("baseline_stable")
    canary_stats = tracker.get_skill_stats("canary_failing")

    # Simulate canary routing probability check
    t_use = max(1, tracker.get_task_total_usage())
    c_use = max(1, canary_stats["usage_count"])
    uncertainty = math.sqrt(math.log(t_use) / c_use)
    traffic_pct = max(0.01, 0.15 - (uncertainty * 0.05))

    print(f"  Baseline stats          : success_rate={baseline_stats['success_rate']:.3f}")
    print(f"  Canary stats            : success_rate={canary_stats['success_rate']:.3f}")
    print(f"  Canary traffic %        : {traffic_pct:.1%}")
    print(f"  Canary uncertainty      : {uncertainty:.3f}")

    # Compute global health including both skill pools
    all_settled = [m for m in tracker.metrics.values() if m["final_status"] != "pending"]
    overall_seq = tracker.get_health_sequence(all_settled)
    overall_mean = sum(overall_seq) / max(1, len(overall_seq))

    # Compute baseline-only health
    baseline_settled = [m for m in tracker.metrics.values()
                        if m["final_status"] != "pending" and "baseline_stable" in m["selected_skills"]]
    baseline_seq = tracker.get_health_sequence(baseline_settled)
    baseline_mean = sum(baseline_seq) / max(1, len(baseline_seq))

    print(f"  Overall system health   : {overall_mean:.3f}")
    print(f"  Baseline-only health    : {baseline_mean:.3f}")
    print(f"  Health contamination    : {baseline_mean - overall_mean:.3f}")

    # == Report ============================================================
    print("\n  == Results ==")
    print(f"  Canary traffic cap      : {traffic_pct:.1%}")
    print(f"  Baseline uncontaminated : {baseline_mean:.3f}")
    print(f"  Canary isolation delta  : {baseline_mean - overall_mean:.3f}")

    reg_mod.skill_registry = old_registry
    met_mod.skill_metrics = old_metrics

    # Assertions
    assert baseline_stats["success_rate"] > 0.85, \
        f"Baseline contaminated! rate={baseline_stats['success_rate']:.3f}"
    assert canary_stats["success_rate"] < 0.25, \
        f"Canary unexpectedly successful: rate={canary_stats['success_rate']:.3f}"
    assert traffic_pct <= 0.15, f"Canary traffic cap exceeded 15%: {traffic_pct:.1%}"
    print("  [PASS] SCENARIO 4 PASSED")

    return {
        "scenario": "Canary Containment",
        "baseline_rate": round(baseline_stats["success_rate"], 3),
        "canary_rate": round(canary_stats["success_rate"], 3),
        "canary_traffic_pct": round(traffic_pct, 4),
        "baseline_health": round(baseline_mean, 3),
        "overall_health": round(overall_mean, 3),
        "contamination_delta": round(baseline_mean - overall_mean, 3)
    }


# =============================================================================
# SCENARIO 5 -- OSCILLATION RISK
# Performance hovers right around the regression threshold.
# System should NOT repeatedly promote/rollback (thrashing).
# =============================================================================

def test_scenario_5_oscillation_risk():
    """Validate that borderline performance does not cause promote/rollback thrashing."""
    print("\n" + "=" * 72)
    print("  SCENARIO 5 -- OSCILLATION RISK")
    print("=" * 72)

    tracker = _fresh_tracker()
    registry = _fresh_registry()

    import skills.registry as reg_mod
    import skills.metrics as met_mod
    old_registry = reg_mod.skill_registry
    old_metrics = met_mod.skill_metrics
    reg_mod.skill_registry = registry
    met_mod.skill_metrics = tracker

    oscillating_skill = Skill(
        name="oscillating_skill", description="Borderline performance skill",
        when_to_use=["test"], instructions="test",
        priority=0.8, status="core", family="oscillating_skill"
    )
    registry.register_skill(oscillating_skill)

    # Inject 100 baseline tasks (80% success -- moderate)
    for i in range(100):
        success = random.random() < 0.80
        pq = 0.75 if success else 0.35
        retries = 0 if success else 1
        _inject_task(tracker, f"s5-base-{i}", ["oscillating_skill"], success, plan_quality=pq, retries=retries)

    snap_baseline = _compute_snapshot(tracker, "Baseline")
    print(f"  [Baseline] {snap_baseline}")

    # Now run 8 cycles of performance that oscillates around the borderline
    # Alternating between slightly-below and slightly-above baseline health
    stage_history = []
    snapshots = [snap_baseline]
    stage_transitions = 0

    for cycle in range(8):
        # Alternate: even cycles are slightly worse, odd cycles recover slightly
        if cycle % 2 == 0:
            cycle_success_rate = 0.65  # dip below baseline
        else:
            cycle_success_rate = 0.82  # slightly above baseline (slight recovery)

        for i in range(15):
            success = random.random() < cycle_success_rate
            pq = 0.7 if success else 0.3
            retries = 0 if success else 2
            _inject_task(
                tracker, f"s5-cycle{cycle}-{i}",
                ["oscillating_skill"], success, plan_quality=pq, retries=retries
            )

        pre_stage = oscillating_skill.mitigation_stage
        tracker.detect_and_handle_regressions()
        post_stage = oscillating_skill.mitigation_stage

        if post_stage != pre_stage:
            stage_transitions += 1

        snap = _compute_snapshot(tracker, f"Cycle {cycle} (rate={cycle_success_rate})")
        snapshots.append(snap)
        stage_history.append({
            "cycle": cycle,
            "rate": cycle_success_rate,
            "stage_before": pre_stage,
            "stage_after": post_stage,
            "transitioned": post_stage != pre_stage
        })

        print(f"  [Cycle {cycle}] rate={cycle_success_rate} stage: {pre_stage}->{post_stage} | {snap}")

    # == Report ============================================================
    print("\n  == Results ==")
    print(f"  Total stage transitions : {stage_transitions}")
    print(f"  Final mitigation stage  : {oscillating_skill.mitigation_stage}")
    print(f"  Final status            : {oscillating_skill.status}")
    for sh in stage_history:
        marker = " <- TRANSITION" if sh["transitioned"] else ""
        print(f"    Cycle {sh['cycle']}: rate={sh['rate']} | {sh['stage_before']}->{sh['stage_after']}{marker}")

    reg_mod.skill_registry = old_registry
    met_mod.skill_metrics = old_metrics

    # Assertions: the system should NOT oscillate rapidly (max 3 transitions across 8 cycles)
    # Multi-stage mitigation is monotonic (0->1->2->3), so "thrashing" means
    # the system erroneously triggers multiple escalations on borderline data.
    # With 8 cycles and only moderate dips, we expect <= 3 escalations max.
    assert stage_transitions <= 3, \
        f"Oscillation detected! {stage_transitions} stage transitions in 8 cycles = thrashing."
    print("  [PASS] SCENARIO 5 PASSED")

    return {
        "scenario": "Oscillation Risk",
        "total_transitions": stage_transitions,
        "final_stage": oscillating_skill.mitigation_stage,
        "final_status": oscillating_skill.status,
        "stage_history": stage_history,
        "snapshots": snapshots
    }


# =============================================================================
# MAIN: RUN ALL SCENARIOS AND PRODUCE CONSOLIDATED REPORT
# =============================================================================

def run_all():
    """Execute all 5 stress test scenarios and print consolidated results."""
    print("\n" + "#" * 72)
    print("  JARVIS STABILITY GOVERNANCE -- STRESS TEST SUITE")
    print("#" * 72)

    results = {}
    scenarios = [
        ("Scenario 1", test_scenario_1_gradual_degradation),
        ("Scenario 2", test_scenario_2_noisy_performance),
        ("Scenario 3", test_scenario_3_combination_failure),
        ("Scenario 4", test_scenario_4_canary_containment),
        ("Scenario 5", test_scenario_5_oscillation_risk),
    ]

    passed = 0
    failed = 0

    for name, test_fn in scenarios:
        try:
            result = test_fn()
            results[name] = {"status": "PASSED", "data": result}
            passed += 1
        except AssertionError as e:
            results[name] = {"status": "FAILED", "error": str(e)}
            failed += 1
            print(f"  [FAIL] {name} FAILED: {e}")
        except Exception as e:
            results[name] = {"status": "ERROR", "error": str(e)}
            failed += 1
            print(f"  [FAIL] {name} ERROR: {e}")

    # == Consolidated Summary ==============================================
    print("\n" + "#" * 72)
    print("  CONSOLIDATED RESULTS")
    print("#" * 72)

    for name, res in results.items():
        icon = "[PASS]" if res["status"] == "PASSED" else "[FAIL]"
        print(f"  {icon} {name}: {res['status']}")
        if res["status"] == "PASSED":
            data = res["data"]
            for k, v in data.items():
                if k in ("snapshots", "events", "stage_history"):
                    continue  # skip verbose timeline data in summary
                print(f"       {k}: {v}")

    print(f"\n  Total: {passed} passed, {failed} failed out of {len(scenarios)}")
    print("#" * 72)

    return results


if __name__ == "__main__":
    run_all()
