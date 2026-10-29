# Vivid Dashboard — Dev Notes

## Architecture

- `api/terros.py` — Terros API client (Python stdlib only, no pip)
- `api/odoo.py`   — Odoo CRM client (Python stdlib only, no pip)
- `api/server.py` — HTTP server on port 8000; serves HTML at `/` (Terros) and `/crm` (Odoo), JSON at `/api/*`
- `vivid-terros-dashboard.html` — Terros leaderboard; fetches from `http://localhost:8000/api/weekly`
- `vivid-crm-dashboard.html`    — Odoo CRM revenue dashboard; fetches from `http://localhost:8000/api/odoo/report`
- `leaderboard.html` — Leader Board (Drip squad-tracker style, light/dark); served at `/leaderboard`, fetches `/api/leaderboard`
- `tahiti.html` — Tahiti Trip Tracker in the same style; served at `/tahiti`, fetches `/api/odoo/deals?start=2026-04-17&end=2026-12-31`. Its stylesheet is a copy of leaderboard.html's — change the CSS there and re-copy (or edit both).
- `onboarding.html` — Onboarding in the same style; served at `/onboarding`, fetches `/api/odoo/onboarding`. Stylesheet copied from leaderboard.html too.
- `api/leaderboard.py` — assembles the Leader Board payload: Odoo Won deals (widened to whole Sun–Sat weeks), the Sales roster (via `commissions.employees_now`), Terros period KPIs + yesterday
- Launch: double-click `api/Start API Server.bat`

## Odoo CRM Dashboard

### Odoo API endpoint
`GET /api/odoo/report?start=YYYY-MM-DD&end=YYYY-MM-DD`

Defaults to current month (MTD). Pass `?force=1` to bust the 5-minute cache.

### Odoo credentials (env vars on Render, fallback in odoo.py)
- `ODOO_URL`      — default `https://myvivid.odoo.com`
- `ODOO_DB`       — default `myvivid`
- `ODOO_USER`     — default `abipsha.joshi@vividwindows.com`
- `ODOO_PASSWORD` — **use an Odoo API key, not the login password**
  - Odoo SaaS blocks XML-RPC auth from cloud provider IPs (Render = 74.220.48.235)
  - API keys bypass this restriction
  - Generate in Odoo: avatar → My Profile → Account Security → New API Key
  - The API key is used as the `password` arg in `xmlrpc.client` authenticate/execute_kw calls

### Revenue goals (env vars or fallback in odoo.py)
- `GOAL_NORTHERN_UTAH`, `GOAL_SOUTHERN_UTAH`, `GOAL_EASTERN_IDAHO`, `GOAL_NORTHERN_CALIFORNIA`, `GOAL_INSIDE_SALES`
- `GOAL_DEFAULT` — used for any unlisted office (default $300,000/month)

### Field mapping (crm.lead model)
- Revenue:  `x_studio_contract_value`
- Closer:   `x_studio_closer_text_1`
- Setter/Canvasser: `x_studio_canvasser_text`
- Office:   `team_id[1]` (team name)
- Date:     `date_closed`
- Won filter: `[['stage_id.is_won', '=', True]]`

## Critical Rules

### DO NOT write `</script>` anywhere inside the HTML `<script>` block
Even inside a JS comment or string — the HTML parser terminates the script block on the first `</script>` it sees, cutting off everything after it. If you need to reference it in a string, split it: `'</' + 'script>'`.

### DO NOT use bash heredoc to append Unicode content to the HTML file
The sandbox shell corrupts multi-byte UTF-8 characters (─, ⟳, –, etc.). Always use the Python `open(..., encoding='utf-8')` or the Edit/Write tools to write Unicode content.

### DO NOT use nested backtick template literals in the HTML `<script>` block
This breaks all JS silently — the page loads but makes zero API calls.

❌ BROKEN:
```js
`some text ${condition ? `nested \`backtick\`` : ''}`
```

