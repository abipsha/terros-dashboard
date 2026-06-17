"""
Terros API client — wraps all calls to https://api.terros.com
"""
import urllib.request
import urllib.error
import json
import os
from datetime import datetime, timezone, timedelta

API_BASE = "https://api.terros.com"
# Set TERROS_API_KEY environment variable in production (Render, etc.)
# Falls back to the hardcoded key for local use.
API_KEY  = os.environ.get("TERROS_API_KEY", "atQjJCg13du0c4aAeU4hc")


def _post(path: str, body: dict) -> dict:
    """Make a POST request to the Terros API and return parsed JSON."""
    url  = API_BASE + path
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"ApiKey {API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read()) if e.fp else {"type": "error", "message": str(e)}
    except Exception as e:
        return {"type": "error", "message": str(e)}


# ─── Users ────────────────────────────────────────────────────────────────────

def get_users() -> list:
    """Return all non-system users."""
    resp = _post("/user/list", {"showArchived": "all"})
    users = resp.get("users", [])
    return [u for u in users if not u.get("userId", "").startswith("U:system")]


# ─── Workflow ─────────────────────────────────────────────────────────────────

_workflow_cache  = None
_teams_cache     = None     # list of active team IDs
_report_cache: dict = {}   # key: "start_ms:end_ms" → (timestamp, report)
CACHE_TTL_SECS = 300       # 5-minute cache

def get_teams() -> list:
    """Return all active (non-archived) team IDs (cached)."""
    global _teams_cache
    if _teams_cache is None:
        resp = _post("/team/list", {})
        teams = resp.get("teams", [])
        _teams_cache = [t["teamId"] for t in teams if not t.get("isArchived", False)]
        print(f"  Loaded {len(_teams_cache)} active teams")
    return _teams_cache


def get_workflow() -> dict:
    """Return the first workflow (cached)."""
    global _workflow_cache
    if _workflow_cache is None:
        resp = _post("/workflow/list", {})
        workflows = resp.get("workflows", [])
        if not workflows:
            raise RuntimeError("No workflows found")
        _workflow_cache = workflows[0]
    return _workflow_cache


def get_action_map() -> dict:
    """Return {actionId: {name, short, deleted}} for every action in the workflow."""
    wf = get_workflow()
    return {
        a["actionId"]: {
            "name":    a.get("name", ""),
            "short":   a.get("short", a.get("name", "")),
            "deleted": a.get("isDeleted", False),
        }
        for a in wf.get("actions", [])
    }


# ─── Activity ─────────────────────────────────────────────────────────────────

def get_activities(start_ms: int, end_ms: int) -> list:
    """
    Fetch activity records for the date range using offset pagination.
    Client-side timestamp filter is applied as a hard guarantee regardless
    of whether the API's date filter works.
    """
    wf_id     = get_workflow()["workflowId"]
    results   = []
    PAGE_SIZE = 1000
    MAX_PAGES = 100          # safety cap (100k records max per request)

    print(f"  Fetching activities {_fmt_ts(start_ms)} → {_fmt_ts(end_ms)} …")

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        body = {
            "workflowId": wf_id,
            "startDate":  start_ms,   # top-level (some API versions)
            "endDate":    end_ms,
            "filter": {               # nested (other API versions)
                "startDate": start_ms,
                "endDate":   end_ms,
            },
            "limit":  PAGE_SIZE,
            "offset": offset,
        }

        resp = _post("/activity/list", body)

        if resp.get("type") == "error":
            print(f"  [activity/list] Error on page {page+1}: {resp.get('message')}")
            break

        actions = resp.get("actions", resp.get("activities", resp.get("data", [])))
        total   = resp.get("total", None)

        if not actions:
            print(f"  Page {page+1}: 0 records — done.")
            break

        # Strip system/transition records — A.Transition is emitted alongside
        # every real action and would double-count everything.
        real_actions = [
            a for a in actions
            if not (a.get("actionId") or "").startswith("A.Transition")
            and (a.get("actionId") or "") != ""
        ]

        # Client-side date filter — hard guarantee regardless of API behavior
        in_range = [
            a for a in real_actions
            if start_ms <= (a.get("timestamp") or 0) <= end_ms
        ]

        skipped     = len(actions) - len(real_actions)
        out_of_range = len(real_actions) - len(in_range)

        results.extend(in_range)
        print(f"  Page {page+1}: {len(actions)} raw → {len(real_actions)} real → "
              f"{len(in_range)} in range"
              f"{f' ({skipped} system, {out_of_range} date-filtered)' if skipped or out_of_range else ''}"
              f"  [total so far: {len(results)}" + (f" / ~{total}" if total else "") + "]")

        # Stop if we got fewer records than the page size (last page)
        if len(actions) < PAGE_SIZE:
            print(f"  Last page.")
            break

        # If API told us the total, stop when we've fetched it all
        if total is not None and (page + 1) * PAGE_SIZE >= total:
            print(f"  Reached API total ({total} records).")
            break

        # Stop if ALL records on this page are before our start date
        # (assumes API returns newest-first)
        timestamps = [a.get("timestamp", 0) for a in real_actions if a.get("timestamp")]
        if timestamps and max(timestamps) < start_ms:
            print(f"  All records on this page are before the week start — stopping.")
            break

    else:
        print(f"  Warning: hit {MAX_PAGES}-page safety cap.")

    print(f"  Total activities in range: {len(results)}")
    return results


