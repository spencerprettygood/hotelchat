from celery import Celery
import os

BROKER_URL = os.getenv('REDIS_URL', 'redis://red-cvfhn5nnoe9s73bhmct0:6379')

celery_app = Celery('tasks', broker=BROKER_URL, backend=BROKER_URL)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    worker_concurrency=9,
    task_routes={
        'tasks.process_incoming_message': {'queue': 'whatsapp'},
        'tasks.send_whatsapp_message_task': {'queue': 'whatsapp'}
    },
    task_default_queue='default',
    broker_connection_retry_on_startup=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True
)