✅ SAFE — use string concatenation inside ternaries:
```js
`some text ${condition ? 'nested ' + variable : ''}`
```

### API date filtering is unreliable
The Terros `activity/list` endpoint ignores `filter:{startDate,endDate}`. We apply a **client-side timestamp filter** in `get_activities()` as a hard guarantee. Do not remove this.

### `activity/list` pagination: send back `sortTimestamp` (checked Oct 25, 2026)
The endpoint now **ignores `offset`** — every page is the newest 1000 records — and every response carries `sortTimestamp: [ms, recordId]` for its last record. Send that array back as `sortTimestamp` in the next request body to get the next (older) page. Records are newest-first, so stop when a whole page is older than the start you want. `api/leaderboard.py::_fetch_activity` does this; the old `terros.get_activities()` (offset paging) is left as-is for the legacy board but returns the same 1000 records on every page. Do not paginate on `actionId` — it is the action *type* (e.g. "Not Home").

### Report cache is 5 minutes
`build_weekly_report()` caches results for 300s. Pass `?force=1` to bust it (e.g. `GET /api/weekly?start=...&end=...&force=1`).

## Action ID Map (workflow WF.lgpBmeFEyHVjxOHoJHtu3)
- `A.GsdC4_En3Y_6IXXZte-hA` = Not Home (active)
- `A.Kd2ZLBcZf4klDaX-XYlhJ` = Set / AC (active)
- `A.nM4CBIicoU2epwimOCIOm` = Follow Up (active)
- `A.lndlL_HplRp8oCuyxWJwT` = Pitch (deleted)
- `A.bi9Sdscuzejlf7wUY4XfC` = Knock (deleted)
- `A.whcOhETTwkkq5Vbo59Kh8` = New Account/Lead (deleted)

## Activity Record Schema (from /activity/list)
Each record: `{id, workflowId, actionId, timestamp (ms), userId, latlng, propertyLatLng, stageId, accountId, address, city, state, zip, user{...}}`
- `id` format: `"AccountId|WorkflowId|ActionId|timestamp"` — this IS a unique record ID
- `A.Transition` records are emitted alongside every real action — **must be filtered out** or all counts double
- The response includes `"total"` — the full record count across all pages. Use it to know when pagination is complete.

## Timezone: CDT (UTC-5)
Terros uses **Central Daylight Time (CDT = UTC-5)** for all date boundaries. Confirmed from the stats page URL: `start=1780290000000` = Jun 1, 2026 00:00 CDT exactly. The server must convert `YYYY-MM-DD` strings to CDT midnight, not UTC midnight. See `TERROS_TZ = timezone(timedelta(hours=-5))` in `server.py`.

## Data Source: `/report/kpi` v2 (NOT `activity/list`)

The dashboard now uses `POST /report/kpi` with `version: 2` — the same call the Terros stats page makes. Numbers match exactly.

### Request format
```python
{
  "grouping": {"type": "user"},
  "filter": {"startDate": start_ms, "endDate": end_ms, "workflowId": "WF.lgpBmeFEyHVjxOHoJHtu3"},
  "kpis": [
    {"value": "groupBy"},
    {"value": "account|S.a8f7HEnPXbcn7i5hYt2hi|count", "accumulator": "distinct"},  # pitches
    {"value": "Ratio.DYw9YWyI6-iiVk3MfTnjO",           "accumulator": "sum"},        # knocks
    {"value": "account|S.sKssTYhP95k9n1LA3fTao|count", "accumulator": "distinct"},   # sets/ACs
    {"value": "account|S.WYBl4yTJCNZoNoxAGCqBL|count", "accumulator": "distinct"},   # leads
    {"value": "account|S.WInKtoxDT73Vpo1QgXnzv|count", "accumulator": "distinct"},   # closes
    {"value": "action.daysWorked",                      "accumulator": "sum"},
  ],
  "hideSystemUsers": True,
  "version": 2
}
```

