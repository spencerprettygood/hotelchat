#!/usr/bin/env python3
"""
Performance monitoring utility for HotelChat application.

This utility provides real-time monitoring of:
- OpenAI API usage and performance
- SocketIO connections and events
- Database query performance
- Redis cache hit rates
- System resource usage

It can be used as a standalone tool or embedded in the Flask application.
"""

import os
import time
import json
import logging
import threading
import datetime
from collections import defaultdict, deque
import psutil
import redis

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("performance_monitor")

class PerformanceMetricCollector:
    """Collects and stores performance metrics."""
    
    def __init__(self, window_size=100):
        """Initialize the metrics collector."""
        self.window_size = window_size
        self.metrics = {
            'openai_api': {
                'request_times': deque(maxlen=window_size),
                'token_usage': deque(maxlen=window_size),
                'errors': defaultdict(int),
                'requests_count': 0,
                'last_request_time': None
            },
            'socketio': {
                'connection_count': 0,
                'message_count': 0,
                'errors': defaultdict(int),
                'events': defaultdict(int)
            },
            'database': {
                'query_times': deque(maxlen=window_size),
                'connection_errors': 0,
                'query_count': 0
            },
            'redis': {
                'hits': 0,
                'misses': 0,
                'errors': 0,
                'latency': deque(maxlen=window_size)
            },
            'system': {
                'cpu_percent': deque(maxlen=window_size),
                'memory_percent': deque(maxlen=window_size),
                'start_time': time.time(),
                'last_update': time.time()
            }
        }
        
        self.monitoring_active = False
        self._monitor_thread = None
        
    def record_openai_request(self, elapsed_time_ms, token_count=0, error=None):
        """Record metrics for an OpenAI API request."""
        metrics = self.metrics['openai_api']
        metrics['request_times'].append(elapsed_time_ms)
        metrics['token_usage'].append(token_count)
        metrics['requests_count'] += 1
        metrics['last_request_time'] = datetime.datetime.now().isoformat()
        
        if error:
            error_type = type(error).__name__
            metrics['errors'][error_type] += 1
            
    def record_socketio_event(self, event_type, error=None):
        """Record a SocketIO event."""
        metrics = self.metrics['socketio']
        metrics['events'][event_type] += 1
        
        if event_type == 'connect':
            metrics['connection_count'] += 1
        elif event_type == 'disconnect':
            metrics['connection_count'] = max(0, metrics['connection_count'] - 1)
        elif event_type == 'message':
            metrics['message_count'] += 1
            
        if error:
            error_type = type(error).__name__
            metrics['errors'][error_type] += 1
            
    def record_db_query(self, elapsed_time_ms, error=None):
        """Record a database query."""
        metrics = self.metrics['database']
        metrics['query_times'].append(elapsed_time_ms)
        metrics['query_count'] += 1
        
        if error:
            metrics['connection_errors'] += 1
            
    def record_redis_operation(self, operation, elapsed_time_ms, hit=None, error=None):
        """Record a Redis cache operation."""
        metrics = self.metrics['redis']
        metrics['latency'].append(elapsed_time_ms)
        
        if hit is True:
            metrics['hits'] += 1
        elif hit is False:
            metrics['misses'] += 1
            
        if error:
            metrics['errors'] += 1
            
    def start_monitoring(self, interval=5):
        """Start the background monitoring thread."""
        if self.monitoring_active:
            logger.warning("Monitoring is already active")
            return False
            
        self.monitoring_active = True
        self._monitor_thread = threading.Thread(
            target=self._background_monitor, 
            args=(interval,),
            daemon=True
        )
        self._monitor_thread.start()
        logger.info(f"Started background monitoring with {interval}s interval")
        return True
        
    def stop_monitoring(self):
        """Stop the background monitoring thread."""
        self.monitoring_active = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5.0)
            logger.info("Stopped background monitoring")
            
    def _background_monitor(self, interval):
        """Background thread to collect system metrics."""
        logger.info("Background monitoring thread started")
        
        while self.monitoring_active:
            try:
                # Collect system metrics
                system_metrics = self.metrics['system']
                system_metrics['cpu_percent'].append(psutil.cpu_percent())
                system_metrics['memory_percent'].append(psutil.virtual_memory().percent)
                system_metrics['last_update'] = time.time()
                
                # Check Redis connection if available
                if hasattr(self, 'redis_client'):
                    try:
                        start = time.time()
                        ping_result = self.redis_client.ping()
                        latency = (time.time() - start) * 1000
                        self.metrics['redis']['latency'].append(latency)
                    except Exception as e:
                        logger.error(f"Redis ping error: {str(e)}")
                        self.metrics['redis']['errors'] += 1
            
            except Exception as e:
                logger.error(f"Error in background monitoring: {str(e)}")
                
            time.sleep(interval)
            
        logger.info("Background monitoring thread stopped")
        
    def get_summary(self):
        """Get a summary of the collected metrics."""
        summary = {
            'timestamp': datetime.datetime.now().isoformat(),
            'uptime_seconds': time.time() - self.metrics['system']['start_time'],
            'openai': {
                'total_requests': self.metrics['openai_api']['requests_count'],
                'avg_response_time_ms': self._safe_avg(self.metrics['openai_api']['request_times']),
                'avg_token_usage': self._safe_avg(self.metrics['openai_api']['token_usage']),
                'error_count': sum(self.metrics['openai_api']['errors'].values())
            },
            'socketio': {
                'current_connections': self.metrics['socketio']['connection_count'],
                'total_messages': self.metrics['socketio']['message_count'],
                'event_counts': dict(self.metrics['socketio']['events']),
                'error_count': sum(self.metrics['socketio']['errors'].values())
            },
            'database': {
                'total_queries': self.metrics['database']['query_count'],
                'avg_query_time_ms': self._safe_avg(self.metrics['database']['query_times']),
                'connection_errors': self.metrics['database']['connection_errors']
            },
            'redis': {
                'hit_rate': self._safe_rate(self.metrics['redis']['hits'], 
                               self.metrics['redis']['hits'] + self.metrics['redis']['misses']),
                'avg_latency_ms': self._safe_avg(self.metrics['redis']['latency']),
                'errors': self.metrics['redis']['errors']
            },
            'system': {
                'avg_cpu_percent': self._safe_avg(self.metrics['system']['cpu_percent']),
                'avg_memory_percent': self._safe_avg(self.metrics['system']['memory_percent']),
                'current_cpu_percent': self.metrics['system']['cpu_percent'][-1] if self.metrics['system']['cpu_percent'] else None,
                'current_memory_percent': self.metrics['system']['memory_percent'][-1] if self.metrics['system']['memory_percent'] else None
            }
        }
        return summary
        
    def reset_metrics(self):
        """Reset all collected metrics."""
        for key in self.metrics:
            if isinstance(self.metrics[key], dict):
                for subkey in self.metrics[key]:
                    if isinstance(self.metrics[key][subkey], (deque, defaultdict)):
                        self.metrics[key][subkey].clear()
                    elif isinstance(self.metrics[key][subkey], (int, float)):
                        self.metrics[key][subkey] = 0
            
        # Keep start time
        self.metrics['system']['start_time'] = time.time()
        logger.info("Performance metrics reset")
        
    def _safe_avg(self, values):
        """Safely calculate an average, handling empty collections."""
        return sum(values) / len(values) if values else 0
        
    def _safe_rate(self, numerator, denominator):
        """Safely calculate a rate, handling zero denominators."""
        if denominator == 0:
            return 0
        return numerator / denominator
        
    def set_redis_client(self, client):
        """Set the Redis client for monitoring."""
        self.redis_client = client

