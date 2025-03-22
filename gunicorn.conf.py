# gunicorn.conf.py
bind = "0.0.0.0:8000"
workers = 1
worker_class = "gevent"
timeout = 120  # Increase timeout to 120 seconds (2 minutes)
