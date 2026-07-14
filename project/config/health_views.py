"""
Health and readiness endpoints.

`/healthz/`  — liveness; returns 200 if the process is up.
`/readyz/`   — readiness; returns 200 only if DB + Redis are reachable.

Load balancers should hit `/healthz/` for liveness (fast, no deps)
and `/readyz/` for readiness (slow, checks deps). If `readyz` fails,
the LB stops routing traffic to this instance but doesn't kill it.
"""

from django.db import connections
from django.http import JsonResponse

from section2_queue.rate_limiter import _get_redis


def healthz(request):
    """Liveness — always 200 if the process can serve the request."""
    return JsonResponse({"status": "ok"})


def readyz(request):
    """Readiness — check DB + Redis."""
    checks = {}

    # DB
    try:
        connections["default"].cursor().execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"fail: {exc}"
        return JsonResponse({"status": "fail", "checks": checks},
                            status=503)

    # Redis
    try:
        _get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"fail: {exc}"
        return JsonResponse({"status": "fail", "checks": checks},
                            status=503)

    return JsonResponse({"status": "ok", "checks": checks})