# ─── KPI v2 (what the Terros stats page actually uses) ───────────────────────

def get_kpi_v2(start_ms: int, end_ms: int) -> dict:
    """
    Fetch per-rep stats using POST /report/kpi with version:2.
    This is the same call the Terros stats page makes — numbers match exactly.

    Response rows are positional arrays:
      row[0]  user object  {userId, firstName, lastName, preferredName, ...}
      row[1]  pitches      account|S.a8f7HEnPXbcn7i5hYt2hi|count
      row[2]  knocks       Ratio.DYw9YWyI6-iiVk3MfTnjO
      row[3]  sets/ACs     account|S.sKssTYhP95k9n1LA3fTao|count
      row[4]  leads        account|S.WYBl4yTJCNZoNoxAGCqBL|count
      row[5]  closes/SRAs  account|S.WInKtoxDT73Vpo1QgXnzv|count
      row[6]  daysWorked   action.daysWorked

    totalRow has the same positional layout with index 0 = "Total".
    """
    wf_id  = get_workflow()["workflowId"]
    teams  = get_teams()
    filter_body = {
        "startDate":  start_ms,
        "endDate":    end_ms,
        "workflowId": wf_id,
    }
    if teams:
        filter_body["teams"] = teams
    return _post("/report/kpi", {
        "grouping": {"type": "user"},
        "filter": filter_body,
        "kpis": [
            {"value": "groupBy"},
            {"value": "account|S.a8f7HEnPXbcn7i5hYt2hi|count", "accumulator": "distinct"},  # pitches
            {"value": "Ratio.DYw9YWyI6-iiVk3MfTnjO",           "accumulator": "sum"},        # knocks
            {"value": "account|S.sKssTYhP95k9n1LA3fTao|count", "accumulator": "distinct"},   # sets/ACs
            {"value": "account|S.WYBl4yTJCNZoNoxAGCqBL|count", "accumulator": "distinct"},   # leads
            {"value": "account|S.WInKtoxDT73Vpo1QgXnzv|count", "accumulator": "distinct"},   # closes/SRAs
            {"value": "action.daysWorked",                      "accumulator": "sum"},        # days worked
        ],
        "hideSystemUsers": True,
        "version": 2,
    })


# ─── Weekly report ────────────────────────────────────────────────────────────

