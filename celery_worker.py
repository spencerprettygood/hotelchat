# celery_worker.py
import gevent
from gevent import monkey

# Apply monkey-patching before any other imports
monkey.patch_all()

# Now import the Celery app
from tasks import celery_app

# Configure logging
import logging
logger = logging.getLogger("chat_server")
logger.info("✅ Applied gevent monkey-patching in celery_worker.py")

if __name__ == "__main__":
    # Start the Celery worker
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        f"--concurrency={celery_app.conf.worker_concurrency}"
    ])
