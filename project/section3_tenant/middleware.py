"""
Section 3 — TenantMiddleware.

Responsibilities
----------------
1. Extract the tenant ID from the request (here: the ``X-Tenant-ID``
   header; in production you'd typically decode it from a JWT, but the
   header form makes the contract explicit and testable).
2. Resolve the header to a ``Tenant`` row, then bind it to the
   ``contextvars.ContextVar`` defined in ``context.py``.
3. ALWAYS reset the context after the response — even if the view
   raised. ``ContextVar.reset`` requires the original token, so we
   capture it on entry and pass it on exit.

Async-safety
------------
This middleware is written as a synchronous ``__call__`` callable (the
modern Django middleware contract) — Django runs sync middleware inside
``sync_to_async`` when running under ASGI, and crucially each request
gets its own copy of the ContextVar. That's why we use ``contextvars``
and not ``threading.local``.
"""

from __future__ import annotations

from django.http import JsonResponse

from .context import reset_current_tenant, set_current_tenant
from .models import Tenant


class TenantMiddleware:
    """Bind the current tenant to a ContextVar for the request lifecycle."""

    header_name = "HTTP_X_TENANT_ID"  # Django exposes headers as META[HTTP_*]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = None
        request.tenant = None

        raw = request.META.get(self.header_name)
        if raw:
            try:
                tenant = Tenant.objects.get(slug=raw)
            except Tenant.DoesNotExist:
                return JsonResponse(
                    {"detail": f"Unknown tenant: {raw!r}"},
                    status=400,
                )
            # Bind into the contextvar. Token is used to restore the
            # previous value (which is almost always None here).
            token = set_current_tenant(tenant)
            request.tenant = tenant

        try:
            response = self.get_response(request)
        finally:
            # ALWAYS clean up — even on exception. If we forget, the
            # next request on this same async task could inherit the
            # tenant (a more subtle leak than threading.local but still
            # possible if a task is reused).
            if token is not None:
                reset_current_tenant(token)

        return response
