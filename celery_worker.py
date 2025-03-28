import os
import sys
import logging
from pathlib import Path

# Add the project root directory to the Python path
project_root = str(Path(__file__).parent.absolute())
if project_root not in sys.path:
    sys.path.append(project_root)

# Configure logging for Celery
logger = logging.getLogger("chat_server")
celery_logger = logging.getLogger("celery")
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
celery_logger.handlers = [handler]
celery_logger.setLevel(logging.INFO)
celery_logger.propagate = False

# Import the Celery app from tasks.py
from tasks import celery_app

if __name__ == "__main__":
    logger.info("Starting Celery worker...")
    # Start the Celery worker with event monitoring (-E) and info log level
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "-E",  # Enable events for monitoring
        "--queues=default,whatsapp",  # Specify the queues to listen to
        "--concurrency=9"  # Match the worker_concurrency setting
    ])
