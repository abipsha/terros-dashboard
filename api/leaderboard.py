"""
Leader Board data — one call that assembles everything the /leaderboard page
shows for a date range:

  deals    Won accounts from Odoo whose closing date falls in the range,
           widened to whole Sunday–Saturday weeks so the weekly bonus can be
           worked out for every week that touches the range
  roster   The Sales roster from Odoo (via commissions.employees_now): name,
           team, role (Market Director / Market Coordinator), active flag
  terros   Per-rep field activity from Terros for the range (knocks, pitches,
           sits, hours, days worked) and for yesterday (plus first knock), keyed
           by a normalised name so the page can join it to Odoo names

Revenue is contract value on Won accounts by closing date, the same rule the
revenue tracker and the commission ledger use. Hours and first knock are not
in Terros's KPI report, so they come from the activity list: a rep's first
knock is the earliest action they logged that day, and hours worked is the
span from first to last action, day by day.
"""
import re
import time
from datetime import datetime, timedelta, timezone

import odoo
import terros

try:
    import commissions
except Exception as _e:          # the ledger is optional; the roster falls back to the deals
    commissions = None
    print(f"[leaderboard] commissions module unavailable: {_e}")

TERROS_TZ = timezone(timedelta(hours=-5))   # Terros works in CDT; see CLAUDE.md
CACHE_TTL = 300
_cache: dict = {}
_hours_cache: dict = {}          # "start:end" -> (ts, {name: {...}})

# Teams shown on the board. Inside Sales books appointments but is not a
# field team, so it is not a tab of its own; its reps still appear on rows.
TEAMS = ["Northern Utah", "Eastern Idaho", "Southern Utah", "Northern California"]

SITS_FROM = "sras"               # Terros "SRA" = sat/ran appointment; swap to "acs" if the office means set appointments


# ── helpers ──────────────────────────────────────────────────────

def norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _s(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def sunday_on_or_before(s: str) -> str:
    d = _d(s)
    return _s(d - timedelta(days=(d.weekday() + 1) % 7))


def saturday_on_or_after(s: str) -> str:
    d = _d(s)
    return _s(d + timedelta(days=(5 - d.weekday()) % 7))


def _cdt_bounds(day: str):
    """Millisecond bounds of one calendar day in Terros's timezone."""
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TERROS_TZ)
    end = start + timedelta(days=1) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _today_cdt() -> datetime:
    return datetime.now(tz=TERROS_TZ).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


# ── roster ───────────────────────────────────────────────────────

def _role_of(e: dict) -> str:
    text = " ".join([e.get("job") or "", e.get("tags") or ""]).lower()
    if "closer" in text or "director" in text:
        return "Market Director"
    if "setter" in text or "canvass" in text or "coordinator" in text:
        return "Market Coordinator"
    return ""


def roster():
    if not commissions:
        return []
    try:
        emps = commissions.employees_now()
    except Exception as e:
        print(f"[leaderboard] roster unavailable: {e}")
        return []
    out = []
    for e in emps:
        if not e.get("name"):
            continue
        out.append({
            "name": e["name"],
            "team": e.get("team") or "",
            "role": _role_of(e),
            "active": bool(e.get("active", True)),
            "manager": e.get("manager") or "",
            "start": e.get("start") or "",
            "odooId": e.get("odooId"),
        })
    return out


# ── Terros ───────────────────────────────────────────────────────

def _kpi_by_name(start_ms: int, end_ms: int) -> dict:
    resp = terros.get_kpi_v2(start_ms, end_ms)
    if resp.get("type") == "error":
        raise RuntimeError(resp.get("message"))
    out = {}
    for row in resp.get("rows", []):
        if not row or not isinstance(row[0], dict):
            continue
        u = row[0]
        name = ((u.get("preferredName") or u.get("firstName") or "") + " " + (u.get("lastName") or "")).strip()
        rec = {
            "name": name,
            "knocks": int(row[2] or 0) if len(row) > 2 else 0,
            "pitches": int(row[1] or 0) if len(row) > 1 else 0,
            "acs": int(row[3] or 0) if len(row) > 3 else 0,
            "sras": int(row[5] or 0) if len(row) > 5 else 0,
            "days": int(row[6] or 0) if len(row) > 6 else 0,
        }
        rec["sits"] = rec[SITS_FROM]
        out[norm(name)] = rec
    return out


def _activity_spans(start_ms: int, end_ms: int) -> dict:
    """{normName: {name, hours, firstKnockMs, perDay}} from the activity list.

    For every rep and every day, the first action is the first knock and the
    span from first to last action is the hours worked (a single action on a
    day counts as a quarter hour, so a rep who knocked once is not shown as
    zero hours)."""
    acts = terros.get_activities(start_ms, end_ms)
    per = {}
    for a in acts:
        ts = a.get("timestamp") or 0
        if not ts:
            continue
        u = a.get("user") or {}
        name = ((u.get("preferredName") or u.get("firstName") or "") + " " + (u.get("lastName") or "")).strip()
        if not name:
            name = a.get("userId") or ""
        day = datetime.fromtimestamp(ts / 1000, tz=TERROS_TZ).strftime("%Y-%m-%d")
        rec = per.setdefault(norm(name), {"name": name, "days": {}})
        d = rec["days"].setdefault(day, [ts, ts, 0])
        d[0] = min(d[0], ts)
        d[1] = max(d[1], ts)
        d[2] += 1
    out = {}
    for k, rec in per.items():
        hours = 0.0
        first_ms = None
        for day, (lo, hi, n) in rec["days"].items():
            span = (hi - lo) / 3600000.0
            hours += max(span, 0.25)
            first_ms = lo if first_ms is None else min(first_ms, lo)
        out[k] = {"name": rec["name"], "hours": round(hours, 1),
                  "firstKnockMs": first_ms, "daysActive": len(rec["days"]),
                  "perDay": {day: {"first": v[0], "last": v[1], "actions": v[2]} for day, v in rec["days"].items()}}
    return out


