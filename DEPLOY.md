# Deploy Judah Scanner to Google Cloud Run

Free-tier deploy — auto-builds from GitHub, HTTPS included, scales to zero when idle.

## Prerequisites

- Google Cloud account: https://console.cloud.google.com (new accounts get $300 free credit)
- `gcloud` CLI installed (optional — can do everything via web console)

## Quick Deploy (Web Console, no CLI)

1. Go to https://console.cloud.google.com/run
2. Click **Create Service**
3. Select **Deploy one revision from a source repository**
4. Click **Set up with Cloud Build**
5. Authenticate with GitHub if prompted
6. Select `joshuagnanaselvin-coder/Judah-Scanner`
7. Branch: `main`
8. Build type: **Dockerfile** (auto-detected)
9. Region: `us-central1`
10. Authentication: **Allow unauthenticated**
11. Settings:
    - Memory: **1 GiB**
    - CPU: **2**
    - Min instances: **1** (keeps WebSocket warm)
    - Max instances: **3**
    - Request timeout: **600 seconds**
12. Click **Create**

In ~3 minutes you'll get a URL like:
```
https://judah-scanner-xyz-uc.a.run.app
```

## Local Test (optional)

```bash
docker build -t judah-scanner .
docker run -p 8080:8080 judah-scanner
# Open http://localhost:8080
```

## Cost

| Usage | Cost |
|-------|------|
| Always-on, low traffic | **$0/month** (within free tier: 2M requests, 360K vCPU-sec, 1GB egress) |
| Light traffic spikes | A few cents |
| Heavy traffic (100k+ req/day) | ~$5-10/month |

## WebSocket Note

Cloud Run supports WebSockets but with timeout: I set **600s**. For 24/7 connections, set `--min-instances=1` so one container stays warm.

## Custom Domain

After deploy: Cloud Run → Service → **Manage Custom Domains** → map `signals.yourdomain.com` (free TLS cert).