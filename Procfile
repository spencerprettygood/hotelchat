web: gunicorn --worker-class gevent -w 1 chat_server:app
worker: cd /opt/render/project/src && PYTHONPATH=/opt/render/project/src celery -A tasks.celery_app worker --loglevel=info
