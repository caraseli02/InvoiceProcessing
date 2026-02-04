---
date: 2026-02-03
topic: fastapi-docker-invoice-service
---

# FastAPI + Docker Invoice Processing Service

## What We're Building

Add a REST API layer and Docker containerization to the existing CLI invoice processing tool, enabling remote access for internal team members while preserving local CLI functionality. The service will expose an `/extract` endpoint that accepts PDF uploads and returns structured invoice JSON data.

**Scope:** FastAPI wrapper with Docker support, internal team use, basic authentication (API keys), simple error handling.

## Why This Approach

**Alternatives considered:**
- **API-First Service:** Full production-ready microservice with comprehensive auth and monitoring - rejected as overkill for internal team use
- **Unified Service:** Refactor to shared core for multiple interfaces - rejected as unnecessary complexity given only CLI + API needed

**Chosen Approach (FastAPI Wrapper):**
- Minimal risk - 100% reuse of existing extraction logic
- Fast implementation (1-2 weeks)
- Keeps CLI available for local development
- Low maintenance burden
- Sufficient for internal team requirements

This approach respects YAGNI - we're building exactly what's needed (API + deployment) without over-engineering for hypothetical future requirements.

## Key Decisions

- **FastAPI framework:** Chosen for automatic API docs (Swagger UI), async support, and Python ecosystem compatibility
- **Docker multi-stage build:** Minimize image size while including Tesseract OCR dependencies
- **API key authentication:** Simple auth sufficient for internal team use (no OAuth/SSO needed)
- **Single endpoint pattern:** `/extract` POST endpoint for PDF upload (GET endpoints not needed)
- **Reuse existing models:** `InvoiceData` Pydantic model serves as both internal structure and API response schema
- **Minimal error handling:** Basic 4xx/5xx responses without complex retry/circuit breaker logic (simple per user request)
- **Health check endpoint:** `/health` GET endpoint for container orchestration (Kubernetes, etc.)

## Open Questions

None - scope is well-defined and sufficient for planning phase.

## Next Steps

1. Implement FastAPI API layer (`src/invproc/api.py`)
2. Add Docker configuration (`Dockerfile`, `docker-compose.yml`)
3. Add API key authentication middleware
4. Create health check endpoint
5. Test API with existing test invoices
6. Validate Docker container builds and runs correctly

→ `/workflows:plan` for implementation details, file structure, and testing strategy.
