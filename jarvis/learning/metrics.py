import logging
from memory.db import SessionLocal, ToolMetric

logger = logging.getLogger(__name__)

class MetricsEngine:
    """Tracks neural adaptation by weighting the historical success rates of tools."""
    
    def log_tool_success(self, tool_name: str):
        self._update_metric(tool_name, success=True)
        
    def log_tool_failure(self, tool_name: str):
        self._update_metric(tool_name, success=False)
        
    def _update_metric(self, tool_name: str, success: bool):
        try:
            with SessionLocal() as db:
                metric = db.query(ToolMetric).filter(ToolMetric.tool_name == tool_name).first()
                if not metric:
                    metric = ToolMetric(tool_name=tool_name)
                    db.add(metric)
                
                if success:
                    metric.success_count += 1
                else:
                    metric.fail_count += 1
                    
                db.commit()
        except Exception as e:
            logger.error(f"[MetricsEngine] Failed to update metrics for {tool_name}: {e}")

    def get_tool_performance_bias(self, tool_name: str) -> str:
        """Returns string bias instructions for the Planner based on historical success."""
        try:
            with SessionLocal() as db:
                metric = db.query(ToolMetric).filter(ToolMetric.tool_name == tool_name).first()
                if not metric or (metric.success_count + metric.fail_count) < 5:
                    return ""
                
                total = metric.success_count + metric.fail_count
                success_rate = metric.success_count / total
                
                if success_rate < 0.3:
                    return f"WARNING: Tool '{tool_name}' has a high failure rate ({success_rate*100:.0f}% success). Use strictly as fallback."
                elif success_rate > 0.8:
                    return f"Tool '{tool_name}' is historically highly reliable."
                return ""
        except:
            return ""

# Global singleton
metrics = MetricsEngine()