### Response format
- `rows`: array of positional arrays — `[userObj, pitches, knocks, sets, leads, closes, daysWorked]`
- `totalRow`: same layout — `["Total", totalPitches, totalKnocks, totalSets, totalLeads, totalCloses, totalDays]`

## Known Issues
- `activity/list` is no longer used for the main report (it was unreliable for date filtering and slow).
- `firstPitch`/`lastPitch` per rep are not available from `report/kpi` v2 — they are set to `null`.

## Leader Board (`/leaderboard`)

### Endpoint
`GET /api/leaderboard?start=YYYY-MM-DD&end=YYYY-MM-DD&part=deals|terros[&force=1]` — defaults to MTD. Two parts so the page can paint revenue (Odoo, fast) before field activity (Terros, slow): the page calls `part=deals` first, renders, then `part=terros` and re-renders.

**Speed rules** (`api/leaderboard.py::build`): whatever is cached is returned at once, even past the 300 s TTL — a stale copy kicks off a refresh in a background thread, so nobody waits for a rebuild. Only a range never asked for before is built while the caller waits. `_warm_cache()` in `server.py` builds the MTD board (both parts) at startup and every 2 minutes, so the default view is always warm. What is left: Render's free plan puts the service to sleep after 15 idle minutes, and the first visitor after that pays ~1 minute for the wake-up plus a cold cache. A paid Render instance removes the sleep.

### Rules the page applies (all in `leaderboard.html`, `derive()`)
- Revenue = `x_studio_contract_value` on Won accounts by closing date, same as the revenue tracker.
- Roles are shown as **Market Director** (closer) and **Market Coordinator** (setter). Never "Closer"/"Setter"/"Captain"/"Manager" in the UI.
- Weekly bonus runs **Sunday–Saturday**: 1 set no bonus, 2 sets +2%, 3+ sets +4% of the contract value set that week, paid to the setter. Self-gen counts as a set. The deals feed is widened to whole weeks (`weeksStart`/`weeksEnd`) so partial weeks at the edges of the range are scored on their full week.
- Standing: Productive = sale in the current week; Active = sale within 30 days of the period end or any Terros activity in the period; otherwise Inactive.
- Inside Sales is not a team tab (`TEAMS` in `api/leaderboard.py`); its reps and deals still appear on rep rows and in company totals.
- Market Coordinators lists (roster toggle, Reps filter, Overview board) show only reps who set at least one Won account **for someone else** in the period (`isCoordinatorRow` → `r.setOthers`); a self-gen does not put anyone on the list, and Sets / Revenue set there leave self-gens out. The **weekly bonus grid is different on purpose**: it follows payroll, so anyone with a set in a bonus week is listed and a self-gen counts as a set. Roles come from the employee **Tags** in Odoo (hr.employee categories "Closer" / "Setter"; Closer wins when both) — change the tag in Odoo, not the code.
- A rep's team is the office where their accounts are (most deals in the window wins); the Odoo region (`x_studio_region`) is only the fallback for a rep with no sales. Odoo is often behind when someone moves offices — Nathan Lewis was filed under Northern California while selling in Southern Utah. Inside Sales reps keep Inside Sales regardless of where their sets land.
- Tahiti tracker is NOT on this page — the "Tahiti" link goes to `/tahiti`. The Leader Board replaces the CRM revenue tracker as the main page; `/crm` stays as it was.

### Terros fields on the board
- Knocks, pitches, days worked: KPI v2 as on the Terros board.
- **Sits** = KPI v2 column 5 (`sras`). Switch `SITS_FROM = "acs"` in `api/leaderboard.py` if the office means set appointments instead.
- **Hours worked** and **first knock** are not in KPI v2. They come from `activity/list` (cursor paging, see above): per rep per day, first action = first knock, first→last action span = hours (minimum 0.25 h for a one-action day). Finished days are cached for the life of the process (`_day_cache`), so only today is re-read; a month's first read is ~30 pages (~40 s). "First knock" on the board is always **yesterday's** (last full CDT day).
- The roster hides reps with standing Inactive (no sale in 30 days, no Terros activity) behind a "Show N inactive" button, because the Odoo roster carries ~200 names.
- Terros names are joined to Odoo names by `norm()` (lowercase letters only); a rep whose Terros name differs from the Odoo roster name gets no Terros readings — fix the name in Terros or Odoo rather than in code.

