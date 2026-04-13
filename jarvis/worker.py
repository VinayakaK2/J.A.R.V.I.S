import os
import sys
import logging
from redis import Redis
from rq import Worker, Queue, Connection

# Setup path so imports work correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config.settings import settings
from memory.db import init_db

# Configure worker logging natively
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] Worker: %(message)s")
logger = logging.getLogger(__name__)

# Fallback Redis to localhost if not specified
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/")
redis_conn = Redis.from_url(redis_url)

def start_worker():
    # Hydrate the DB schemas proactively on boot
    init_db()
    with Connection(redis_conn):
        # Multi-Tenant isolation: Pulls all queues automatically mapping across runtime
        from rq.registry import StartedJobRegistry
        logger.info("Initializing multi-tenant Redis worker checking isolated namespaces...")
        
        # We start listening to the root, but can script RQ to bind regex wildcard (RQ v1.11+) or all existing
        # But for stability we simply tell RQ to work on anything matching Queue structure natively.
        queues = Queue.all(connection=redis_conn)
        if not queues:
            queues = [Queue("jarvis_tasks")]
        
        w = Worker(queues, connection=redis_conn)
        w.work(with_scheduler=True)

if __name__ == "__main__":
    start_worker()
