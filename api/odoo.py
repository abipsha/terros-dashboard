"""
Vivid Odoo CRM Client
Fetches won deals from myvivid.odoo.com and aggregates into dashboard data.
Python stdlib only — no pip installs needed.
Uses XML-RPC for auth (bypasses IP restrictions on the web session endpoint).
"""
import json
import os
import time
import xmlrpc.client
import http.cookiejar
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

ODOO_URL   = os.environ.get("ODOO_URL",      "https://myvivid.odoo.com")
ODOO_DB    = os.environ.get("ODOO_DB",       "myvivid")
ODOO_USER  = os.environ.get("ODOO_USER",     "abipsha.joshi@vividwindows.com")
ODOO_PASS  = os.environ.get("ODOO_PASSWORD", "")

# XML-RPC proxies for external API (bypasses web session IP restrictions)
_xmlrpc_common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
_xmlrpc_models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

# Persistent cookie jar so Odoo session survives across requests (fallback)
_jar    = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_uid    = None   # cached after first auth

# Revenue goals per office per month (USD) — edit here or override via env
OFFICE_GOALS = {
    "Northern Utah":      float(os.environ.get("GOAL_NORTHERN_UTAH",      "500000")),
    "Southern Utah":      float(os.environ.get("GOAL_SOUTHERN_UTAH",      "500000")),
    "Eastern Idaho":      float(os.environ.get("GOAL_EASTERN_IDAHO",      "400000")),
    "Northern California": float(os.environ.get("GOAL_NORTHERN_CALIFORNIA","400000")),
    "Inside Sales":       float(os.environ.get("GOAL_INSIDE_SALES",        "200000")),
}
DEFAULT_GOAL = float(os.environ.get("GOAL_DEFAULT", "300000"))

# Cache
_cache: dict = {}
_stale_cache: dict = {}   # last known-good data — never expires, served on API failure
CACHE_TTL = 120  # 2 minutes


def _post(endpoint: str, payload: dict) -> dict:
    """POST via the persistent cookie-bearing opener."""
    url  = ODOO_URL + endpoint
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": ODOO_URL + "/web",
            "Origin": ODOO_URL,
        }
    )
    with _opener.open(req, timeout=30) as r:
        return json.loads(r.read())


def authenticate() -> int:
    """Authenticate via XML-RPC and cache uid."""
    global _uid
    uid = _xmlrpc_common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
    if not uid:
        raise RuntimeError("Odoo XML-RPC auth failed: bad credentials or access denied")
    _uid = uid
    print(f"  Odoo authenticated via XML-RPC (uid={uid})")
    return uid


def call_kw(model: str, method: str, args: list, kwargs: dict):
    """Call Odoo XML-RPC. Re-authenticates automatically if uid is missing."""
    global _uid
    if _uid is None:
        authenticate()
    try:
        return _xmlrpc_models.execute_kw(ODOO_DB, _uid, ODOO_PASS, model, method, args, kwargs)
    except xmlrpc.client.Fault as e:
        if "session" in str(e).lower() or "access" in str(e).lower():
            print("  Odoo session issue — re-authenticating…")
            _uid = None
            authenticate()
            return _xmlrpc_models.execute_kw(ODOO_DB, _uid, ODOO_PASS, model, method, args, kwargs)
        raise RuntimeError(f"Odoo XML-RPC error: {e}")


def get_won_deals(start_date: str, end_date: str) -> list:
    """
    Fetch all won deals between start_date and end_date (YYYY-MM-DD, inclusive).
    Returns list of records with relevant fields.
    """
    domain = [
        ["stage_id.is_won", "=", True],
        ["date_deadline", ">=", start_date],
        ["date_deadline", "<=", end_date],
    ]
    fields = [
        "id", "name",
        "x_studio_contract_value",
        "x_studio_closer_text_1",
        "x_studio_canvasser_text",
        "team_id",
        "date_closed",
        "date_deadline",
        "user_id",
    ]
    records = []
    offset  = 0
    limit   = 200
    while True:
        batch = call_kw("crm.lead", "search_read", [domain], {
            "fields": fields, "limit": limit, "offset": offset,
            "order": "date_closed desc",
            "context": {"active_test": False},
        })
        if not batch:
            break
        records.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return records


