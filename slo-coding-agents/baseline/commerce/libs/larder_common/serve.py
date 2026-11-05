"""
Production entry point helper. Each service's __main__.py does:

    from larder_common.serve import run
    run(create_app, "catalog", threads=THREADS)

Serves the app with waitress. PORT comes from the environment (see
settings.py). Telemetry is flushed on SIGTERM/SIGINT.
"""
import os
import signal
import sys

from larder_common.log import get_logger

log = get_logger("serve")

# DP 2019: waitress defaults that we've always used
CHANNEL_TIMEOUT = 120
CONNECTION_LIMIT = 200


def _shutdown(signum, frame):
    from larder_common import telemetry

    log.info("signal %s received, shutting down", signum)
    try:
        telemetry.shutdown()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)


def _rss_mb():
    """Resident set size in MB (Linux: /proc; elsewhere: peak RSS)."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except (OSError, ValueError, IndexError):
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1048576.0 if sys.platform == "darwin" else peak / 1024.0


def _memory_guard(service, limit_mb, every_s=5.0):
    """INC-2187 (2024-09): the kernel OOM-killed cart mid-write and cart.db
    needed a restore. Since then a service with a limit in MEMORY_LIMITS_MB
    exits cleanly (code 70) when it goes over, and the supervisor restarts it."""
    import threading
    import time

    def watch():
        while True:
            time.sleep(every_s)
            rss = _rss_mb()
            if rss > limit_mb:
                log.critical("%s over its memory limit (%.0f MB > %d MB), exiting for a clean restart", service, rss,
                             limit_mb)
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(70)

    threading.Thread(target=watch, name="memory-guard", daemon=True).start()


def run(create_app, service, threads, before_serve=None, connection_limit=CONNECTION_LIMIT):
    from waitress import serve

    from larder_common import settings

    os.environ.setdefault("LARDER_SERVICE", service)
    settings.SERVICE = os.environ.get("LARDER_SERVICE", service)
    app = create_app()
    if before_serve is not None:
        before_serve(app)
    limit = getattr(settings, "MEMORY_LIMITS_MB", {}).get(service)
    if limit:
        _memory_guard(service, limit)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    log.info(
        "%s starting on %s:%s threads=%s env=%s engine=%s",
        service,
        settings.HOST,
        settings.PORT,
        threads,
        settings.ENV,
        settings.PRICING_ENGINE,
    )
    serve(
        app,
        host=settings.HOST,
        port=settings.PORT,
        threads=threads,
        ident="larder-%s" % service,
        channel_timeout=CHANNEL_TIMEOUT,
        connection_limit=connection_limit,
    )
