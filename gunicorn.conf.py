# gunicorn.conf.py

# Bind the server to all network interfaces on port 8000
bind = "0.0.0.0:8000"

# Number of workers
# Using 3 workers to handle multiple requests at the same time
workers = 3

# Worker class
# Using 'gevent' for asynchronous handling of requests
worker_class = "gevent"

# Number of threads per worker
# Adding 50 threads per worker to handle more concurrent connections
threads = 50

# Timeout for worker processes
# Keeping at 120 seconds (2 minutes) since it’s already sufficient
timeout = 120