# Create a singleton instance for the application
metrics_collector = PerformanceMetricCollector()

def openai_metric_decorator(func):
    """Decorator to track OpenAI API calls."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        error = None
        token_count = 0
        
        try:
            result = await func(*args, **kwargs)
            
            # Extract token count if available
            if hasattr(result, 'usage') and hasattr(result.usage, 'total_tokens'):
                token_count = result.usage.total_tokens
                
            return result
            
        except Exception as e:
            error = e
            raise
            
        finally:
            elapsed_time_ms = (time.time() - start_time) * 1000
            metrics_collector.record_openai_request(elapsed_time_ms, token_count, error)
            
    return wrapper

def db_metric_decorator(func):
    """Decorator to track database queries."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        error = None
        
        try:
            result = func(*args, **kwargs)
            return result
            
        except Exception as e:
            error = e
            raise
            
        finally:
            elapsed_time_ms = (time.time() - start_time) * 1000
            metrics_collector.record_db_query(elapsed_time_ms, error)
            
    return wrapper

def create_dashboard_blueprint():
    """Create a Flask blueprint for the monitoring dashboard."""
    try:
        from flask import Blueprint, render_template, jsonify
        
        bp = Blueprint('performance_dashboard', __name__, url_prefix='/admin')
        
        @bp.route('/dashboard')
        def dashboard():
            """Render the monitoring dashboard."""
            return render_template('performance_dashboard.html')
            
        @bp.route('/metrics')
        def get_metrics():
            """Return current metrics as JSON."""
            return jsonify(metrics_collector.get_summary())
            
        @bp.route('/metrics/reset', methods=['POST'])
        def reset_metrics():
            """Reset all metrics."""
            metrics_collector.reset_metrics()
            return jsonify({"status": "success", "message": "Metrics reset"})
            
        return bp
        
    except ImportError:
        logger.error("Flask not available, cannot create dashboard blueprint")
        return None

