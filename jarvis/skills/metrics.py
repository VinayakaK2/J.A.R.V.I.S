import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class SkillMetricsTracker:
    # Minimum settled tasks before snapshotting the static baseline
    STATIC_BASELINE_MIN_TASKS = 50
    # EMA decay factor -- low alpha means the EMA moves very slowly
    EMA_ALPHA = 0.02
    # Minimum negative slope (over 20 tasks) to trigger drift detection
    DRIFT_SLOPE_THRESHOLD = -0.015
    # Static baseline gap threshold (lower = more sensitive)
    STATIC_THRESHOLD_FACTOR = 1.2
    # EMA gap threshold (lower = more sensitive)
    EMA_THRESHOLD_FACTOR = 0.08

    def __init__(self):
        # In-memory storage for request metrics.
        # Key: request_id. Value: Dictionary of metrics.
        self.metrics: Dict[str, Dict[str, Any]] = {}

        # --- Dual Baseline State ---
        # Frozen snapshot of the first N healthy tasks (never updated after capture)
        self._static_baseline_seq: List[float] = []
        self._static_mean: float = 0.0
        self._static_std: float = 0.0
        self._static_baseline_locked: bool = False

        # Exponential Moving Average -- slow-moving adaptive reference
        self._ema_health: float = 0.0
        self._ema_initialized: bool = False

    def init_request(self, request_id: str) -> None:
        """Initialize the metrics payload for a new request."""
        if request_id not in self.metrics:
            self.metrics[request_id] = {
                "request_id": request_id,
                "task_type": "general",
                "complexity": "low",
                "selected_skills": [],
                "plan_quality_score": 0.0,
                "execution_success": False,
                "retry_count": 0,
                "final_status": "pending"
            }

    def log_task_context(self, request_id: str, task_type: str, complexity: str) -> None:
        self.init_request(request_id)
        self.metrics[request_id]["task_type"] = task_type
        self.metrics[request_id]["complexity"] = complexity
        logger.debug(f"[SkillMetrics] Req {request_id} context logged: {task_type} ({complexity})")

    def log_selected_skills(self, request_id: str, skills: List[str]) -> None:
        """Log the skills selected for the given request."""
        self.init_request(request_id)
        self.metrics[request_id]["selected_skills"] = skills
        logger.debug(f"[SkillMetrics] Req {request_id} initialized with skills: {skills}")

    def log_plan_quality(self, request_id: str, score: float) -> None:
        """Log the critic's plan quality score for the request."""
        self.init_request(request_id)
        self.metrics[request_id]["plan_quality_score"] = float(score)
        logger.debug(f"[SkillMetrics] Req {request_id} plan quality logged: {score}")

    def log_execution_success(self, request_id: str, success: bool, retry_count: int = 0) -> None:
        """Log final execution success and number of retries."""
        self.init_request(request_id)
        self.metrics[request_id]["execution_success"] = success
        self.metrics[request_id]["retry_count"] += retry_count
        self.metrics[request_id]["final_status"] = "success" if success else "failed"
        
        m = self.metrics[request_id]
        logger.info(
            f"[SkillMetrics] Request {request_id} finalized | "
            f"Status: {m['final_status']} | "
            f"Skills: {m['selected_skills']} | "
            f"Quality: {m['plan_quality_score']} | "
            f"Retries: {m['retry_count']}"
        )

    def get_skill_stats(self, skill_name: str, task_type: str = None) -> Dict[str, Any]:
        """Aggregate performance for a specific skill based on real usage data in context."""
        # Retrieve all settled requests explicitly invoking this skill
        relevant = [
            m for m in self.metrics.values()
            if skill_name in m["selected_skills"] and m["final_status"] != "pending"
        ]
        
        # Filter down by task_type context if provided
        if task_type:
            context_relevant = [m for m in relevant if m["task_type"] == task_type]
            # If we don't have enough data for this specific context, gracefully expand to general relevant
            if len(context_relevant) >= 1:
                relevant = context_relevant

        usage_count = len(relevant)
        if usage_count == 0:
            # Safe Default Prior distribution to allow fair initial scoring
            return {
                "usage_count": 0,
                "success_rate": 0.5,
                "avg_plan_quality": 0.5,
                "avg_retry_count": 0.0,
                "recent_performance": 0.5
            }

        successes = sum(1 for m in relevant if m["execution_success"])
        success_rate = successes / usage_count

        avg_plan_quality = sum(m["plan_quality_score"] for m in relevant) / usage_count
        avg_retry_count = sum(m["retry_count"] for m in relevant) / usage_count

        # Recent performance computed utilizing weighted window to prevent 
        # relying purely on static lifelong averages (recent N = up to 5)
        recent_N = 5
        recent_executions = relevant[-recent_N:]
        
        # We apply an incrementally heavier weight to the most recent elements
        weighted_success_sum = 0.0
        total_weight = 0.0
        
        for i, exec_m in enumerate(recent_executions):
            weight = i + 1.0 # 1.0 for oldest in window, 5.0 for newest
            if exec_m["execution_success"]:
                weighted_success_sum += weight
            total_weight += weight
            
        recent_performance = (weighted_success_sum / total_weight) if total_weight > 0 else success_rate

        return {
            "usage_count": usage_count,
            "success_rate": success_rate,
            "avg_plan_quality": avg_plan_quality,
            "avg_retry_count": avg_retry_count,
            "recent_performance": recent_performance
        }

    def get_task_total_usage(self, task_type: str = None) -> int:
        """Get the total number of finalized executions for a task domain."""
        if task_type:
            return sum(1 for m in self.metrics.values() if m["task_type"] == task_type and m["final_status"] != "pending")
        return sum(1 for m in self.metrics.values() if m["final_status"] != "pending")

    def get_combination_stats(self, skills_tuple: tuple, task_type: str = None) -> Dict[str, Any]:
        """Aggregate performance for a specific combination of skills."""
        skill_set = set(skills_tuple)
        
        relevant = [
            m for m in self.metrics.values()
            if skill_set.issubset(set(m["selected_skills"])) and m["final_status"] != "pending"
        ]
        
        # Exact task_type matches
        if task_type:
            context_relevant = [m for m in relevant if m["task_type"] == task_type]
            if len(context_relevant) >= 1:
                relevant = context_relevant

        usage_count = len(relevant)
        if usage_count == 0:
            return {
                "usage_count": 0,
                "success_rate": 0.0,
                "avg_plan_quality": 0.0,
                "avg_retry_count": 0.0,
                "recent_performance": 0.0
            }

        successes = sum(1 for m in relevant if m["execution_success"])
        success_rate = successes / usage_count
        
        return {
            "usage_count": usage_count,
            "success_rate": success_rate,
            "avg_plan_quality": sum(m["plan_quality_score"] for m in relevant) / usage_count,
            "avg_retry_count": sum(m["retry_count"] for m in relevant) / usage_count
        }
        
    def get_best_combination_memory(self, task_type: str) -> tuple:
        """Recall highest performing combination memory for the task domain."""
        memory_index = {}
        for m in self.metrics.values():
            if m["task_type"] == task_type and m["final_status"] != "pending":
                tup_key = tuple(sorted(m["selected_skills"]))
                if not tup_key: continue
                if tup_key not in memory_index:
                    memory_index[tup_key] = {"successes": 0, "total": 0}
                memory_index[tup_key]["total"] += 1
                if m["execution_success"]:
                    memory_index[tup_key]["successes"] += 1
                    
        best_combo = None
        best_win = 0.0
        
        for k, v in memory_index.items():
            if v["total"] >= 2: # meaningful signal
                rate = v["successes"] / v["total"]
                if rate > best_win:
                    best_win = rate
                    best_combo = k
                    
        return best_combo if best_combo else ()

    def evaluate_exploratory_skills(self):
        import math
        from skills.registry import skill_registry
        
        all_skills = skill_registry.get_all_skills(include_archived=False)
        exploratory_skills = [s for s in all_skills if s.status == "exploration"]
        
        for exp_skill in exploratory_skills:
            stats = self.get_skill_stats(exp_skill.name)
            usage = stats["usage_count"]
            if usage < 5:
                continue # Needs more data for statistical significance
                
            task_total_usage = max(1, self.get_task_total_usage())
            uncertainty = math.sqrt(math.log(task_total_usage) / usage)
            lcb = stats["success_rate"] - uncertainty
            
            # Find the core version of this family if it exists
            core_skills = [s for s in all_skills if s.family == exp_skill.family and s.status == "core" and s.name != exp_skill.name]
            
            should_promote = False
            if core_skills:
                core_skill = max(core_skills, key=lambda s: s.version) # latest core
                core_stats = self.get_skill_stats(core_skill.name)
                core_usage = max(1, core_stats["usage_count"])
                core_uncertainty = math.sqrt(math.log(task_total_usage) / core_usage)
                core_lcb = core_stats["success_rate"] - core_uncertainty
                
                # Check for statistically significant improvement
                if lcb > core_lcb:
                    should_promote = True
            else:
                # No core baseline, promote if it performs generally well
                if lcb > 0.4:  # Means highly confident baseline success
                    should_promote = True
                    
            if should_promote:
                import datetime
                exp_skill.status = "core"
                exp_skill.promoted_at = datetime.datetime.utcnow().isoformat()
                logger.info(f"[SkillMetrics] Promoted {exp_skill.name} to CORE (LCB {lcb:.3f})")
                
                # Retain previous versions as fallback but penalize slightly if performance was degrading
                for core_skill in core_skills:
                    if core_stats["recent_performance"] < 0.5:
                        logger.info(f"[SkillMetrics] Demoting old core {core_skill.name} priority due to degradation.")
                        core_skill.priority *= 0.5 # Demotion
                        core_skill.demoted_at = datetime.datetime.utcnow().isoformat()

    def get_health_sequence(self, dataset: List[Dict[str, Any]]) -> List[float]:
        seq = []
        for d in dataset:
            succ = 1.0 if d["execution_success"] else 0.0
            pq = d["plan_quality_score"]
            rets = min(d["retry_count"], 3) / 3.0
            # 60% success, 30% quality, 10% minimal retries
            health = succ * 0.6 + pq * 0.3 + (1.0 - rets) * 0.1
            seq.append(health)
        return seq

    def _snapshot_static_baseline(self, health_seq: List[float]) -> None:
        """Freeze the first N healthy task health values as an immutable reference.

        Called exactly once when we have enough settled data. After this,
        the static baseline never changes, making it immune to gradual
        pollution from degrading performance.
        """
        import math
        self._static_baseline_seq = list(health_seq)
        n = len(health_seq)
        self._static_mean = sum(health_seq) / max(1, n)
        self._static_std = math.sqrt(
            sum((x - self._static_mean) ** 2 for x in health_seq) / max(1, n - 1)
        )
        self._static_baseline_locked = True
        logger.info(
            f"[Governance] Static baseline locked: mean={self._static_mean:.4f}, "
            f"std={self._static_std:.4f}, samples={n}"
        )

    def _update_ema(self, latest_health: float) -> None:
        """Update the exponential moving average with the latest health value.

        Uses a very low alpha (0.02) so the EMA moves slowly and resists
        short-term noise, but will eventually diverge from a sustained decline.
        """
        if not self._ema_initialized:
            # Seed the EMA with the first observed health
            self._ema_health = latest_health
            self._ema_initialized = True
        else:
            self._ema_health = (
                (1.0 - self.EMA_ALPHA) * self._ema_health
                + self.EMA_ALPHA * latest_health
            )

    def _compute_drift_slope(self, health_seq: List[float]) -> float:
        """Compute the linear regression slope over a health sequence.

        A negative slope indicates a sustained downward trend. Uses
        ordinary least squares: slope = cov(x,y) / var(x).
        Returns 0.0 if the sequence is too short.
        """
        n = len(health_seq)
        if n < 5:
            return 0.0
        # x values: 0, 1, 2, ..., n-1
        x_mean = (n - 1) / 2.0
        y_mean = sum(health_seq) / n
        numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(health_seq))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0:
            return 0.0
        return numerator / denominator

    def detect_and_handle_regressions(self) -> None:
        """Triple-channel regression detection with multi-stage mitigation.

        Detection channels:
          1. Static baseline: compares short window against a frozen
             historical baseline that is immune to data pollution.
          2. EMA baseline: compares short window against a slow-moving
             adaptive reference that catches medium-term drift.
          3. Drift slope: detects sustained negative performance trends
             via linear regression over the short window.

        Triggers mitigation when:
          - BOTH static and EMA checks indicate degradation (dual confirmation), OR
          - Drift slope is strongly negative AND short_mean is below EMA.
        """
        import math
        import datetime
        from skills.registry import skill_registry

        settled = [m for m in self.metrics.values() if m["final_status"] != "pending"]
        if len(settled) < 30:
            return  # Need minimal data for meaningful analysis

        # --- Compute current health sequences ---
        all_seq = self.get_health_sequence(settled)
        long_window = settled[-100:] if len(settled) > 100 else settled
        short_window = settled[-20:]
        long_seq = self.get_health_sequence(long_window)
        short_seq = self.get_health_sequence(short_window)

        def mean(d):
            return sum(d) / max(1, len(d))

        def stddev(d, m):
            return math.sqrt(sum((x - m) ** 2 for x in d) / max(1, len(d) - 1))

        long_mean = mean(long_seq)
        long_std = stddev(long_seq, long_mean)
        short_mean = mean(short_seq)

        # --- Step 1: Lock static baseline on first sufficient data ---
        if not self._static_baseline_locked and len(settled) >= self.STATIC_BASELINE_MIN_TASKS:
            # Snapshot the earliest tasks before any degradation can occur
            baseline_data = settled[:self.STATIC_BASELINE_MIN_TASKS]
            baseline_health = self.get_health_sequence(baseline_data)
            self._snapshot_static_baseline(baseline_health)

        # --- Step 2: Update EMA with the latest short-window mean ---
        # If static baseline is locked and EMA is uninitialized, seed from
        # the frozen baseline so the EMA starts at the known-good level
        if not self._ema_initialized and self._static_baseline_locked:
            self._ema_health = self._static_mean
            self._ema_initialized = True
        self._update_ema(short_mean)

        # --- Step 3: Compute drift slope over the short window ---
        drift_slope = self._compute_drift_slope(short_seq)

        # --- Populate health summary for observability ---
        self.last_health_summary = {
            "global_health": long_mean,
            "short_health": short_mean,
            "global_std": long_std,
            "static_mean": self._static_mean,
            "static_std": self._static_std,
            "ema_health": self._ema_health,
            "drift_slope": round(drift_slope, 6),
            "static_locked": self._static_baseline_locked,
        }

        # --- Channel 1: Static baseline check ---
        # Short window must be significantly below the frozen baseline.
        # Use a stddev floor of 0.01 so zero-variance baselines still trigger
        # on any meaningful deviation (a system with zero variance means
        # ANY deviation is definitionally abnormal).
        static_triggered = False
        if self._static_baseline_locked:
            effective_std = max(self._static_std, 0.01)
            static_gap = self._static_mean - short_mean
            static_threshold = self.STATIC_THRESHOLD_FACTOR * effective_std
            static_triggered = static_gap > static_threshold

        # --- Channel 2: EMA baseline check ---
        # Short window must have dropped below the slow-moving EMA
        ema_triggered = False
        if self._ema_initialized:
            ema_gap = self._ema_health - short_mean
            ema_triggered = ema_gap > self.EMA_THRESHOLD_FACTOR

        # --- Channel 3: Drift slope check ---
        # Sustained negative slope in the short window
        drift_triggered = drift_slope < self.DRIFT_SLOPE_THRESHOLD

        # --- Legacy check: sudden-drop via trailing window (preserved) ---
        sudden_drop = (
            short_mean < long_mean - (1.5 * long_std)
            and long_std > 0.01
        )

        # --- Decision logic ---
        # Trigger A: dual-baseline confirmation (static AND EMA both flag)
        # Trigger B: drift detected AND short_mean is below EMA
        # Trigger C: legacy sudden-drop (kept for backward compatibility)
        regression_detected = (
            (static_triggered and ema_triggered)
            or (drift_triggered and ema_triggered)
            or sudden_drop
        )

        if not regression_detected:
            return

        # Determine which channel(s) fired for logging
        channels = []
        if static_triggered and ema_triggered:
            channels.append("static+EMA")
        if drift_triggered and ema_triggered:
            channels.append("drift+EMA")
        if sudden_drop:
            channels.append("sudden-drop")

        logger.warning(
            f"[Governance] Regression detected via [{', '.join(channels)}]! "
            f"short={short_mean:.3f}, static_base={self._static_mean:.3f}, "
            f"ema={self._ema_health:.3f}, slope={drift_slope:.4f}"
        )

        # --- Root Cause Analysis: Isolate culprit skill ---
        # Use the static baseline mean as the health threshold (not the polluted long_mean)
        reference_mean = self._static_mean if self._static_baseline_locked else long_mean
        bad_skills = {}
        for m, h in zip(short_window, short_seq):
            if h < reference_mean:
                for sk in m["selected_skills"]:
                    bad_skills[sk] = bad_skills.get(sk, 0) + 1

        if not bad_skills:
            return

        # Find the most frequent failing skill in the short window
        culprit_name = max(bad_skills.items(), key=lambda x: x[1])[0]
        all_skills = skill_registry.get_all_skills()
        culprit_skill = next((s for s in all_skills if s.name == culprit_name), None)

        if culprit_skill and culprit_skill.status == "core":
            now = datetime.datetime.utcnow().isoformat()

            # Multi-stage mitigation (unchanged pipeline)
            if culprit_skill.mitigation_stage == 0:
                logger.warning(f"[Governance] Stage 1 Mitigation: Reducing weight for {culprit_name}")
                culprit_skill.mitigation_stage = 1
                culprit_skill.priority *= 0.5
            elif culprit_skill.mitigation_stage == 1:
                logger.warning(f"[Governance] Stage 2 Mitigation: Restricting {culprit_name} to canary traffic")
                culprit_skill.mitigation_stage = 2
            elif culprit_skill.mitigation_stage == 2:
                logger.warning(f"[Governance] Stage 3 Mitigation: Rolling back {culprit_name}")
                culprit_skill.mitigation_stage = 3
                culprit_skill.status = "archived"
                culprit_skill.demoted_at = now

# Singleton instance
skill_metrics = SkillMetricsTracker()
