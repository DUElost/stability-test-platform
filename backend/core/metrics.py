"""
Prometheus Metrics for Stability Test Platform

Exposes key metrics for monitoring and alerting.
"""

import functools
import time
from typing import Callable, Optional

# Try to import prometheus_client, fallback to mock if not available
try:
    from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

    # Mock classes for when prometheus_client is not installed
    class _MockTimer:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    class _MockMetric:
        """Mock metric that accepts any constructor args and supports method chaining"""
        def __init__(self, *args, **kwargs):
            pass
        def inc(self, *args, **kwargs):
            pass
        def dec(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def labels(self, *args, **kwargs):
            return self
        def time(self):
            return _MockTimer()
        def info(self, *args, **kwargs):
            pass

    # Create mock classes that accept all Prometheus-specific kwargs
    class MockCounter(_MockMetric):
        pass

    class MockHistogram(_MockMetric):
        pass

    class MockGauge(_MockMetric):
        pass

    class MockInfo(_MockMetric):
        pass

    Counter = MockCounter
    Histogram = MockHistogram
    Gauge = MockGauge
    Info = MockInfo


def is_prometheus_available() -> bool:
    """Check if prometheus_client is available"""
    return PROMETHEUS_AVAILABLE


# ============================================================================
# Task Dispatch Metrics
# ============================================================================

task_dispatch_latency = Histogram(
    'stability_task_dispatch_latency_seconds',
    'Task dispatch latency in seconds',
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

task_dispatch_total = Counter(
    'stability_task_dispatch_total',
    'Total number of task dispatch attempts',
    ['status']  # success, failure, retry
) if PROMETHEUS_AVAILABLE else _MockMetric()

task_dispatch_errors = Counter(
    'stability_task_dispatch_errors_total',
    'Total number of task dispatch errors',
    ['error_type']  # device_unavailable, host_capacity, lock_failed, etc.
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Device Lease Metrics
# ============================================================================

device_lease_acquired = Counter(
    'stability_device_lease_acquired_total',
    'Total number of device leases acquired',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

device_lease_released = Counter(
    'stability_device_lease_released_total',
    'Total number of device leases released',
    ['reason']  # completed, failed, timeout, canceled
) if PROMETHEUS_AVAILABLE else _MockMetric()

device_lease_conflicts = Counter(
    'stability_device_lease_conflicts_total',
    'Total number of device lease conflicts'
) if PROMETHEUS_AVAILABLE else _MockMetric()

device_lease_duration = Histogram(
    'stability_device_lease_duration_seconds',
    'Duration of device leases in seconds',
    buckets=[60, 120, 300, 600, 900, 1800, 3600, 7200]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Task Run Metrics
# ============================================================================

task_run_duration = Histogram(
    'stability_task_run_duration_seconds',
    'Task run duration in seconds',
    ['task_type'],  # MONKEY, MTBF, DDR, GPU, STANDBY, AIMONKEY
    buckets=[60, 300, 600, 1800, 3600, 7200, 14400, 28800]
) if PROMETHEUS_AVAILABLE else _MockMetric()

task_run_total = Counter(
    'stability_task_run_total',
    'Total number of task runs',
    ['status', 'task_type']
) if PROMETHEUS_AVAILABLE else _MockMetric()

task_run_state_changes = Counter(
    'stability_task_run_state_changes_total',
    'Total number of task run state changes',
    ['from_state', 'to_state']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Host Metrics
# ============================================================================

host_online = Gauge(
    'stability_host_online',
    'Number of online hosts',
    ['status']  # online, offline, degraded
) if PROMETHEUS_AVAILABLE else _MockMetric()

host_heartbeat_latency = Histogram(
    'stability_host_heartbeat_latency_seconds',
    'Host heartbeat latency in seconds',
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

host_heartbeat_missed = Counter(
    'stability_host_heartbeat_missed_total',
    'Total number of missed host heartbeats',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Device Metrics
# ============================================================================

device_online = Gauge(
    'stability_device_online',
    'Number of online devices',
    ['status']  # online, offline, busy
) if PROMETHEUS_AVAILABLE else _MockMetric()

device_temperature = Gauge(
    'stability_device_temperature_celsius',
    'Device temperature in celsius',
    ['device_serial']
) if PROMETHEUS_AVAILABLE else _MockMetric()

device_battery_level = Gauge(
    'stability_device_battery_level_percent',
    'Device battery level in percent',
    ['device_serial']
) if PROMETHEUS_AVAILABLE else _MockMetric()

device_monitoring_updates = Counter(
    'stability_device_monitoring_updates_total',
    'Total number of device monitoring updates'
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Recycler Metrics
# ============================================================================

recycler_runs = Counter(
    'stability_recycler_runs_total',
    'Total number of recycler runs'
) if PROMETHEUS_AVAILABLE else _MockMetric()

recycler_timeouts = Counter(
    'stability_recycler_timeouts_total',
    'Total number of timeouts detected by recycler',
    ['timeout_type']  # dispatched, running, host, device_lock
) if PROMETHEUS_AVAILABLE else _MockMetric()

recycler_duration = Histogram(
    'stability_recycler_duration_seconds',
    'Recycler execution duration in seconds',
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Reconciler Metrics (ADR-0019 Phase 4a/4b)
# ============================================================================

reconciler_runs = Counter(
    'stability_reconciler_runs_total',
    'Total number of reconciler check invocations',
    ['check', 'outcome']  # check: expired_leases/stale_unknown/terminal_job_active_lease, outcome: success/error
) if PROMETHEUS_AVAILABLE else _MockMetric()

reconciler_actions = Counter(
    'stability_reconciler_actions_total',
    'Total number of actions taken by reconciler',
    ['action', 'reason']  # action: to_unknown/to_failed/release_lease, reason: lease_expired/unknown_grace_timeout/terminal_job_active_lease
) if PROMETHEUS_AVAILABLE else _MockMetric()

expired_active_leases_gauge = Gauge(
    'stability_expired_active_leases',
    'Number of expired but still ACTIVE (grace-held) leases',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

unknown_jobs_gauge = Gauge(
    'stability_unknown_jobs',
    'Number of UNKNOWN status jobs',
    ['reason']  # reason: lease_expired / host_timeout
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# API Metrics
# ============================================================================

api_requests = Counter(
    'stability_api_requests_total',
    'Total number of API requests',
    ['method', 'endpoint', 'status_code']
) if PROMETHEUS_AVAILABLE else _MockMetric()

api_request_duration = Histogram(
    'stability_api_request_duration_seconds',
    'API request duration in seconds',
    ['method', 'endpoint'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# SAQ (Async Task Queue) Metrics
# ============================================================================

saq_tasks_total = Counter(
    'stability_saq_tasks_total',
    'Total SAQ tasks processed',
    ['task_name', 'status']  # status: completed, failed, aborted
) if PROMETHEUS_AVAILABLE else _MockMetric()

saq_task_duration = Histogram(
    'stability_saq_task_duration_seconds',
    'SAQ task execution duration in seconds',
    ['task_name'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

saq_queue_depth = Gauge(
    'stability_saq_queue_depth',
    'Current SAQ queue depth',
    ['queue_name']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# SocketIO Metrics
# ============================================================================

socketio_connections = Gauge(
    'stability_socketio_connections_active',
    'Number of active SocketIO connections',
    ['namespace']  # /agent, /dashboard
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# APScheduler Metrics
# ============================================================================

apscheduler_job_runs = Counter(
    'stability_apscheduler_job_runs_total',
    'Total APScheduler job executions',
    ['job_name', 'outcome']  # outcome: success, error
) if PROMETHEUS_AVAILABLE else _MockMetric()

apscheduler_job_duration = Histogram(
    'stability_apscheduler_job_duration_seconds',
    'APScheduler job execution duration in seconds',
    ['job_name'],
    buckets=[0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Build Info
# ============================================================================

build_info = Info(
    'stability_build',
    'Build information'
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Decorators and Utilities
# ============================================================================

def timed(metric: Histogram):
    """Decorator to time function execution"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not PROMETHEUS_AVAILABLE:
                return func(*args, **kwargs)

            with metric.time():
                return func(*args, **kwargs)
        return wrapper
    return decorator


def count_exceptions(metric: Counter, exception_type: type = Exception):
    """Decorator to count exceptions"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exception_type as e:
                if PROMETHEUS_AVAILABLE:
                    metric.inc()
                raise
        return wrapper
    return decorator


def record_task_run_status(status: str, task_type: str):
    """Record a task run status change"""
    if PROMETHEUS_AVAILABLE:
        task_run_total.labels(status=status, task_type=task_type).inc()


def record_device_lease_acquired(host_id: int):
    """Record a device lease acquisition"""
    if PROMETHEUS_AVAILABLE:
        device_lease_acquired.labels(host_id=str(host_id)).inc()


def record_device_lease_released(reason: str):
    """Record a device lease release"""
    if PROMETHEUS_AVAILABLE:
        device_lease_released.labels(reason=reason).inc()


def record_socketio_connection(namespace: str, connected: bool):
    """Record SocketIO connection change (new framework metric)."""
    if not PROMETHEUS_AVAILABLE:
        return
    if connected:
        socketio_connections.labels(namespace=namespace).inc()
    else:
        socketio_connections.labels(namespace=namespace).dec()


def record_saq_task(task_name: str, status: str, duration: float):
    """Record a completed SAQ task with its outcome and duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    saq_tasks_total.labels(task_name=task_name, status=status).inc()
    saq_task_duration.labels(task_name=task_name).observe(duration)


def record_apscheduler_job(job_name: str, outcome: str, duration: float):
    """Record an APScheduler job execution with its outcome and duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    apscheduler_job_runs.labels(job_name=job_name, outcome=outcome).inc()
    apscheduler_job_duration.labels(job_name=job_name).observe(duration)


def record_api_request(method: str, endpoint: str, status_code: int, duration: float):
    """Record an API request"""
    if not PROMETHEUS_AVAILABLE:
        return

    api_requests.labels(
        method=method,
        endpoint=endpoint,
        status_code=str(status_code)
    ).inc()

    api_request_duration.labels(
        method=method,
        endpoint=endpoint
    ).observe(duration)


def get_metrics_response():
    """Generate Prometheus metrics response"""
    if not PROMETHEUS_AVAILABLE:
        return b"# Prometheus client not installed\n", "text/plain"

    return generate_latest(), CONTENT_TYPE_LATEST


def init_build_info(version: str = "unknown", commit: str = "unknown"):
    """Initialize build info metrics"""
    if PROMETHEUS_AVAILABLE:
        build_info.info({
            'version': version,
            'commit': commit,
        })
