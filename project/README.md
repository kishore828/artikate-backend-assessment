# Artikate Studio — Backend Developer Assessment Submission

A Django-based project implementing the requirements of the Artikate Studio backend assessment.

## Project Overview
This project contains implementations for the following sections:
1. **Diagnose a Broken System**: Resolving an N+1 query in a database order summary endpoint.
2. **Rate-Limited Async Job Queue**: Designing and implementing an email sending task queue using Celery and Redis with an atomic token-bucket rate limiter.
3. **Multi-Tenant Data Isolation**: Implemented ORM-level tenant isolation using custom Managers and HttpRequest Middleware backed by thread-safe `contextvars`.
4. **Written Architecture Review**: Solutions and answers to the system design questions.

## Requirements
- Python 3.10+
- Django 4.2.16
- Celery 5.4.0
- Redis 5.0.8
- PostgreSQL (Optional, SQLite is used by default)
- Docker & Docker Compose (for running Redis)

## Setup & Installation

### 1. Create a Virtual Environment and Install Dependencies
```bash
python -m venv venv
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
*(No changes are needed by default to run with SQLite and mock Redis).*

### 3. Run Database Migrations
```bash
python manage.py migrate
```

### 4. Create Admin Account (Optional)
```bash
python manage.py createsuperuser
```

---

## Running the Application

### Running Redis
To run Redis locally (required to test Celery end-to-end), you can spin up the Redis container using Docker Compose:
```bash
docker compose up -d redis
```

### Running Celery
Start the Celery worker in a separate terminal:
```bash
celery -A config worker -l info
```

### Running Django Dev Server
```bash
python manage.py runserver
```

---

## Running Tests
Run the test suite via pytest from the `project` root directory:
```bash
pytest
```
*Note: All Celery and Rate Limiter tests use `fakeredis` so they do not require a live Redis instance to be running to pass.*

---

## API Endpoints
When the Django dev server is running, the following endpoints are available:

| Endpoint | Method | Description |
| --- | --- | --- |
| `/api/orders/seed/` | GET | Seeds the database with random orders and items. |
| `/api/orders/summary/` | GET | Broken (N+1 query) order summary endpoint. |
| `/api/orders/summary/optimized/` | GET | Fixed/Optimized order summary endpoint using select/prefetch. |
| `/silk/` | GET | Django Silk profiler dashboard showing SQL query performance. |

---

## Screenshot Locations
Proof of functionality and query logs are stored in the [project/screenshots/](file:///e:/trding/algo/job/artikate_backend_assessment_v2/project/screenshots) folder:

- **Section 1: Diagnose Broken System**
  - [section1_before.png.png](file:///e:/trding/algo/job/artikate_backend_assessment_v2/project/screenshots/section1_before.png.png): Silk profiler query count list BEFORE optimization (201 database queries).
  - [section1_after.png.png](file:///e:/trding/algo/job/artikate_backend_assessment_v2/project/screenshots/section1_after.png.png): Silk profiler query count list AFTER optimization (constant 2 SQL queries).
  - [quary s1.png](file:///e:/trding/algo/job/artikate_backend_assessment_v2/project/screenshots/quary%20s1.png): Silk detailed query plan and execution time details.

- **Section 2: Celery Job Queue & Throttling**
  - [celery-task.png.png](file:///e:/trding/algo/job/artikate_backend_assessment_v2/project/screenshots/celery-task.png.png): Celery worker initialization.
  - [celery-task sent.png.png](file:///e:/trding/algo/job/artikate_backend_assessment_v2/project/screenshots/celery-task%20sent.png.png): Celery logs demonstrating 500 tasks being queued, first 200 executing immediately and the rest throttled with exponential backoffs.

- **Automated Test Results**
  - [tests.png.png](file:///e:/trding/algo/job/artikate_backend_assessment_v2/project/screenshots/tests.png.png): Pytest output screenshot showing all 44 unit and integration tests passing successfully.

---


## Repository Structure
```
.
├── manage.py
├── pytest.ini
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
├── README.md
├── DESIGN.md
├── ANSWERS.md
├── config/                  # Django project configuration
│   ├── settings.py
│   ├── config.py
│   ├── urls.py
│   ├── celery.py
│   ├── wsgi.py
│   └── asgi.py
├── section1_diagnose/       # Section 1 App (N+1 Incident Diagnosis)
├── section2_queue/          # Section 2 App (Rate Limited Queue)
└── section3_tenant/         # Section 3 App (Multi-Tenant Isolation)
```

---

## Assumptions
- **Async Safety**: Implemented multi-tenant isolation via `contextvars.ContextVar` assuming async-views context safety, which isolates the tenant state per concurrent request handler.
- **Fail Closed**: The rate limiter fails-closed when Redis is unavailable to prevent rate-limit violations, which could result in account bans from external email providers.
