web: gunicorn chat_server:app --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --config gunicorn.conf.py
worker: celery -A tasks worker -l INFO -Q default,whatsapp --concurrency=3
