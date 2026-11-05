# OWNED BY PLATFORM TEAM - do not modify without #platform approval
#
# OpenTelemetry bootstrap for every commerce service. Configured ONLY from the
# standard OTEL_* environment variables:
#
#   OTEL_EXPORTER_OTLP_ENDPOINT   (unset => telemetry is a no-op)
#   OTEL_EXPORTER_OTLP_HEADERS
#   OTEL_EXPORTER_OTLP_PROTOCOL   (http/protobuf only)
#   OTEL_SERVICE_NAME
#   OTEL_RESOURCE_ATTRIBUTES
#   OTEL_METRIC_EXPORT_INTERVAL   (ms)
#
# Metric names are part of the platform dashboards/alerts contract. Never set
# a `unit` on an instrument (the backend appends it to the metric name).
# Service identity comes from the resource (service.name), never a label.
#
# Contact: #platform  (owner: platform-observability)

import logging
import os
import threading

from opentelemetry import metrics, trace

_log = logging.getLogger("larder.telemetry")

_lock = threading.Lock()
_initialised = False
_enabled = False
_providers = {}

_HISTOGRAM_BUCKETS_SECONDS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 30, 60]

# resource defaults; anything in OTEL_RESOURCE_ATTRIBUTES / OTEL_SERVICE_NAME wins
_RESOURCE_DEFAULTS = {
    "service.namespace": "larder",
    "deployment.environment": "bench",
    "cx.application.name": "larder",
    "larder.repo": "commerce",
}

# instruments (created in _create_instruments; API no-op meter when disabled)
_checkouts = None
_coupon_applications = None
_search_cache_flushes = None
_http_client_duration = None

# observable gauge sources, registered by app code. key -> zero-arg callable
_gauge_sources = {}


def enabled():
    return _enabled


def init_telemetry(app=None, service_name=None):
    """Initialise providers + auto-instrumentation. Safe to call twice."""
    global _initialised, _enabled
    with _lock:
        if not _initialised:
            _enabled = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
            if _enabled:
                try:
                    _setup_providers(service_name)
                    _instrument_libraries()
                except Exception:  # never take the app down for telemetry
                    _log.exception("telemetry setup failed, continuing without it")
                    _enabled = False
            _create_instruments()
            _initialised = True
    if app is not None and _enabled and not getattr(app, "_otel_instrumented", False):
        try:
            from opentelemetry.instrumentation.flask import FlaskInstrumentor

            FlaskInstrumentor().instrument_app(app, excluded_urls="healthz")
            app._otel_instrumented = True
        except Exception:
            _log.exception("flask instrumentation failed")


def _resource(service_name):
    from opentelemetry.sdk.resources import Resource

    attrs = dict(_RESOURCE_DEFAULTS)
    name = os.environ.get("OTEL_SERVICE_NAME") or service_name or os.environ.get("LARDER_SERVICE")
    if name:
        attrs["service.name"] = name
        attrs["cx.subsystem.name"] = name
    run_id = os.environ.get("LARDER_RUN_ID")
    if run_id:
        attrs["bench.run_id"] = run_id
    # Resource.create() merges OTEL_SERVICE_NAME + OTEL_RESOURCE_ATTRIBUTES on top
    return Resource.create(attrs)


def _setup_providers(service_name=None):
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = _resource(service_name)

    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)

    interval_ms = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "10000"))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval_ms)
    views = [
        View(
            instrument_name="*_seconds",
            aggregation=ExplicitBucketHistogramAggregation(boundaries=_HISTOGRAM_BUCKETS_SECONDS),
        )
    ]
    mp = MeterProvider(resource=resource, metric_readers=[reader], views=views)
    metrics.set_meter_provider(mp)

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(lp)
    # bridge stdlib logging (and therefore the legacy logger) into OTel logs
    handler = LoggingHandler(level=logging.INFO, logger_provider=lp)
    logging.getLogger().addHandler(handler)

    _providers.update(tracer=tp, meter=mp, logger=lp)


def _instrument_libraries():
    # W3C tracecontext is the default propagator, which is what ops (Node) uses
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor

    RequestsInstrumentor().instrument()
    SQLite3Instrumentor().instrument()


def _create_instruments():
    global _checkouts, _coupon_applications, _search_cache_flushes, _http_client_duration
    meter = metrics.get_meter("larder.commerce")
    _checkouts = meter.create_counter("commerce_checkouts", description="checkout outcomes")
    _coupon_applications = meter.create_counter(
        "commerce_coupon_applications", description="coupon application attempts by result"
    )
    _search_cache_flushes = meter.create_counter(
        "commerce_search_cache_flushes", description="search cache safety-valve flushes"
    )
    _http_client_duration = meter.create_histogram(
        "commerce_http_client_duration_seconds", description="outbound service call duration (per attempt)"
    )
    meter.create_observable_gauge(
        "commerce_search_cache_entries",
        callbacks=[_gauge_callback("search_cache_entries")],
        description="entries in the search results cache",
    )
    meter.create_observable_gauge(
        "commerce_busy_workers",
        callbacks=[_gauge_callback("busy_workers")],
        description="requests currently being handled",
    )
    meter.create_observable_gauge(
        "commerce_active_carts",
        callbacks=[_gauge_callback("active_carts")],
        description="carts held in memory by the cart service",
    )


def _gauge_callback(key):
    from opentelemetry.metrics import Observation

    def _cb(options):
        fn = _gauge_sources.get(key)
        if fn is None:
            return []
        try:
            return [Observation(fn())]
        except Exception:
            return []

    return _cb


def register_gauge(key, fn):
    _gauge_sources[key] = fn


def tracer():
    return trace.get_tracer("larder.commerce")


# --- helpers used by app code -----------------------------------------------


def record_checkout(outcome):
    if _checkouts is not None:
        _checkouts.add(1, {"outcome": outcome})


def record_coupon(result):
    if _coupon_applications is not None:
        _coupon_applications.add(1, {"result": result})


def record_search_flush():
    if _search_cache_flushes is not None:
        _search_cache_flushes.add(1)


def touch_search_flushes():
    # export the series from startup (search service only)
    if _search_cache_flushes is not None:
        _search_cache_flushes.add(0)


def record_http_client(target, method, seconds):
    if _http_client_duration is not None:
        _http_client_duration.record(seconds, {"target": target, "method": method})


def shutdown(timeout_ms=5000):
    """Flush + shut down providers. Called on SIGTERM / exit."""
    if not _providers:
        return
    for name in ("tracer", "meter", "logger"):
        p = _providers.get(name)
        if p is None:
            continue
        try:
            if hasattr(p, "force_flush"):
                p.force_flush(timeout_millis=timeout_ms)
            p.shutdown()
        except Exception:
            pass
    _providers.clear()