def _office_name(team_id_val):
    if not team_id_val:
        return "Unknown"
    return team_id_val[1] if isinstance(team_id_val, (list, tuple)) else str(team_id_val)


def get_deals_list(start_date: str, end_date: str, force: bool = False) -> dict:
    """
    Return all won deals in the date range as flat records — matching the
    two-tab spreadsheet view (Closers / Setters).
    Cached for CACHE_TTL seconds. On API failure, serves last known-good data
    (stale cache) so the dashboard stays populated until the key is renewed.
    """
    cache_key = f"deals|{start_date}|{end_date}"
    now = time.time()
    if not force and cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < CACHE_TTL:
            return data

    try:
        domain = [
            ["stage_id.is_won", "=", True],
            ["date_deadline", ">=", start_date],
            ["date_deadline", "<=", end_date],
        ]
        fields = [
            "id", "name",
            "x_studio_closer_text_1",
            "x_studio_canvasser_text",
            "x_studio_contract_value",
            "x_studio_installation_date",
            "x_studio_street",
            "x_studio_city",
            "x_studio_state",
            "date_closed",
            "date_deadline",
            "team_id",
            "tag_ids",
        ]

        records = []
        offset, limit = 0, 200
        while True:
            batch = call_kw("crm.lead", "search_read", [domain], {
                "fields": fields, "limit": limit, "offset": offset,
                "order": "date_deadline desc",
                "context": {"active_test": False},
            })
            if not batch:
                break
            records.extend(batch)
            if len(batch) < limit:
                break
            offset += limit

        # Fetch tag names
        all_tag_ids = list({tid for r in records for tid in (r.get("tag_ids") or [])})
        tag_map = {}
        if all_tag_ids:
            tags = call_kw("crm.tag", "read", [all_tag_ids, ["id", "name"]], {})
            tag_map = {t["id"]: t["name"] for t in (tags or [])}

        # Clean up records
        clean = []
        for r in records:
            clean.append({
                "id":           r["id"],
                "name":         r.get("name") or "",
                "closer":       r.get("x_studio_closer_text_1") or "",
                "setter":       r.get("x_studio_canvasser_text") or "",
                "rev":          float(r.get("x_studio_contract_value") or 0),
                "install_date": (r.get("x_studio_installation_date") or "")[:10],
                "street":       r.get("x_studio_street") or "",
                "city":         r.get("x_studio_city") or "",
                "state":        r.get("x_studio_state") or "",
                "date_closed":  (r.get("date_closed") or "")[:10],
                "date_deadline": (r.get("date_deadline") or "")[:10],
                "office":       _office_name(r.get("team_id")),
                "tags":         [tag_map.get(tid, str(tid)) for tid in (r.get("tag_ids") or [])],
            })

        result = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "date_range":   {"start": start_date, "end": end_date},
            "total_deals":  len(clean),
            "total_rev":    round(sum(r["rev"] for r in clean), 2),
            "deals":        clean,
            "stale":        False,
        }
        _cache[cache_key] = (now, result)
        _stale_cache[cache_key] = result   # save as last known-good
        return result

    except Exception as e:
        # API key expired or network error — serve last known-good data
        if cache_key in _stale_cache:
            stale = dict(_stale_cache[cache_key])
            stale["stale"] = True
            stale["stale_since"] = datetime.utcnow().isoformat() + "Z"
            print(f"  [odoo] API error, serving stale cache: {e}")
            return stale
        # No stale data available yet — re-raise so the caller can surface the error
        raise