def _fmt_time(ms) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=TERROS_TZ).strftime("%I:%M %p").lstrip("0")


def terros_block(start: str, end: str, force: bool = False) -> dict:
    """Period KPIs, period hours, and yesterday's activity, keyed by normalised name."""
    period = {"ok": True, "byName": {}, "error": ""}
    yday = {"ok": True, "date": "", "byName": {}, "error": ""}

    s_ms, _ = _cdt_bounds(start)
    _, e_ms = _cdt_bounds(end)
    try:
        period["byName"] = _kpi_by_name(s_ms, e_ms)
    except Exception as e:
        period.update(ok=False, error=str(e))

    # hours for the period: the activity list is slow, so cache it for an hour
    key = f"{start}:{end}"
    now = time.time()
    hit = _hours_cache.get(key)
    if hit and now - hit[0] < 3600 and not force:
        spans = hit[1]
    else:
        try:
            spans = _activity_spans(s_ms, e_ms)
            _hours_cache[key] = (now, spans)
        except Exception as e:
            print(f"[leaderboard] period activity failed: {e}")
            spans = hit[1] if hit else {}
    for k, v in spans.items():
        rec = period["byName"].setdefault(k, {"name": v["name"], "knocks": 0, "pitches": 0, "acs": 0, "sras": 0, "sits": 0, "days": 0})
        rec["hours"] = v["hours"]
        rec["daysActive"] = v["daysActive"]

    # yesterday: the last full day in Terros's timezone
    y = _today_cdt() - timedelta(days=1)
    yday["date"] = _s(y)
    ys, ye = _cdt_bounds(yday["date"])
    try:
        kp = _kpi_by_name(ys, ye)
        sp = _activity_spans(ys, ye)
        for k, rec in kp.items():
            rec["hours"] = sp.get(k, {}).get("hours", 0)
            rec["firstKnock"] = _fmt_time(sp.get(k, {}).get("firstKnockMs"))
        for k, v in sp.items():
            if k not in kp:
                kp[k] = {"name": v["name"], "knocks": 0, "pitches": 0, "acs": 0, "sras": 0, "sits": 0,
                         "days": 1, "hours": v["hours"], "firstKnock": _fmt_time(v["firstKnockMs"])}
        yday["byName"] = kp
    except Exception as e:
        yday.update(ok=False, error=str(e))

    return {"period": period, "yesterday": yday}


# ── the board ────────────────────────────────────────────────────

def build(start: str, end: str, force: bool = False) -> dict:
    key = f"{start}|{end}"
    now = time.time()
    if not force and key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]

    wk_start = sunday_on_or_before(start)
    wk_end = saturday_on_or_after(end)
    deals_resp = odoo.get_deals_list(wk_start, wk_end, force=force)
    deals = [
        {
            "id": d["id"], "name": d["name"], "closer": d["closer"], "setter": d["setter"],
            "rev": d["rev"], "date": d.get("date_deadline") or d.get("date_closed") or "",
            "team": d.get("office") or "", "adders": d.get("adders", 0), "stage": d.get("stage", ""),
        }
        for d in deals_resp.get("deals", [])
    ]

    # the month before the range, for the "last month" comparison on the cards
    ps = _d(start)
    prev_end = ps - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    try:
        prev = odoo.get_deals_list(_s(prev_start), _s(prev_end), force=False)
        prev_deals = [{"closer": d["closer"], "setter": d["setter"], "rev": d["rev"], "team": d.get("office") or "",
                       "date": d.get("date_deadline") or "", "adders": d.get("adders", 0)} for d in prev.get("deals", [])]
    except Exception as e:
        print(f"[leaderboard] previous month unavailable: {e}")
        prev_deals = []

    # six months of monthly totals for the trend, from the cache where possible
    months = []
    cur = ps.replace(day=1)
    for i in range(6):
        m_start = cur
        m_end = (cur.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        try:
            r = odoo.get_deals_list(_s(m_start), _s(m_end), force=False)
            by_team = {}
            for d in r.get("deals", []):
                t = d.get("office") or ""
                by_team[t] = by_team.get(t, 0.0) + d["rev"]
            months.append({"month": m_start.strftime("%Y-%m"), "total": round(r.get("total_rev", 0), 2),
                           "deals": r.get("total_deals", 0), "byTeam": by_team})
        except Exception as e:
            months.append({"month": m_start.strftime("%Y-%m"), "total": None, "deals": None, "byTeam": {}, "error": str(e)})
        cur = (cur - timedelta(days=1)).replace(day=1)
    months.reverse()

    out = {
        "generatedAt": datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "period": {"start": start, "end": end, "weeksStart": wk_start, "weeksEnd": wk_end},
        "previous": {"start": _s(prev_start), "end": _s(prev_end), "deals": prev_deals},
        "teams": TEAMS,
        "roster": roster(),
        "deals": deals,
        "dealsStale": bool(deals_resp.get("stale")),
        "months": months,
        "terros": terros_block(start, end, force=force),
        "odooBase": "https://myvivid.odoo.com/odoo/crm/",
    }
    _cache[key] = (now, out)
    return out
