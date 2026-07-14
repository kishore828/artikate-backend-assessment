# ANSWERS.md — Written Assessment Answers

This file contains the required written answers for the assessment sections.

---

## 01: Diagnose a Broken System

### 1. Incident Investigation Log
- **Step 1**: Confirmed the latency regression. Inspected application performance logs for `/api/orders/summary/` showing response times spiking from ~80ms to over 30 seconds for users with >200 orders.
- **Step 2**: Inspected deployment diff. No changes were made to the view code, but a schema migration added a new `Item` model representing order line items.
- **Step 3**: Analyzed DB logs and Django Silk profiler dashboard at `/silk/`. Discovered the endpoint executed **201 SQL queries** (1 query to fetch orders + 200 lazy-loaded SELECT queries for items, i.e., one query for each order).
- **Step 4**: Confirmed the N+1 issue locally, applied the optimization, and validated that query counts dropped to exactly 2.

### 2. Root Cause Category
- **N+1 Query Regression**: Caused by iterating over the orders queryset and accessing the reverse ForeignKey relationship (`order.items.all()`) inside the loop without eager loading, generating 1 database round-trip per order row.

### 3. Why the Fix Works
Applying `.select_related("customer").prefetch_related("items")` changes the database footprint:
- **`select_related("customer")`**: Eagerly joins the Customer table via SQL `INNER JOIN` during the primary query, collapsing the customer lookup into 0 extra queries.
- **`prefetch_related("items")`**: Performs 1 extra query `SELECT * FROM items WHERE order_id IN (...)` to fetch all line items, then correlates the items to orders in Python memory, collapsing the N queries for items down to 1 query.
Total footprint is optimized from 201 queries to exactly **2 queries**.

### 4. Profiler Evidence
django-silk was installed. Before the fix, the dashboard recorded 201 queries for `/api/orders/summary/`. After the fix, it recorded 2 queries (1 for orders + customer join, 1 for prefetching items).

---

## 02: Design a Rate-Limited Async Job Queue

### SIGKILL Handling
- **The Issue**: By default, Celery acknowledges (`ACK`) a task before executing it. If a worker process is killed (`SIGKILL`), crashed, or runs out of memory mid-run, the task is lost.
- **The Solution**: We enabled global settings and task-level overrides to defer acknowledgement:
  ```python
  acks_late = True
  reject_on_worker_lost = True
  ```
  - **`acks_late=True`**: Celery only sends the `ACK` back to Redis *after* the task successfully returns.
  - **`reject_on_worker_lost=True`**: If the worker child process crashes or receives `SIGKILL`, the supervisor rejects the message, forcing Redis to immediately requeue the message for another worker instead of waiting for visibility timeouts (default 1 hour).
  - **Idempotency**: This guarantees **at-least-once** delivery. Emails may be sent twice if the crash occurs after sending but before ACK. We handle this down-stream via email provider uniqueness or de-duplication metadata.

---

## 03: Multi-Tenant Data Isolation

### Failure Modes of Thread-locals in Async Django Views
- **The Issue**: `threading.local` associates variables with the OS thread. Under an async ASGI web server, a single OS thread is shared and multiplexed among hundreds of concurrent requests using the asyncio event loop.
- **Failure Mode**: When Request A (Tenant A) yields during an awaitable database call, the thread switches to execute Request B (Tenant B). Request B sets the thread-local tenant to Tenant B. Once Request A resumes on the same thread, it reads the overridden value and exposes Tenant B’s data to Tenant A.
- **The Solution**: Use `contextvars.ContextVar`. ContextVar isolates data to the execution context of the asynchronous task (coroutine) rather than the parent OS thread. When task context switches block and resume, the coroutine context is preserved, preventing cross-tenant leakage.

---

## 04: Written Architecture Review

### Question A — Django Admin Performance (500k+ records)
1. **N+1 Queries in `list_display`**: When showing fields from related models, a query is executed for every row. Fix: Define `list_select_related = ("customer",)` on the `ModelAdmin` class to perform an SQL JOIN for the related models.
2. **Sequential `COUNT(*)` Scans for Pagination**: PostgreSQL traverses the table on every page load to calculate total pages. Fix: Override `ModelAdmin.get_paginator` to return a cached count or query `reltuples` planner estimations from `pg_class` for sub-millisecond pagination, or set `show_full_result_count = False`.
3. **Unindexed Searches**: Using `search_fields` triggers an unindexed `ILIKE '%term%'` sequential scan. Fix: Add an index (e.g. `B-Tree` for prefix or `GIN` for substring search) and override `get_search_results` to perform prefix-only matches (`ILIKE 'term%'` instead of leading wildcard searches).

### Question B — Pagination Trade-offs
- **Offset-based (`LIMIT X OFFSET Y`)**:
  - *Database cost*: DB must scan and discard all rows up to target page offset ($O(Y)$ complexity). Gets slower on deep pages.
  - *Mutation vulnerability*: If rows are inserted or deleted during pagination, offsets shift, causing items to be skipped or duplicated across page boundaries.
  - *When to use*: Admin tables and dashboards where jumping to arbitrary page numbers is required and data change is slow.
- **Cursor-based (`WHERE id > last_seen_id ORDER BY id LIMIT X`)**:
  - *Database cost*: Constant $O(X)$ complexity using index seek, scale-independent.
  - *Mutation vulnerability*: Safe against shifted items on insertions/deletions.
  - *When to use*: Infinite scroll feeds, high-frequency APIs, and mobile app feeds where absolute pages are not required.
