# Vivid Terros Dashboard — Full Setup Guide

This document captures the complete architecture, configuration, and deployment steps so the project can be replicated for a new dataset in the future.

---

## What Was Built

A live sales dashboard that:
- Fetches weekly rep performance data from the Terros API
- Displays it in a sortable leaderboard (pitches, knocks, ACs, SRAs)
- Is hosted publicly on GitHub Pages (shareable link)
- Fetches live data from a Python API server hosted on Render
- Is kept awake 24/7 by UptimeRobot (free)
- Also works locally via a .bat launcher

**Live URLs:**
- Dashboard: https://abipsha.github.io/terros-dashboard/
- API server: https://terros-dashboard.onrender.com

---

## File Structure

```
terros-dashboard/
  api/
    server.py       — Python HTTP server (serves JSON at /api/weekly etc.)
    terros.py       — Terros API client (all API calls live here)
    Start API Server.bat   — Double-click to run locally
    Restart Server.bat     — Kills existing Python process and restarts
  index.html        — The dashboard (single HTML file, all CSS+JS inline)
  vivid-terros-dashboard.html  — Same as index.html (kept for local server use)
  requirements.txt  — Empty (stdlib only), required by Render to detect Python
  .gitignore        — Excludes __pycache__, .env, etc.
  CLAUDE.md         — Developer notes for Claude (architecture, known issues)
  SETUP.md          — This file
```

---

## How It Works

1. User opens the GitHub Pages URL
2. `index.html` loads in the browser
3. JS detects it's not on localhost → uses `PROD_API` (Render URL)
4. Fetches `GET https://terros-dashboard.onrender.com/api/weekly`
5. Python server calls Terros `/report/kpi` v2 with teams filter
6. Returns JSON; dashboard renders the leaderboard table

When opened locally, the JS detects `localhost` and hits `http://localhost:8000` instead.

---

## Key Configuration in index.html

Near the top of the `<script>` block:

```javascript
const PROD_API  = 'https://terros-dashboard.onrender.com'; // ← Render URL
const LOCAL_API = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
  ? 'http://localhost:8000'
  : PROD_API;
```

Change `PROD_API` when deploying a new version with a different Render URL.

---

## Key Configuration in api/terros.py

```python
API_KEY = os.environ.get("TERROS_API_KEY", "atQjJCg13du0c4aAeU4hc")
```

The API key is read from an environment variable in production (set on Render).
Falls back to the hardcoded value for local use.

---

## Terros API Details

- Endpoint: `POST https://api.terros.com/report/kpi` with `version: 2`
- Requires `teams` filter in the request body (without it, counts are inflated)
- Timezone: CDT = UTC-5 (all date boundaries must use this)
- Week range: Monday 00:00:00 CDT → Sunday 23:59:59.999 CDT
- Response rows are positional arrays: `[user, pitches, knocks, sets, leads, closes, daysWorked]`

KPI IDs used:
```python
{"value": "account|S.a8f7HEnPXbcn7i5hYt2hi|count", "accumulator": "distinct"},  # pitches
{"value": "Ratio.DYw9YWyI6-iiVk3MfTnjO",           "accumulator": "sum"},        # knocks
{"value": "account|S.sKssTYhP95k9n1LA3fTao|count", "accumulator": "distinct"},   # sets/ACs
{"value": "account|S.WYBl4yTJCNZoNoxAGCqBL|count", "accumulator": "distinct"},   # leads
{"value": "account|S.WInKtoxDT73Vpo1QgXnzv|count", "accumulator": "distinct"},   # closes/SRAs
{"value": "action.daysWorked",                      "accumulator": "sum"},
```

---

## Cloud Deployment Steps

### 1. GitHub (code hosting + static site)

1. Install GitHub Desktop: https://desktop.github.com
2. Add Local Repository → select the project folder → Initialize Repository
3. Commit all files → Publish Repository (set to **Private**)
4. Delete any sensitive files (key.txt, etc.) from the repo
5. Upload `index.html` directly via GitHub web if GitHub Desktop doesn't detect it
6. Enable GitHub Pages: Settings → Pages → Deploy from branch → main / (root) → Save
7. URL appears at top of Pages settings after ~1 minute

### 2. Render (Python API server)

1. Sign up at https://render.com (free, no credit card)
2. New → Web Service → connect GitHub repo
3. Settings:
   - **Root Directory**: (leave blank)
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python api/server.py`
   - **Instance Type**: Free
4. Environment Variables:
   - `TERROS_API_KEY` = your API key
5. Deploy → wait for green "Live" status
6. Copy the `.onrender.com` URL

### 3. UptimeRobot (prevent Render from sleeping)

1. Sign up at https://uptimerobot.com (free)
2. Add New Monitor:
   - Type: HTTP(s)
   - URL: `https://YOUR-APP.onrender.com/health`
   - Interval: 5 minutes
3. Save — server will never sleep

---

## Local Development

Double-click `api/Start API Server.bat` (or `Restart Server.bat` to kill and restart).

Dashboard opens automatically at `http://localhost:8000`.

To force fresh data: `GET http://localhost:8000/api/weekly?force=1`

---

## Replicating for a New Dataset

1. Copy this entire folder as a starting point
2. Update `api/terros.py` with the new API credentials and endpoints
3. Update the KPI IDs in `get_kpi_v2()` to match the new data source
4. Update the table columns in `index.html` to match the new metrics
5. Create a new GitHub repo and Render service
6. Update `PROD_API` in `index.html` with the new Render URL
7. Follow the deployment steps above

---

## Known Issues & Gotchas

- Render free tier has ~50 second cold start if UptimeRobot is not set up
- GitHub repo must be **public** for GitHub Pages on free plan
- The Terros API requires a `teams` filter — without it, counts are inflated
- Timezone must be CDT (UTC-5), not UTC — all date math uses this
- `</script>` must never appear inside the HTML `<script>` block (breaks parsing)
- No nested backtick template literals in JS (breaks silently)
- Do not use bash heredoc to write Unicode content to HTML files
