# DESIGN.md — System Design & Architecture Decisions

This document details the design and architecture choices for the optimized database queries, the rate-limited async job queue, and the multi-tenant data isolation.

---

## Section 1: Diagnose a Broken System

### Root Cause
Under load, the Django API endpoint `/api/orders/summary/` experienced an N+1 query regression. While querying orders returned a list of `Order` rows in a single DB round-trip, accessing the reverse ForeignKey relationship `order.items.all()` inside a loop caused Django to issue a separate SQL query for the `Item` model for every single order row. With 200 orders, this resulted in 1 primary query + 200 secondary queries = 201 database round-trips.

### Optimization
The endpoint was optimized by applying ORM pre-fetching instructions on the `Order` queryset:
```python
Order.objects.select_related("customer").prefetch_related("items")
```

### Why it Works
- **`select_related("customer")`** performs an SQL `INNER JOIN` in the primary query to fetch the related customer data in the same round-trip.
- **`prefetch_related("items")`** executes exactly one additional query to fetch all relevant `Item` rows in a single batch (using a `WHERE order_id IN (...)` clause). Django then handles the mapping of items to orders in Python memory.
This reduces the query footprint to a constant **2 queries**, regardless of table size.

---

## Section 2: Design a Rate-Limited Async Job Queue

### Celery Design
We chose **Celery + Redis** over alternatives like Django Q or custom asyncio loop supervisions. Celery provides battle-tested task orchestration, native retry strategies, failure handling, and worker-loss notifications. Setting `acks_late=True` and `reject_on_worker_lost=True` ensures tasks are requeued to the broker even if a worker crashes (SIGKILL) mid-execution, preventing task loss.

### Token Bucket Rate Limiter
The rate limiter implements a **Token Bucket** algorithm rather than fixed-window or sliding-window algorithms.
- **Continuous Refill**: Tokens refill continuously based on the elapsed time between requests rather than resetting at fixed times. This avoids the burst limit boundary problems of fixed window loops, smoothing out traffic over time.
- **No Sleep**: Instead of blocking using `time.sleep()`, the worker retries the Celery task itself with a computed delay (`self.retry(countdown=...)`), freeing the worker process to handle other active tasks.

### Redis Atomicity
To guarantee atomicity and prevent race conditions where concurrent workers over-spend tokens, the check-and-decrement logic is executed in a single **Redis Lua script**:
```lua
local key      = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill   = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local state    = redis.call('HMGET', key, 'tokens', 'ts')
local tokens   = tonumber(state[1])
local ts       = tonumber(state[2])

if tokens == nil then
    tokens = capacity
    ts = now
end

local delta = now - ts
if delta > 0 then
    tokens = math.min(capacity, tokens + delta * refill)
    ts = now
end

if tokens >= 1 then
    tokens = tokens - 1
    redis.call('HMSET', key, 'tokens', tokens, 'ts', ts)
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'ts', ts)
    return 0
end
```
Because Redis runs scripts in a single event loop, no other commands interleave during the script's execution.

### Failure Handling
- **Fail Closed**: If Redis crashes, `acquire_token` raises `RedisError`. The rate limiter fails closed (raises error), causing Celery to retry with exponential backoff. This protects downstream email providers from being overwhelmed.
- **Dead-Letter Handling**: If a task exceeds its maximum retries, the failure is captured and persisted to the `FailedTask` database model for operator intervention.

### Trade-offs
- Failing closed protects downstream systems but results in Celery task backlog growth during Redis outages.
- Late acknowledgments ensure task delivery guarantees but require the task payload and email sending system to be idempotent, as tasks might run twice on worker failure.

---

## Section 3: Multi-Tenant Data Isolation

### Tenant Isolation
Isolation must be enforced automatically at the ORM layer. This is achieved by creating a custom Django Manager (`TenantManager`) that overrides the default queryset to automatically apply `.filter(tenant=current_tenant)`. 

### Thread-local vs. Contextvars
- **Thread-local (`threading.local`)**: Fails under async ASGI servers (like Django async views or channels) where multiple requests are multiplexed on a single OS thread. Coroutine context switches can lead to variables being modified by one request and leaking into another.
- **Contextvars (`contextvars.ContextVar`)**: Resolves this by isolating variables to the execution context of the asynchronous task (coroutine), ensuring thread and coroutine safety under both WSGI (sync) and ASGI (async) executions.

### Trade-offs
- Defining tenant state globally requires developer discipline to explicitly use `Order.unscoped` when global queries (like admin aggregates) are needed.
- If tenant context is not set (e.g. background runner runs without tenant association), the queryset fails closed by returning `QuerySet.none()`.
