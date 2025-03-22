web: gunicorn --worker-class gevent -w 1 chat_server:app
worker: celery -A tasks worker --loglevel=info