def main():
    """Run as a standalone monitoring service."""
    import argparse
    
    parser = argparse.ArgumentParser(description="HotelChat Performance Monitor")
    parser.add_argument("--redis-url", help="Redis URL for monitoring")
    parser.add_argument("--interval", type=int, default=5, help="Monitoring interval in seconds")
    parser.add_argument("--output-file", help="Write metrics to this file periodically")
    
    args = parser.parse_args()
    
    # Set up Redis if provided
    if args.redis_url:
        try:
            redis_client = redis.Redis.from_url(args.redis_url)
            metrics_collector.set_redis_client(redis_client)
            logger.info(f"Connected to Redis at {args.redis_url}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
    
    # Start monitoring
    metrics_collector.start_monitoring(interval=args.interval)
    
    try:
        logger.info("Press Ctrl+C to exit...")
        
        while True:
            summary = metrics_collector.get_summary()
            
            # Print summary to console
            print("\n" + "=" * 50)
            print(f"PERFORMANCE SUMMARY - {summary['timestamp']}")
            print(f"Uptime: {summary['uptime_seconds']:.1f} seconds")
            print(f"OpenAI: {summary['openai']['total_requests']} requests, {summary['openai']['avg_response_time_ms']:.2f}ms avg")
            print(f"SocketIO: {summary['socketio']['current_connections']} connections, {summary['socketio']['total_messages']} messages")
            print(f"Database: {summary['database']['total_queries']} queries, {summary['database']['avg_query_time_ms']:.2f}ms avg")
            print(f"Redis: {summary['redis']['hit_rate']*100:.1f}% hit rate, {summary['redis']['avg_latency_ms']:.2f}ms avg")
            print(f"System: CPU {summary['system']['current_cpu_percent']}%, Memory {summary['system']['current_memory_percent']}%")
            
            # Write to file if specified
            if args.output_file:
                try:
                    with open(args.output_file, 'w') as f:
                        json.dump(summary, f, indent=2)
                except Exception as e:
                    logger.error(f"Failed to write metrics to file: {e}")
            
            time.sleep(args.interval)
            
    except KeyboardInterrupt:
        logger.info("Stopping monitoring...")
        
    finally:
        metrics_collector.stop_monitoring()

if __name__ == "__main__":
    main()
