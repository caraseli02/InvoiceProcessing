---
category: security-issues
title: No Rate Limiting Enables DoS Attacks
component: FastAPI Rate Limiting
priority: p1
issue_type: security
tags: [rate-limiting, dos, security, slowapi, fastapi]
related_issues: ["001", "003"]
created_date: 2026-02-04
solved_date: 2026-02-04
---

# No Rate Limiting Enables DoS Attacks

## Problem Statement

The FastAPI service has no rate limiting mechanism, allowing unlimited API calls per client. This enables denial-of-service attacks, quota exhaustion, and resource exhaustion attacks.

**Why this matters:**
- Single client can overwhelm the system
- OpenAI API quota can be drained by malicious actors
- No protection against abuse or misuse
- Cannot implement fair usage policies
- High risk in production environments

## Symptoms

- Unlimited requests per minute/hour
- No per-client limits
- No global concurrency limits
- No quota management
- OpenAI API calls unthrottled
- API vulnerable to quota exhaustion attacks

## Investigation Steps

1. Reviewed `src/invproc/api.py` for rate limiting
2. Analyzed attack scenarios (DoS, quota drain, resource exhaustion)
3. Evaluated 4 solution approaches (slowapi, fastapi-limiter, custom, gateway)
4. Determined `slowapi` library as optimal solution
5. Designed rate limit appropriate for invoice processing

## Root Cause

No rate limiting middleware or decorators configured:

```python
# 🔴 No rate limiting
@app.post("/extract")
async def extract_invoice(...):
    # No rate limit checks
    # Unlimited API calls possible
    ...
```

### Attack Scenarios

1. **Quota Drain**: Attacker sends 500 requests, exhausting monthly OpenAI quota
2. **Resource Exhaustion**: Attacker uploads 500MB PDFs, consuming all memory
3. **Service Disruption**: Attacker floods API with requests, blocking legitimate users

### Impact Assessment

| Attack Type | Impact | Risk |
|-------------|--------|-------|
| **Quota Drain** | $50/month quota drained in minutes | High (cost) |
| **Resource Exhaustion** | Server OOM, crash | High (availability) |
| **Service Disruption** | Legitimate users denied | High (reliability) |

## Working Solution

### slowapi Library

Added rate limiting using `slowapi` library:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["10/minute"],
    swallow_errors=True,  # Important for TestClient compatibility
)

@app.get("/health")
@limiter.exempt  # Health check should not be rate limited
async def health_check():
    return {
        "status": "healthy",
        "service": "invoice-processing",
        "version": "1.0.0",
    }

@app.post(
    "/extract",
    response_model=InvoiceData,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"description": "Invalid API key"},
        400: {"description": "Invalid PDF file"},
        429: {"description": "Rate limit exceeded"},
        500: {"description": "Internal server error"},
    },
)
@limiter.limit("10/minute")  # 10 requests per minute
async def extract_invoice(...):
    # ... processing logic ...
```

### Exception Handler

Added handler for rate limit exceeded:

```python
@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Rate limit exceeded handler."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )
```

### Dependencies

Updated `requirements.txt`:

```txt
slowapi>=0.1.9
```

### Key Changes

1. **Added slowapi Library**: For rate limiting functionality
2. **Default Limits**: 10 requests/minute for `/extract` endpoint
3. **Exempt Health Check**: `/health` not rate limited (monitoring access)
3. **Exception Handler**: Returns 429 with clear message
4. **IP-Based Key Function**: Rate limit per IP address
5. **Swallow Errors**: `swallow_errors=True` for TestClient compatibility

## Prevention Strategies

### 1. Rate Limiting Principles

Implement effective rate limiting:

- **Rate Limits**: Appropriate for use case (10/min for invoice processing)
- **Per-Client**: Limit by IP address or API key
- **Graceful Degradation**: Return 429, don't crash
- **Configurable**: Environment variables for different environments

### 2. Monitoring and Alerting

Set up monitoring:

```python
# Log rate limit violations
import logging

logger = logging.getLogger(__name__)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    logger.warning(f"Rate limit exceeded: {request.client.host}")
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
```

### 3. Distributed Rate Limiting

For production scaling:

```python
# Use Redis-backed rate limiting for multiple instances
from slowapi import Limiter

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="redis://localhost:6379",  # Redis for distributed
    default_limits=["10/minute"],
)
```

### 4. API Gateway Rate Limiting

Alternative: Offload to infrastructure:

```yaml
# Cloudflare Workers rate limiting
# AWS API Gateway throttling
# nginx rate limiting module
```

### 5. Testing Rate Limits

Test and validate rate limiting:

```bash
# Test rate limit enforcement
for i in {1..15}; do
  curl -X POST "http://localhost:8000/extract" \
    -H "X-API-Key: test-key" \
    -F "file=@invoice.pdf" &
done
# Should get 429 after 10 requests
```

## Cross-References

### Related Issues

- Issue #001: Global State Thread Safety - Thread safety enables rate limiting to work correctly
- Issue #002: CORS Security - Part of comprehensive security hardening
- Issue #003: Blocking I/O - Async behavior needed for rate limiting to be effective

### Related Documentation

- [slowapi GitHub](https://github.com/laurentS/slowapi)
- [OWASP Unrestricted Resource Consumption](https://cwe.mitre.org/data/definitions/770.html)
- [Rate Limiting Best Practices](https://restfulapi.net/rate-limiting/)

## Verification

### Acceptance Criteria

- [x] `slowapi` library added to dependencies
- [x] Rate limiting middleware configured
- [x] `/extract` endpoint limited to 10 requests/minute
- [x] `/health` endpoint exempt from rate limiting
- [x] Rate limit headers included in responses (`X-RateLimit-Remaining`, `X-RateLimit-Reset`)
- [x] Tests verify rate limiting behavior
- [x] Load test confirms rate limits are enforced
- [x] Error 429 returned with clear message when limit exceeded

## Notes

- This fix was part of commit `1fb0682`
- 10 requests/minute is appropriate for invoice processing (not too restrictive)
- Protects against DoS attacks and quota exhaustion
- Rate limit can be adjusted per environment if needed
- Consider Redis-backed rate limiting for production horizontal scaling
