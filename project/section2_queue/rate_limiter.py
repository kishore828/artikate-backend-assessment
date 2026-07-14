"""
Section 2 — atomic Token Bucket rate limiter built on a Redis Lua script.

Why a Token Bucket?
-------------------
The assessment brief gives three options:
  A. Token bucket    — Redis DECR + TTL (or, more rigorously, a Lua script
                       that refills tokens based on elapsed wall-clock
                       time).
  B. Sliding window  — Redis sorted set + ZREMRANGEBYSCORE.
  C. Fixed window    — Redis INCR + EXPIRE.

We chose the **Token Bucket** because:

1. It allows short bursts up to the bucket capacity without throttling,
   which matches the email-provider's real-world tolerance — providers
   typically allow brief spikes above the per-minute cap as long as the
   rolling average stays under the limit.
2. Refill is continuous (tokens dribble back in at `refill_rate` per
   second), so a worker that just got rate-limited doesn't have to wait
   a full 60 seconds for the next window to roll over — it can fire
   again as soon as a single token refills.
3. It is trivially atomisable with a single Lua script: HMGET for the
   current state, refill math, conditional DECR, HMSET back — all
   inside one Redis EVAL.

Why Lua (and not MULTI/EXEC or a pipeline)?
-------------------------------------------
* `MULTI/EXEC` only guarantees *atomicity of execution* — it does **not**
  let us branch on the result of a command inside the transaction
  (Redis has no `WATCH`-free conditional logic). To decide "do we have
  enough tokens?" we'd need a WATCH/MULTI/EXEC loop, which adds retries
  and complexity.
* A `pipeline` is even weaker — it is just batching, with no atomicity
  guarantee at all.
* A **Lua script**, by contrast, runs as a single atomic step inside the
  Redis event loop: nothing else can mutate the bucket between the
  HMGET and the HMSET. Redis also caches the script's SHA1, so
  subsequent calls are `EVALSHA` (one network round-trip).

What happens if Redis goes down?
--------------------------------
The brief explicitly asks: fail-open or fail-closed?

We **fail closed** by default (`fail_open=False`): if we cannot reach
Redis, we refuse to send the email and instead raise — the Celery task
then retries with exponential backoff. Rationale: the email provider
will hard-ban us if we exceed 200/min, so a silent fail-open risks
catastrophic external impact. Better to queue-and-retry than to risk
the provider account.

For low-stakes use cases (e.g. internal metrics) you can flip
`fail_open=True` to let traffic through when Redis is unavailable.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token Bucket Lua script
# ---------------------------------------------------------------------------
# KEYS[1] = Redis hash key holding the bucket state
#           fields: "tokens" (float), "ts" (float, unix seconds)
# ARGV[1] = capacity           (max tokens the bucket can hold)
# ARGV[2] = refill_rate        (tokens added per second)
# ARGV[3] = now                (caller-provided wall-clock seconds —
#                               passing it in keeps the script pure and
#                               testable with fakeredis + frozen time)
# ARGV[4] = requested          (tokens this call needs; usually 1)
# ARGV[5] = ttl_seconds        (expire the key after this long idle)
#
# Returns: 1 if the request was allowed (and a token was consumed), else 0.
# ---------------------------------------------------------------------------
TOKEN_BUCKET_LUA = """
local key        = KEYS[1]
local capacity   = tonumber(ARGV[1])
local refill     = tonumber(ARGV[2])
local now        = tonumber(ARGV[3])
local requested  = tonumber(ARGV[4])
local ttl        = tonumber(ARGV[5])

local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])

if tokens == nil then
    tokens = capacity
    ts     = now
end

-- Refill: add tokens proportional to elapsed time, capped at capacity.
local delta = now - ts
if delta > 0 then
    tokens = math.min(capacity, tokens + delta * refill)
    ts     = now
end

local allowed = 0
if tokens >= requested then
    tokens  = tokens - requested
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', ts)
redis.call('EXPIRE', key, ttl)

return allowed
"""


# ---------------------------------------------------------------------------
# Defaults — 200 emails per minute
# ---------------------------------------------------------------------------
DEFAULT_CAPACITY = 200          # max burst
DEFAULT_REFILL_PER_MINUTE = 200  # provider's hard limit
DEFAULT_REFILL_PER_SECOND = DEFAULT_REFILL_PER_MINUTE / 60.0
DEFAULT_TTL_SECONDS = 600        # idle-evict the bucket after 10 min


# ---------------------------------------------------------------------------
# Redis client
# ---------------------------------------------------------------------------
# We construct the client lazily so that importing this module never opens
# a Redis connection — important for tests that swap in fakeredis.
_real_client: Optional[redis.Redis] = None


def _get_redis() -> redis.Redis:
    """Return the module-level Redis client, building it on first use."""
    global _real_client
    if _real_client is None:
        _real_client = redis.Redis.from_url(settings.REDIS_URL)
    return _real_client


def _set_redis(client: redis.Redis) -> None:
    """Test hook: inject a (fake)redis client."""
    global _real_client
    _real_client = client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def acquire_token(
    *,
    bucket: str = "email",
    capacity: int = DEFAULT_CAPACITY,
    refill_per_minute: float = DEFAULT_REFILL_PER_MINUTE,
    now: Optional[float] = None,
    fail_open: bool = False,
    client: Optional[redis.Redis] = None,
) -> bool:
    """Try to consume one token from the named bucket.

    Returns ``True`` if the call is allowed (token consumed), ``False`` if
    the bucket is empty (caller should back off).

    Parameters
    ----------
    bucket:
        Logical name of the bucket — different upstreams can have their
        own limits.
    capacity:
        Max tokens the bucket can hold (burst size).
    refill_per_minute:
        Steady-state refill rate. Converted to per-second internally.
    now:
        Optional caller-supplied timestamp (seconds). Used in tests to
        simulate the passage of time without `time.sleep`.
    fail_open:
        If True, return ``True`` when Redis is unreachable. Defaults to
        False — we fail closed (see module docstring).
    client:
        Optional injected Redis client (used by tests with fakeredis).
    """
    if now is None:
        now = time.time()

    refill_per_second = refill_per_minute / 60.0
    key = f"ratelimit:tokenbucket:{bucket}"
    ttl = max(int(capacity / refill_per_second) * 2, 60)

    r = client or _get_redis()
    try:
        allowed = r.eval(
            TOKEN_BUCKET_LUA,
            1,                  # number of keys
            key,
            capacity,
            refill_per_second,
            now,
            1,                  # requested tokens
            ttl,
        )
    except redis.RedisError as exc:
        logger.warning("Rate-limiter Redis error (%s); fail_open=%s",
                       exc, fail_open)
        if fail_open:
            return True
        raise

    return bool(int(allowed))


def reset_bucket(
    *, bucket: str = "email", client: Optional[redis.Redis] = None
) -> None:
    """Wipe a bucket — used between tests."""
    r = client or _get_redis()
    r.delete(f"ratelimit:tokenbucket:{bucket}")
