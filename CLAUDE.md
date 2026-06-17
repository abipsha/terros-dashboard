# Vivid Terros Dashboard — Dev Notes

## Architecture

- `api/terros.py` — Terros API client (Python stdlib only, no pip)
- `api/server.py` — HTTP server on port 8000; serves HTML at `/` and JSON at `/api/*`
- `vivid-terros-dashboard.html` — single-file dashboard; fetches from `http://localhost:8000/api/weekly`
- Launch: double-click `api/Start API Server.bat`

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

### Pagination uses offset, not cursor
The API's `id`/`actionId` field on activity records is the **action type ID** (e.g. "Not Home"), not a unique record ID. Cursor-based pagination with it loops forever. Always use `offset: page * PAGE_SIZE`.

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
