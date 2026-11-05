"""OTel metrics + logs helper for harness-owned processes (mesh, supervisor).

Configured from the standard OTEL_* env vars (OTEL_EXPORTER_OTLP_ENDPOINT,
OTEL_EXPORTER_OTLP_HEADERS, OTEL_METRIC_EXPORT_INTERVAL, ...). If
OTEL_EXPORTER_OTLP_ENDPOINT is unset everything is a cheap no-op.

Conventions:
  * resource attrs: service.name, service.namespace=larder,
    deployment.environment=bench, cx.application.name=larder,
    cx.subsystem.name=<subsystem>, bench.run_id=$LARDER_RUN_ID,
    larder.repo=<repo> (services only)
  * NEVER set `unit` on instruments (Coralogix appends it to the name).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

log = logging.getLogger("harness.otel")

_SEVERITY = {
    "DEBUG": 5,
    "INFO": 9,
    "WARN": 13,
    "WARNING": 13,
    "ERROR": 17,
    "FATAL": 21,
}


class _NoopInstrument:
    def add(self, *_a: Any, **_k: Any) -> None:  # counter
        pass

    def record(self, *_a: Any, **_k: Any) -> None:  # histogram
        pass


_NOOP = _NoopInstrument()


def resource_attributes(service_name: str, subsystem: str | None = None, repo: str | None = None) -> dict[str, str]:
    attrs = {
        "service.name": service_name,
        "service.namespace": "larder",
        "deployment.environment": "bench",
        "cx.application.name": "larder",
        "cx.subsystem.name": subsystem or service_name,
        "bench.run_id": os.environ.get("LARDER_RUN_ID", "local"),
    }
    if repo:
        attrs["larder.repo"] = repo
    return attrs


def resource_attributes_env(service_name: str, repo: str | None = None) -> str:
    """OTEL_RESOURCE_ATTRIBUTES string for a child service."""
    return ",".join(f"{k}={v}" for k, v in resource_attributes(service_name, repo=repo).items())


class Telemetry:
    """Thin facade; every method is safe to call when telemetry is disabled."""

    def __init__(self, service_name: str, subsystem: str | None = None, *, traces: bool = False):
        self.service_name = service_name
        self.subsystem = subsystem or service_name
        self.enabled = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())
        self._meter = None
        self._logger = None
        self._mp = None
        self._lp = None
        self._tp = None
        if not self.enabled:
            return
        try:
            self._setup(traces)
        except Exception:  # never let telemetry break the harness
            log.exception("OTel setup failed; continuing with telemetry disabled")
            self.enabled = False

    def _setup(self, traces: bool) -> None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create(resource_attributes(self.service_name, self.subsystem))
        interval = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "10000"))
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=interval)
        self._mp = MeterProvider(resource=resource, metric_readers=[reader])
        self._meter = self._mp.get_meter("larder.harness." + self.service_name)

        self._lp = LoggerProvider(resource=resource)
        self._lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        self._logger = self._lp.get_logger("larder.harness." + self.service_name)

        if traces:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            self._tp = TracerProvider(resource=resource)
            self._tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(self._tp)

    # -- metrics -----------------------------------------------------------
    def counter(self, name: str, description: str = ""):
        if not self._meter:
            return _NOOP
        return self._meter.create_counter(name, description=description)

    def histogram(self, name: str, buckets: Sequence[float] | None = None, description: str = ""):
        if not self._meter:
            return _NOOP
        return self._meter.create_histogram(
            name,
            description=description,
            explicit_bucket_boundaries_advisory=list(buckets) if buckets else None,
        )

    def gauge(
        self,
        name: str,
        callback: Callable[[], Iterable[tuple[float, Mapping[str, str]]]],
        description: str = "",
    ) -> None:
        """Observable gauge; callback returns [(value, attributes), ...]."""
        if not self._meter:
            return
        from opentelemetry.metrics import Observation

        def _cb(_options):
            try:
                return [Observation(v, dict(a)) for v, a in callback()]
            except Exception:
                log.exception("gauge callback %s failed", name)
                return []

        self._meter.create_observable_gauge(name, callbacks=[_cb], description=description)

    # -- logs --------------------------------------------------------------
    def log(
        self,
        body: str | Mapping[str, Any],
        severity: str = "INFO",
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._logger:
            return
        from opentelemetry._logs import SeverityNumber

        if not isinstance(body, str):
            body = json.dumps(body, separators=(",", ":"), default=str)
        sev = severity.upper()
        attrs = {k: v for k, v in (attributes or {}).items() if v is not None and isinstance(v, (str, bool, int, float))}
        try:
            self._logger.emit(
                timestamp=time.time_ns(),
                severity_text=sev,
                severity_number=SeverityNumber(_SEVERITY.get(sev, 9)),
                body=body,
                attributes=attrs,
            )
        except Exception:
            log.debug("log emit failed", exc_info=True)

    def shutdown(self) -> None:
        for p in (self._mp, self._lp, self._tp):
            if p is None:
                continue
            try:
                if hasattr(p, "force_flush"):
                    p.force_flush(5000)
                p.shutdown()
            except Exception:
                log.debug("otel shutdown failed", exc_info=True)


def setup(service_name: str, subsystem: str | None = None, *, traces: bool = False) -> Telemetry:
    return Telemetry(service_name, subsystem, traces=traces)