### Nav
`vivid-crm-dashboard.html` links to `/leaderboard` from the sidebar, main tabs and mobile bar. The old **Terros Board** tab is hidden (`style="display:none"`), not removed.

### Tahiti Trip Tracker (`/tahiti`)
Same rules as the Incentives tab on `/crm`: Apr 17 – Dec 31, 2026; Market Directors = contract value on Won accounts closed, self-gen counted twice, targets $1.6M / $1.8M; Market Coordinators = contract value on accounts set, counted once, targets $640K / $800K (`TARGETS` in `tahiti.html`). Pace projects the current daily rate to Dec 31: Ahead reaches Target 2, On pace reaches Target 1, else Behind.

### Onboarding (`/onboarding`)
Same data as the Onboarding tab on `/crm` (`odoo.get_onboarding`): employees in Pre-Boarding (`status1`) or Onboarding (`status2`), the open "W-9 Submission in Gusto" activity, unsigned signature requests. Names link to the Odoo employee record (`/odoo/employees/<id>`).

### Top nav on the new pages
Overview · Teams · Reps (the Leader Board, `#overview` etc.) · Tahiti (`/tahiti`) · Onboarding (`/onboarding`). No "Dashboard" link — the Leader Board is the main page now: signing in, `/`, `/dashboard` and an already-signed-in visit to `/login` all land on `/leaderboard` (`HOME` in `server.py`). `/crm` stays reachable by URL.

### Phone layout (under 700px)
The three pages keep their desktop/iPad layout untouched above 700px. Under 700px a phone layer takes over: everything lives in the `@media (max-width: 700px)` block of leaderboard.html's stylesheet (copied into tahiti.html and onboarding.html by the build scripts) plus the `*Phone` renderers in each page's script, switched by `isPhone()` (re-rendered when the window crosses 700px). What changes on a phone: one-line header (logo, theme, refresh, sign-out icons), the top nav is hidden and a fixed **bottom tab bar** (`.tabbar`: Overview · Teams · Reps · Tahiti · Onboarding) takes its place; tables become list rows (`.prow`) with the number on the right and a thin bar; the weekly bonus grid becomes one row per coordinator with a dot per week (`.wdot`); the rep drawer becomes a bottom sheet; Onboarding shows a card per person. Desktop markup and CSS outside that media block must not be edited for phone fixes.

### Vivid V watermark (all three pages)
A large red Vivid V (from the brand PDF, inlined as an SVG data URI) sits fixed in the middle of the screen behind everything: `body::before` in leaderboard.html's stylesheet, copied into tahiti.html and onboarding.html by the build scripts. Strength is one variable, `--mark-opacity` (.14 light, .22 dark). Cards are 82% opaque (`color-mix`) so the mark reads as one logo; browsers without `color-mix` keep solid cards. On phones the mark is 115vw wide and shifted slightly left so the V's point sits mid-screen. Hidden when printing.

### Favicon (every page)
The Vivid V is the browser-tab icon and the phone home-screen icon. Files live in `icons/` (favicon.svg, favicon.ico with 16/32/48, favicon-32.png, apple-touch-icon.png 180 px on white, icon-192/512.png) and are served by `server.py` **without sign-in** (`ICON_FILES`), because browsers request them before anyone logs in; every other route stays behind auth. Each page's `<head>` links `/favicon.ico`, `/favicon.svg` and `/apple-touch-icon.png`: leaderboard, tahiti, onboarding, vivid-crm-dashboard, login, vivid-terros-dashboard, and commissions (in `commissions-src/part1.html` — rebuild with `python commissions-src/build.py`).

