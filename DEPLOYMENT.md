# Render Deployment Guide

Deploy Invoice Processing API to Render (free tier).

## Prerequisites

- GitHub account
- OpenAI API key
- Render account (create at [render.com](https://render.com))

## Deployment Steps

### 1. Push Code to GitHub

```bash
# Add all changes
git add .
git commit -m "feat: add Render deployment configuration"
git push origin main
```

### 2. Create Render Web Service

1. Go to [dashboard.render.com](https://dashboard.render.com)
2. Click **New** → **Web Service**
3. Connect your GitHub repository
4. Configure settings:

**Build & Deploy:**
- **Dockerfile path**: `./Dockerfile`
- **Docker Context**: `.` (leave default)
- **Region**: Oregon (or closest to you)
- **Branch**: `main`

**Environment Variables** (add these in Render dashboard):

| Variable | Value | Required |
|----------|-------|----------|
| `OPENAI_API_KEY` | `sk-proj-...` | Yes |
| `API_KEYS` | `your-key-1,another-key-2` | Yes |
| `ALLOWED_ORIGINS` | `https://yourdomain.com` | No |
| `SCALE_FACTOR` | `0.2` | No |
| `TOLERANCE` | `3` | No |
| `OCR_DPI` | `150` | No |
| `MAX_PDF_SIZE_MB` | `2` | No |
| `OCR_LANGUAGES` | `ron+eng+rus` | No |
| `LLM_MODEL` | `gpt-4o-mini` | No |
| `LLM_TEMPERATURE` | `0` | No |

**Generate secure API keys:**
```python
import secrets
print(secrets.token_urlsafe(32))  # e.g., "xKj8mN2pQ5rT7vY9wZ3b"
```

**Resources (free tier):**
- **Memory**: 512Mi
- **CPU**: 0.1
- **Instances**: 1

### 3. Deploy

Click **Create Web Service**. Render will:
- Build Docker image
- Run health checks
- Deploy to production

**Deployment time:** 5-10 minutes

### 4. Verify Deployment

Once deployed, you'll get a URL like:
`https://invoice-processing-api.onrender.com`

**Test health check:**
```bash
curl https://invoice-processing-api.onrender.com/health
```

**Test extraction:**
```bash
curl -X POST "https://invoice-processing-api.onrender.com/extract" \
  -H "X-API-Key: your-api-key" \
  -F "file=@invoice.pdf"
```

**Access docs:**
- Swagger UI: `https://invoice-processing-api.onrender.com/docs`
- ReDoc: `https://invoice-processing-api.onrender.com/redoc`

## Production Checklist

- [ ] Add production API keys
- [ ] Set `ALLOWED_ORIGINS` to your frontend domains
- [ ] Test with real invoice PDFs
- [ ] Monitor logs in Render dashboard
- [ ] Set up error alerts (Render notifies you)

## Known Limitations (Free Tier)

- **Sleeps after 15min** of inactivity (cold start ~30s)
- **512MB RAM** (may struggle with large PDFs)
- **0.1 CPU** (slower processing)
- **No persistent storage** (output files lost after restart)

## Scaling (Paid Plans)

If you need always-on or faster processing:
- **Standard**: $7/month (2 CPU, 2GB RAM)
- **Pro**: $25/month (4 CPU, 4GB RAM)

## Troubleshooting

### Deployment Fails

**Check logs:**
- Render dashboard → Service → Logs

**Common issues:**
- Missing `OPENAI_API_KEY` → add environment variable
- Docker build fails → test locally first
- Health check fails → verify `/health` endpoint works

### API Returns 401

**Fix:** Verify `X-API-Key` header matches configured keys.

### Rate Limit Errors

**Free tier:** 10 requests/minute. Slow down or upgrade.

### Slow Performance

**Optimizations:**
- Resize large PDFs before upload
- Keep uploads at or below `MAX_PDF_SIZE_MB` (default 2 MB)
- Use `--mock` mode for testing (add `MOCK=true` env var)
- Upgrade to paid plan for more CPU/RAM

## Environment Variables Reference

```env
# Required
OPENAI_API_KEY=sk-proj-...
API_KEYS=key-1,key-2,key-3

# Optional
ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
SCALE_FACTOR=0.2
TOLERANCE=3
OCR_DPI=150
MAX_PDF_SIZE_MB=2
OCR_LANGUAGES=ron+eng+rus
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0
MOCK=false  # Set to "true" to disable OpenAI calls
```

## Monitoring

Render provides built-in:
- **Logs**: Real-time application logs
- **Metrics**: CPU, memory, response time
- **Alerts**: Email notifications for errors
- **Deployments**: Git-triggered deploys

## Next Steps

1. **Team onboarding**: Share API keys and documentation
2. **Frontend integration**: Use `/extract` endpoint in your app
3. **Error handling**: Implement retry logic for cold starts
4. **Cost monitoring**: Track OpenAI API usage
5. **Backup plan**: Keep CLI version for offline processing
