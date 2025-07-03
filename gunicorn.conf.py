import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
backlog = 2048

# Worker processes
workers = int(os.environ.get('WEB_CONCURRENCY', multiprocessing.cpu_count() * 2 + 1))
worker_class = 'geventwebsocket.gunicorn.workers.GeventWebSocketWorker'
worker_connections = 1000
timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 50

# Server mechanics
daemon = False
raw_env = []
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Logging
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
accesslog = '-'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)s'

# Process naming
proc_name = None