def build_weekly_report(start_ms: int, end_ms: int, force: bool = False) -> dict:
    """
    Fetch per-rep KPIs using /report/kpi v2 — the same endpoint the Terros
    stats page uses, so numbers match exactly.
    Results are cached for CACHE_TTL_SECS seconds.
    """
    import time
    cache_key = f"{start_ms}:{end_ms}"
    if not force and cache_key in _report_cache:
        cached_at, cached_report = _report_cache[cache_key]
        age = time.time() - cached_at
        if age < CACHE_TTL_SECS:
            print(f"  Cache hit for {_fmt_ts(start_ms)}–{_fmt_ts(end_ms)} (age {age:.0f}s)")
            return cached_report

    print(f"  Fetching KPI v2 {_fmt_ts(start_ms)} → {_fmt_ts(end_ms)} …")
    resp = get_kpi_v2(start_ms, end_ms)

    if resp.get("type") == "error":
        raise RuntimeError(f"report/kpi error: {resp.get('message')}")

    rows      = resp.get("rows", [])
    total_row = resp.get("totalRow", [])
    print(f"  Got {len(rows)} reps from report/kpi v2")

    reps = []
    for row in rows:
        if not row or not isinstance(row[0], dict):
            continue
        user    = row[0]
        pitches = row[1] if len(row) > 1 else None
        knocks  = row[2] if len(row) > 2 else None
        acs     = row[3] if len(row) > 3 else None
        leads   = row[4] if len(row) > 4 else None
        sras    = row[5] if len(row) > 5 else None
        days    = row[6] if len(row) > 6 else None

        uid        = user.get("userId", "")
        first_name = user.get("preferredName") or user.get("firstName") or ""
        last_name  = user.get("lastName", "")
        name       = (first_name + " " + last_name).strip() or uid

        reps.append({
            "userId":     uid,
            "name":       name,
            "knocks":     int(knocks  or 0),
            "pitches":    int(pitches or 0),
            "leads":      int(leads   or 0),
            "acs":        int(acs     or 0),
            "sras":       int(sras    or 0),
            "daysWorked": int(days    or 0),
            "firstPitch": None,
            "lastPitch":  None,
            "rawActions": {},
        })

    reps.sort(key=lambda r: r["pitches"], reverse=True)

    # Use totalRow for team totals if available (same positional layout, index 0 = "Total")
    if total_row and len(total_row) > 5:
        total_pitches = int(total_row[1] or 0)
        total_knocks  = int(total_row[2] or 0)
        total_acs     = int(total_row[3] or 0)
        total_leads   = int(total_row[4] or 0)
        total_sras    = int(total_row[5] or 0)
    else:
        total_pitches = sum(r["pitches"] for r in reps)
        total_knocks  = sum(r["knocks"]  for r in reps)
        total_acs     = sum(r["acs"]     for r in reps)
        total_leads   = sum(r["leads"]   for r in reps)
        total_sras    = sum(r["sras"]    for r in reps)

    report = {
        "weekStart":   _fmt_ts(start_ms),
        "weekEnd":     _fmt_ts(end_ms),
        "rosterCount": len(reps),
        "activeCount": sum(1 for r in reps if r["pitches"] > 0 or r["knocks"] > 0),
        "totals": {
            "knocks":  total_knocks,
            "pitches": total_pitches,
            "leads":   total_leads,
            "acs":     total_acs,
            "sras":    total_sras,
        },
        "actionMap": {},
        "reps":       reps,
    }

    _report_cache[cache_key] = (time.time(), report)
    return report


# ─── KPI report (legacy debug endpoint) ──────────────────────────────────────

def get_kpi_report(start_ms: int, end_ms: int) -> dict:
    """Raw KPI v2 call — returns the same data as build_weekly_report, useful for debugging."""
    return get_kpi_v2(start_ms, end_ms)


# ─── Helpers ──────────────────────────────────────────────────────────────────

_TERROS_TZ = timezone(timedelta(hours=-5))  # CDT = UTC-5

def _fmt_ts(ms: int) -> str:
    """Format a millisecond timestamp as YYYY-MM-DD in Terros's timezone (CDT)."""
    dt = datetime.fromtimestamp(ms / 1000, tz=_TERROS_TZ)
    return dt.strftime("%Y-%m-%d")


def _fmt_time(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%I:%M %p")
