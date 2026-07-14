"""
Section 3 — current-tenant context storage.

Why contextvars and not threading.local?
----------------------------------------
Django 3.1+ supports ASGI / async views. Under ASGI a single OS thread
runs *many* concurrent requests via an event loop — if you stash the
current tenant in `threading.local()`, two concurrent requests sharing
that thread will overwrite each other's tenant and leak data across
tenants. That's the failure mode the brief asks us to identify.

`contextvars.ContextVar` solves this: the asyncio event loop copies the
context per *task* (per request), so each async request sees its own
value, while sync requests still behave like thread-locals. Even under
sync Django the overhead is negligible and the API is identical.

Usage
-----
    token = set_current_tenant(tenant)        # bind
    try: ...
    finally: reset_current_tenant(token)       # unbind

The middleware in ``middleware.py`` does exactly this wrap/unwrap so the
context is always cleared at the end of a request — even on exceptions.
"""

from __future__ import annotations

import contextvars
from typing import Optional

# A module-level ContextVar. The default is `None` so any code that
# touches a tenant-scoped model outside of a request gets an empty
# queryset (see TenantManager.get_queryset) rather than silently leaking
# cross-tenant data.
_current_tenant: contextvars.ContextVar[Optional["object"]] = contextvars.ContextVar(
    "current_tenant", default=None
)


def get_current_tenant():
    """Return the Tenant currently bound to this request/context."""
    return _current_tenant.get()


def set_current_tenant(tenant):
    """Bind ``tenant`` to the current context.

    Returns a token that MUST be passed to ``reset_current_tenant`` to
    restore the previous value (which may be ``None``).
    """
    return _current_tenant.set(tenant)


def reset_current_tenant(token) -> None:
    """Restore the previous tenant context using the token from ``set``."""
    _current_tenant.reset(token)
