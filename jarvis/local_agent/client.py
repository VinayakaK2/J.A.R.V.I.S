import os
import sys
import time
import json
import logging
import requests

# Link local dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from local_agent.executor import execute_local_task

# Configurable constants for deployment
CLOUD_URL = os.getenv("JARVIS_CLOUD_URL", "http://localhost:8000")
JWT_TOKEN = os.getenv("JARVIS_AGENT_JWT", "")

# Adaptive Polling Controls
IDLE_POLL_INTERVAL = 5.0
ACTIVE_POLL_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 10.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [LocalClient]: %(message)s")
logger = logging.getLogger(__name__)

class LocalAgentClient:
    def __init__(self, cloud_url: str, token: str):
        self.cloud_url = cloud_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.last_heartbeat = 0.0
        self.current_poll_interval = ACTIVE_POLL_INTERVAL
        
        if not token:
            logger.error("JARVIS_AGENT_JWT environment variable not set. Exiting.")
            sys.exit(1)

    def heartbeat(self):
        """Pings the Cloud Brain to announce active presence."""
        now = time.time()
        if now - self.last_heartbeat >= HEARTBEAT_INTERVAL:
            try:
                res = requests.post(f"{self.cloud_url}/agent/heartbeat", headers=self.headers, timeout=5)
                res.raise_for_status()
                self.last_heartbeat = now
            except requests.RequestException as e:
                logger.warning(f"Heartbeat failed: {e}. The cloud may think we are offline.")

    def submit_result(self, task_id: int, status: str, result: str):
        """Pushes the Local Executor results back up to the Cloud Brain."""
        payload = {
            "task_id": task_id,
            "status": status,
            "result": result
        }
        # Implement built-in client failure recovery retries
        for attempt in range(1, 4):
            try:
                res = requests.post(f"{self.cloud_url}/tasks/result", json=payload, headers=self.headers, timeout=10)
                res.raise_for_status()
                logger.info(f"Result for task {task_id} successfully dispatched.")
                return
            except requests.RequestException as e:
                logger.warning(f"Failed to submit result for {task_id} (attempt {attempt}/3): {e}")
                time.sleep(2)
        
        logger.error(f"FATAL: Could not submit result for {task_id} after 3 attempts.")

    def run_forever(self):
        """Main adaptive polling loop over HTTP."""
        logger.info(f"Local Agent Client active. Bound to Cloud Brain: {self.cloud_url}")
        
        while True:
            self.heartbeat()
            
            try:
                # 1. Fetch Task
                res = requests.get(f"{self.cloud_url}/tasks/poll", headers=self.headers, timeout=10)
                res.raise_for_status()
                data = res.json()
                task = data.get("task")
                
                if not task:
                    # Idle branch
                    self.current_poll_interval = IDLE_POLL_INTERVAL
                    time.sleep(self.current_poll_interval)
                    continue
                
                # Active branch: immediately accelerate polling to prevent blocking the Cloud
                self.current_poll_interval = ACTIVE_POLL_INTERVAL
                
                # 2. Execute Task
                task_id = task["id"]
                action  = task["action"]
                params  = task["params"]
                
                logger.info(f"Claimed Task {task_id}: {action} (Plan: {task['plan_id']})")
                
                status = "failed"
                result_str = None
                
                try:
                    result_str = execute_local_task(action, params)
                    status = "success"
                except Exception as e:
                    logger.error(f"Task {task_id} execution strictly failed: {e}")
                    result_str = str(e)
                
                # 3. Submit Results
                self.submit_result(task_id, status, result_str)
                
                # No sleep here on success; poll instantly again to drain queue
                
            except requests.RequestException as e:
                logger.error(f"Polling connection error: {e}. Backing off.")
                time.sleep(IDLE_POLL_INTERVAL)
            except Exception as e:
                logger.exception(f"Unexpected local client loop fault: {e}")
                time.sleep(IDLE_POLL_INTERVAL)

if __name__ == "__main__":
    client = LocalAgentClient(CLOUD_URL, JWT_TOKEN)
    client.run_forever()