def build_report(start_date: str, end_date: str, force: bool = False) -> dict:
    """
    Build the aggregated CRM report for the given date range.
    Cached for CACHE_TTL seconds. On API failure, serves last known-good data.
    """
    cache_key = f"{start_date}|{end_date}"
    now = time.time()
    if not force and cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < CACHE_TTL:
            return data

    try:
        records = get_won_deals(start_date, end_date)

        # Compute current-week range for "weekly wins"
        today = datetime.now(tz=timezone.utc)
        week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")

        # Aggregate
        by_office:  dict = {}
        by_closer:  dict = {}
        by_setter:  dict = {}
        total_rev   = 0.0
        total_wins  = 0

        for r in records:
            office = _office_name(r.get("team_id"))
            closer = r.get("x_studio_closer_text_1") or "Unknown"
            setter = r.get("x_studio_canvasser_text") or "Unknown"
            rev    = float(r.get("x_studio_contract_value") or 0)
            closed = (r.get("date_closed") or "")[:10]
            is_this_week = closed >= week_start

            total_rev  += rev
            total_wins += 1

            if office not in by_office:
                by_office[office] = {"office": office, "rev": 0.0, "wins": 0, "weekly_wins": 0, "reps": {}}
            by_office[office]["rev"]   += rev
            by_office[office]["wins"]  += 1
            if is_this_week:
                by_office[office]["weekly_wins"] += 1

            reps = by_office[office]["reps"]
            if closer not in reps:
                reps[closer] = {"name": closer, "mtd_rev": 0.0, "wins": 0, "weekly_wins": 0}
            reps[closer]["mtd_rev"]  += rev
            reps[closer]["wins"]     += 1
            if is_this_week:
                reps[closer]["weekly_wins"] += 1

            if closer not in by_closer:
                by_closer[closer] = {"name": closer, "rev": 0.0, "wins": 0}
            by_closer[closer]["rev"]  += rev
            by_closer[closer]["wins"] += 1

            if setter not in by_setter:
                by_setter[setter] = {"name": setter, "rev": 0.0, "wins": 0}
            by_setter[setter]["rev"]  += rev
            by_setter[setter]["wins"] += 1

        offices_sorted  = sorted(by_office.values(), key=lambda x: x["rev"], reverse=True)
        closers_sorted  = sorted(by_closer.values(), key=lambda x: x["rev"], reverse=True)
        setters_sorted  = sorted(by_setter.values(), key=lambda x: x["rev"], reverse=True)

        dt_today       = datetime.now(tz=timezone.utc)
        dt_month_start = dt_today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        import calendar
        days_in_month = calendar.monthrange(dt_today.year, dt_today.month)[1]
        days_elapsed  = (dt_today - dt_month_start).days + 1

        for office in offices_sorted:
            goal = OFFICE_GOALS.get(office["office"], DEFAULT_GOAL)
            pace = (office["rev"] / days_elapsed * days_in_month) if days_elapsed > 0 else 0
            office["goal"]         = goal
            office["pace_to_goal"] = round(pace, 2)
            office["goal_pct"]     = round(office["rev"] / goal * 100, 1) if goal > 0 else 0

            reps_list = sorted(office["reps"].values(), key=lambda x: x["mtd_rev"], reverse=True)
            for rep in reps_list:
                rep_goal = goal / max(len(reps_list), 1)
                rep_pace = (rep["mtd_rev"] / days_elapsed * days_in_month) if days_elapsed > 0 else 0
                rep["goal"]         = round(rep_goal, 2)
                rep["pace_to_goal"] = round(rep_pace, 2)
                rep["goal_pct"]     = round(rep["mtd_rev"] / rep_goal * 100, 1) if rep_goal > 0 else 0
            office["reps"] = reps_list

        result = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "date_range":   {"start": start_date, "end": end_date},
            "summary": {
                "total_rev":     round(total_rev, 2),
                "total_wins":    total_wins,
                "days_elapsed":  days_elapsed,
                "days_in_month": days_in_month,
            },
            "top_office": offices_sorted[0] if offices_sorted else None,
            "top_closer": closers_sorted[0] if closers_sorted else None,
            "top_setter": setters_sorted[0] if setters_sorted else None,
            "offices":    offices_sorted,
            "closers":    closers_sorted[:10],
            "setters":    setters_sorted[:10],
            "stale":      False,
        }

        _cache[cache_key] = (now, result)
        _stale_cache[cache_key] = result   # save as last known-good
        return result

    except Exception as e:
        if cache_key in _stale_cache:
            stale = dict(_stale_cache[cache_key])
            stale["stale"] = True
            stale["stale_since"] = datetime.utcnow().isoformat() + "Z"
            print(f"  [odoo] API error, serving stale cache: {e}")
            return stale
        raise
