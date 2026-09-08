"""
Vivid Windows - commission ledger
=================================================================
Serves the commission dashboard at /commissions, inside the same app that
serves /crm, behind the same Google sign-in.

Two things this module is careful about:

1. The browser is only ever sent data the signed-in person may see. A rep's
   page receives that rep's deals and nothing else, so there is nothing extra
   to find in devtools. The one figure computed from other people's deals -
   the recruiting bonus - is worked out here and sent as finished lines.

2. It cannot take the CRM dashboard down. Everything below is wrapped; if the
   dataset is missing or Odoo is unreachable, /commissions returns 503 and the
   rest of the service carries on. See `_FAILED`.

Storage is Odoo: every hold, release, adjustment, change order and pay-run
freeze is a row in x_commission_event, written through the existing
api/odoo.py client, plus a log note in the chatter. No new credentials - it
reuses ODOO_USER / ODOO_PASSWORD, already set on this service.

Python stdlib only.
"""

import json
import os
import time
import traceback
from datetime import datetime, timezone, timedelta

import odoo   # the existing client: call_kw() handles auth, retries, XML-RPC

# ── where things live ────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_ROOT      = os.path.dirname(_HERE)
PAGE_FILE   = os.environ.get("COMMISSIONS_PAGE",   os.path.join(_ROOT, "commissions.html"))
DATA_FILE   = os.environ.get("COMMISSIONS_DATA",   os.path.join(_HERE, "commissions_data.json"))
PEOPLE_FILE = os.environ.get("COMMISSIONS_PEOPLE", os.path.join(_HERE, "commissions_people.json"))

EVENT_MODEL = os.environ.get("ODOO_EVENT_MODEL", "x_commission_event")
JOURNAL_ID  = int(os.environ.get("ODOO_JOURNAL_ID") or 0) or None
NOTE_ON_EMPLOYEE = os.environ.get("ODOO_NOTE_EMPLOYEE", "") == "1"

# Access. By default only the people named in commissions_people.json can open
# the ledger at all. Set COMMISSIONS_REPS=1 to also let each rep open their own
# statement (their own deals and their own pay, nothing about anyone else).
REPS_ENABLED = os.environ.get("COMMISSIONS_REPS", "") == "1"

# ── plan constants, kept in step with the page ───────────────────
RATES   = {"closer": 0.09, "canvasser": 0.05, "hourly": 0.01}
RECRUIT = {"l1": {"self": 0.015, "closer": 0.0075, "setter": 0.0075},
           "l2": {"self": 0.005, "closer": 0.0025, "setter": 0.0025}}
CUTOFF  = "2026-01-01"

KINDS = ["hold", "release", "adjustment", "adjustment.remove",
         "changeorder.apply", "changeorder.decline", "changeorder.undo", "run.freeze",
         "plan.schedule", "plan.cancel"]
HOLD_REASONS = ["Customer financing not approved", "Awaiting signed change order",
                "Job cancelled - chargeback pending", "Rep eligibility under review",
                "Contract value in dispute", "Install on hold", "Other"]
ADJ_KINDS = ["Bonus", "Manual commission", "Reimbursement",
             "Chargeback", "Deduction", "Advance repayment"]

CACHE_TTL = 60          # how long an event read is reused
STALE_TTL = 600         # how long a cached read survives an Odoo outage

# ── startup: never raise out of this module ──────────────────────
_FAILED = None          # set to a reason string if the ledger cannot run
DATA    = None
PEOPLE  = None

def _check_page():
    """Refuse to serve the standalone build.

    There are two versions of commissions.html: the one that fetches
    /commissions/data (what belongs here), and a standalone prototype with the
    whole dataset compiled into it. They look identical in a browser. Serving
    the wrong one would hand every rep the entire company's pay and nothing
    would appear to be wrong - so check, once, at startup.
    """
    if not os.path.exists(PAGE_FILE):
        raise FileNotFoundError(f"{PAGE_FILE} is missing")
    with open(PAGE_FILE, "r", encoding="utf-8", errors="replace") as f:
        head = f.read()
    # keys that only ever appear in the dataset, never in the page's own markup
    if '"canvComm":' in head or '"baseImplied":' in head or 'const D = {"' in head:
        raise ValueError(
            f"{os.path.basename(PAGE_FILE)} has the dataset compiled into it - that is the "
            "standalone prototype, and serving it would send every rep the whole company's "
            "pay. Use the build that fetches /commissions/data.")
    if "/commissions/data" not in head:
        raise ValueError(
            f"{os.path.basename(PAGE_FILE)} never calls /commissions/data, so it is not the "
            "server-backed build.")


def _load():
    global DATA, PEOPLE, _FAILED
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            DATA = json.load(f)
        with open(PEOPLE_FILE, "r", encoding="utf-8") as f:
            PEOPLE = json.load(f)
        if not isinstance(DATA.get("deals"), list) or not isinstance(DATA.get("employees"), list):
            raise ValueError("the dataset is not shaped like a ledger export")
        _check_page()
        PEOPLE = {k.lower(): v for k, v in PEOPLE.items() if isinstance(v, dict)}
        _FAILED = None
    except Exception as e:
        _FAILED = f"{type(e).__name__}: {e}"
        print(f"[commissions] NOT AVAILABLE: {_FAILED}")
        print("[commissions] /commissions will return 503. The rest of this service is unaffected.")

_load()


# ═════════════════════════════════════════════════════════════════
#  Live deals from Odoo
#
#  Additive on purpose. Everything the workbook covers keeps its recorded paid
#  amounts, so the reconciled runs cannot move; Odoo only carries the ledger
#  forward from where the export stopped. A deal arriving this way has no paid
#  amount, so the page calculates it on the contract value until its run
#  freezes - see paidBasis() in commissions-src/part2.js.
# ═════════════════════════════════════════════════════════════════

LIVE_DEALS = os.environ.get("COMMISSIONS_LIVE_DEALS", "") == "1"
DEALS_TTL  = int(os.environ.get("COMMISSIONS_DEALS_TTL") or 300)
_deals_cache, _deals_at, _deals_stale = None, 0.0, False

LEAD_FIELDS = ["name", "date_deadline", "x_studio_contract_value",
               "x_studio_closer_text_1", "x_studio_canvasser_text",
               "x_studio_vivid_adder", "x_studio_windowdoor_count",
               "team_id", "stage_id"]


def _workbook_last_close():
    last = ""
    for d in DATA.get("deals") or []:
        c = d.get("close") or ""
        if d.get("status") == "Won" and c > last:
            last = c
    return last


def _rel(v):
    return v[1] if isinstance(v, list) and len(v) > 1 else ""


def _map_lead(r, tags):
    closer = (r.get("x_studio_closer_text_1") or "").strip()
    canv = (r.get("x_studio_canvasser_text") or "").strip()
    return {
        "opp": r.get("name") or "",
        "close": r.get("date_deadline") or "",
        "value": float(r.get("x_studio_contract_value") or 0),
        "closer": closer,
        "canvasser": canv,
        "canvTag": tags.get(canv, "#N/A"),
        "adder": int(r.get("x_studio_vivid_adder") or 0),
        "units": int(r.get("x_studio_windowdoor_count") or 0),
        "team": _rel(r.get("team_id")),
        "stage": _rel(r.get("stage_id")),
        "status": "Won",
        "selfGen": bool(closer) and closer == canv,
        "lost": False,
        # never paid, so no recorded basis - the page uses the contract value
        "canvComm": None, "closerComm": None,
        "notes": None, "paid": None,
        "live": True,
    }


def live_deals():
    global _deals_cache, _deals_at, _deals_stale
    if not LIVE_DEALS or _FAILED:
        return []
    now = time.time()
    if _deals_cache is not None and now - _deals_at < DEALS_TTL:
        return _deals_cache
    after = _workbook_last_close()
    try:
        rows = odoo.call_kw("crm.lead", "search_read",
                            [[["stage_id.is_won", "=", True],
                              ["date_deadline", ">", after]]],
                            {"fields": LEAD_FIELDS, "order": "date_deadline asc",
                             "limit": 2000, "context": {"active_test": False}})
        tags = {e["name"]: (e.get("tags") or "") for e in (DATA.get("employees") or []) if e.get("name")}
        out = [_map_lead(r, tags) for r in rows if r.get("name") and r.get("date_deadline")]
        _deals_cache, _deals_at, _deals_stale = out, now, False
        print(f"[commissions] {len(out)} live deals from Odoo closing after {after}")
        return out
    except Exception as e:
        if _deals_cache is not None:
            _deals_stale = True
            print(f"[commissions] Odoo unreachable, serving cached deals: {e}")
            return _deals_cache
        print(f"[commissions] live deals unavailable, serving the workbook alone: {e}")
        return []


def deals_now():
    """Every deal the ledger should consider: the workbook, plus anything Odoo
    has closed since."""
    return (DATA.get("deals") or []) + live_deals()



# ═════════════════════════════════════════════════════════════════
#  Odoo storage
# ═════════════════════════════════════════════════════════════════

_events_cache = None
_events_at    = 0.0
_events_stale = False
_lead_cache   = {}
_emp_cache    = {}
_schema_ready = False
_has_name     = False   # Studio adds a mandatory x_name (Description) to a new model


def _odoo_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _from_odoo(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return str(s)


def ensure_schema():
    """Create the seven fields this module needs, if they are not there.

    Clicking these into Studio one at a time is slow and easy to get subtly
    wrong; a mistyped technical name fails at the worst possible moment, on
    somebody's pay. Running this again is harmless.

    x_kind is a Char rather than a Selection on purpose: a Selection rejects
    any value not on its list, so adding an event kind later would need an Odoo
    change before the code could ship, and a half-finished list silently
    refuses to record a hold. The valid kinds are enforced in `_validate`.
    """
    global _schema_ready
    if _schema_ready:
        return
    want = [
        ("x_kind",    "Kind",        "char",     None),
        ("x_lead",    "Opportunity", "many2one", "crm.lead"),
        ("x_person",  "Person",      "char",     None),
        ("x_payload", "Detail",      "text",     None),
        ("x_actor",   "Entered by",  "char",     None),
        ("x_at",      "When",        "datetime", None),
        ("x_run",     "Pay run",     "char",     None),
    ]
    model = odoo.call_kw("ir.model", "search_read",
                         [[["model", "=", EVENT_MODEL]]], {"fields": ["id"], "limit": 1})
    if not model:
        raise RuntimeError(
            f"No model named {EVENT_MODEL} in Odoo. Create it in Studio, or set "
            f"ODOO_EVENT_MODEL to the technical name of the one you made.")
    model_id = model[0]["id"]

    have = odoo.call_kw("ir.model.fields", "search_read",
                        [[["model", "=", EVENT_MODEL],
                          ["name", "in", [w[0] for w in want] + ["x_name"]]]],
                        {"fields": ["name", "ttype", "state"]})
    by_name = {f["name"]: f for f in have}

    # Studio puts a mandatory Char called x_name ("Description") on every model
    # it creates. Leaving it unset makes Odoo refuse the create outright, which
    # is a write failure at the worst moment - somebody putting a hold on a
    # deal. If it is there, every event gets a readable one-line description.
    global _has_name
    _has_name = "x_name" in by_name

    # An empty model can be corrected in place; one with events in it cannot,
    # because dropping a column would take real pay history with it.
    rows = odoo.call_kw(EVENT_MODEL, "search_count", [[]], {})
    empty = rows == 0

    made, fixed, wrong = [], [], []
    for name, label, ttype, relation in want:
        existing = by_name.get(name)
        if existing:
            if existing["ttype"] == ttype:
                continue
            if empty and existing.get("state") == "manual":
                odoo.call_kw("ir.model.fields", "unlink", [[existing["id"]]], {})
                fixed.append(f"{name} (was {existing['ttype']})")
            else:
                wrong.append(f"{name} is {existing['ttype']} in Odoo, expected {ttype}")
                continue
        vals = {"model_id": model_id, "model": EVENT_MODEL, "name": name,
                "field_description": label, "ttype": ttype, "state": "manual", "store": True}
        if relation:
            vals["relation"] = relation
        odoo.call_kw("ir.model.fields", "create", [vals], {})
        made.append(name)

    if fixed:
        print(f"[commissions] replaced on {EVENT_MODEL} (model was empty): {', '.join(fixed)}")
    if made:
        print(f"[commissions] created on {EVENT_MODEL}: {', '.join(made)}")
    if wrong:
        raise RuntimeError(
            "These fields exist in Odoo with the wrong type: " + "; ".join(wrong)
            + f". The model already holds {rows} event{'' if rows == 1 else 's'}, so nothing "
            "was changed - dropping a column would take pay history with it. Sort it out in "
            "Odoo by hand, then restart.")
    _schema_ready = True
    jid = journal_id()
    print(f"[commissions] Odoo store ready: {EVENT_MODEL}"
          + (f", journal record {jid}" if jid else ", no journal record"))


def _find_lead(opp, close):
    """Match an opportunity by name, preferring one whose closing date agrees.

    Where it cannot tell two apart it returns None and the caller skips the log
    note rather than writing on the wrong customer's record. This whole step
    disappears once deals come from the Odoo API instead of the workbook.
    """
    key = f"{opp}|{close}"
    if key in _lead_cache:
        return _lead_cache[key]
    name = (opp or "").strip()
    hits = odoo.call_kw("crm.lead", "search_read", [[["name", "=", name]]],
                        {"fields": ["id", "date_deadline"], "limit": 10})
    if not hits:
        hits = odoo.call_kw("crm.lead", "search_read",
                            [[["name", "ilike", " ".join(name.split())]]],
                            {"fields": ["id", "date_deadline"], "limit": 10})
    found = None
    if len(hits) == 1:
        found = hits[0]["id"]
    elif len(hits) > 1:
        same = [h for h in hits if h.get("date_deadline") == close]
        found = same[0]["id"] if len(same) == 1 else None
    _lead_cache[key] = found
    return found


def _find_employee(name):
    if name in _emp_cache:
        return _emp_cache[name]
    hits = odoo.call_kw("hr.employee", "search_read", [[["name", "=", name]]],
                        {"fields": ["id"], "limit": 2})
    got = hits[0]["id"] if len(hits) == 1 else None
    _emp_cache[name] = got
    return got


def _esc(s):
    return (str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))



def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _pct(v):
    try:
        return f"{float(v) * 100:g}%"
    except (TypeError, ValueError):
        return "?"


def _money(n):
    try:
        n = float(n)
    except Exception:
        n = 0.0
    return ("-$" if n < 0 else "$") + f"{abs(n):,.2f}"


def _when(iso):
    try:
        dt = datetime.strptime(iso.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        # Mountain Time, without pulling in a tz library
        mt = dt - timedelta(hours=6 if 3 <= dt.month <= 10 else 7)
        return mt.strftime("%b %-d, %Y, %-I:%M %p") + " MT"
    except Exception:
        return str(iso)


def _note_for(ev):
    p = ev.get("payload") or {}
    leg = "setter" if p.get("leg") == "setter" else "closer"
    k = ev["kind"]
    if k == "hold":
        title = "Commission held"
        detail = (f"The <b>{leg}</b> commission on this deal is withheld and will not be paid "
                  "until it is released.")
        if p.get("reason"):
            detail += f"<br/>Reason: <b>{_esc(p['reason'])}</b>"
    elif k == "release":
        title = "Commission released"
        detail = (f"The <b>{leg}</b> commission on this deal is no longer withheld. It pays in "
                  "the next open run.")
    elif k == "changeorder.apply":
        title = "Change order applied"
        detail = ("The contract value moved after commission had already been paid. Setter and "
                  "closer commission, both levels of recruiting bonus and the manager override "
                  "have been recalculated, and only the difference is paid or clawed back. Runs "
                  "that already went out are unchanged.")
    elif k == "plan.schedule":
        r = p.get("rates") or {}
        title = "Commission plan scheduled"
        detail = (f"From <b>{_esc(ev.get('target'))}</b>: closer "
                  f"{_pct(r.get('closer'))}, setter {_pct(r.get('canvasser'))}, hourly setter "
                  f"{_pct(r.get('hourly'))}. Deals closing on or after that date are paid at these "
                  "rates; everything already closed keeps the rates it was sold under."
                  + (f"<br>{_esc(p.get('note'))}" if p.get("note") else ""))
    elif k == "plan.cancel":
        title = "Scheduled plan change cancelled"
        detail = (f"The plan due to start <b>{_esc(ev.get('target'))}</b> will not take effect. "
                  "Nothing had been paid at those rates.")
    elif k == "changeorder.decline":
        title = "Change order not applied"
        detail = ("The contract value moved after commission had already been paid, and the "
                  "difference was reviewed and deliberately not paid on. Nobody is paid or "
                  "clawed back for it. It can be put back in the queue later.")
    elif k == "changeorder.undo":
        title = "Change order reversed"
        detail = "Commission goes back to what the original contract value paid."
    elif k == "adjustment":
        title = "Commission adjustment"
        detail = (f"{_esc(p.get('type'))} of <b>{_money(p.get('amount'))}</b> for "
                  f"<b>{_esc(ev.get('target'))}</b>")
        detail += f", in the {_esc(p.get('run'))} pay run." if p.get("run") else "."
        if p.get("note"):
            detail += f"<br/>Note: {_esc(p['note'])}"
    elif k == "adjustment.remove":
        title = "Adjustment removed"
        detail = "An adjustment entered earlier was removed before the run was frozen."
    elif k == "run.freeze":
        title = "Pay run frozen"
        detail = (f"The <b>{_esc(ev.get('target'))}</b> run was frozen for payroll. Rates, "
                  "contract values, holds and adjustments are captured as they stand. Anything "
                  "entered from here rolls into the next open run.")
    else:
        title, detail = "Commission ledger", _esc(k)
    return (f"<p><b>{title}</b><br/>{detail}<br/>"
            f"<span style=\"color:#777\">{_esc(ev.get('actor'))} &middot; "
            f"{_esc(_when(ev.get('at')))} &middot; commission ledger</span></p>")


_journal_id, _journal_looked = None, False


def journal_id():
    """The record that adjustments and pay-run freezes post their notes to.

    ODOO_JOURNAL_ID names it outright. Failing that, look for the one record in
    the model that is not an event: every event this module writes sets x_kind,
    so the journal record is the one without it. Configuration a person has to
    remember, whose absence shows up as nothing happening, is worse than a
    lookup that says what it found."""
    global _journal_id, _journal_looked
    if JOURNAL_ID:
        return JOURNAL_ID
    if _journal_looked:
        return _journal_id
    _journal_looked = True
    try:
        rows = odoo.call_kw(EVENT_MODEL, "search_read", [[["x_kind", "in", [False, ""]]]],
                            {"fields": ["id", "x_name"], "order": "id asc", "limit": 3})
        if len(rows) == 1:
            _journal_id = rows[0]["id"]
            print(f"[commissions] journal record: {EVENT_MODEL} id {_journal_id}"
                  f" ({rows[0].get('x_name') or 'unnamed'})")
        elif not rows:
            print(f"[commissions] no journal record in {EVENT_MODEL}. Adjustment and pay-run"
                  " notes will not be posted - the events themselves still save. Create one"
                  " record with a Description and no Kind, or set ODOO_JOURNAL_ID.")
        else:
            print(f"[commissions] several records in {EVENT_MODEL} have no Kind, so the journal"
                  " record is ambiguous. Set ODOO_JOURNAL_ID to the one you want.")
    except Exception as e:
        print(f"[commissions] could not look up the journal record: {e}")
    return _journal_id


def _post_note(ev, lead_id):
    """Best effort. A failed note never fails the write - the record is what
    the ledger reads back; the note is only for people reading Odoo."""
    body = _note_for(ev)
    kw = {"body": body, "message_type": "comment", "subtype_xmlid": "mail.mt_note"}
    if lead_id:
        return odoo.call_kw("crm.lead", "message_post", [[lead_id]], kw)
    if NOTE_ON_EMPLOYEE and ev["kind"] == "adjustment" and ev.get("target"):
        emp = _find_employee(ev["target"])
        if emp:
            return odoo.call_kw("hr.employee", "message_post", [[emp]], kw)
    jid = journal_id()
    if jid:
        return odoo.call_kw(EVENT_MODEL, "message_post", [[jid]], kw)
    print(f"[commissions] no journal record, so no note was posted for {ev.get('kind')}"
          f" {ev.get('target')}. The event itself saved.")
    return None


DEAL_KINDS = ("hold", "release", "changeorder.apply", "changeorder.decline",
              "changeorder.undo")


def _event_label(kind, target, payload, is_deal):
    """A one-line description for the Odoo record - this is what somebody
    scrolling the Commission Event list actually reads."""
    p = payload or {}
    if is_deal:
        who = str(target or "").split("|")[0]
    elif kind == "run.freeze":
        who = str(target or "")
    else:
        who = str(target or "")
    bits = [kind]
    if p.get("leg"):
        bits.append(str(p["leg"]))
    if who:
        bits.append("- " + who)
    if p.get("amount") is not None:
        try:
            bits.append("{:+,.2f}".format(float(p["amount"])))
        except (TypeError, ValueError):
            pass
    return " ".join(bits)[:250]


def append_event(kind, target, payload, actor, actor_email):
    ensure_schema()
    at = _odoo_now()
    p = dict(payload or {})
    p["_actorEmail"] = actor_email or ""

    lead_id = None
    is_deal = kind in DEAL_KINDS
    if is_deal:
        p["_target"] = target
        parts = str(target or "").split("|")
        lead_id = _find_lead(parts[0], parts[1] if len(parts) > 1 else None)

    vals = {
        "x_kind": kind,
        "x_lead": lead_id or False,
        "x_person": False if (is_deal or kind == "run.freeze") else (target or False),
        "x_payload": json.dumps(p),
        "x_actor": actor,
        "x_at": at,
        "x_run": (payload or {}).get("run") or (target if kind == "run.freeze" else False),
    }
    if _has_name:
        vals["x_name"] = _event_label(kind, target, payload, is_deal)
    new_id = odoo.call_kw(EVENT_MODEL, "create", [vals], {})

    global _events_cache
    _events_cache = None

    stored = {"id": int(new_id), "kind": kind, "target": target, "payload": payload or {},
              "actor": actor, "actorEmail": actor_email, "at": _from_odoo(at)}
    try:
        _post_note(stored, lead_id)
    except Exception as e:
        print(f"[commissions] event {new_id} saved, but the Odoo log note failed: {e}")
    if is_deal and not lead_id:
        print(f"[commissions] event {new_id} saved, but no single Odoo opportunity matched "
              f"\"{str(target).split('|')[0]}\" - no note was posted.")
    return stored


def _shape(r):
    try:
        payload = json.loads(r.get("x_payload") or "{}")
    except Exception:
        payload = {}
    if r.get("x_kind") == "run.freeze":
        target = r.get("x_run")
    else:
        target = r.get("x_person") or payload.get("_target") or (
            r["x_lead"][1] if r.get("x_lead") else None)
    return {"id": int(r["id"]), "kind": r.get("x_kind"), "target": target,
            "payload": payload, "actor": r.get("x_actor"),
            "actorEmail": payload.get("_actorEmail", ""), "at": _from_odoo(r.get("x_at"))}


def all_events():
    """Read every event. Cached for a minute so a busy admin page is not
    hammering Odoo; during an outage the last good read is served for ten
    minutes and flagged, rather than showing an empty ledger."""
    global _events_cache, _events_at, _events_stale
    now = time.time()
    if _events_cache is not None and now - _events_at < CACHE_TTL:
        return _events_cache
    try:
        ensure_schema()
        rows = odoo.call_kw(EVENT_MODEL, "search_read", [[]],
                            {"fields": ["id", "x_kind", "x_lead", "x_person",
                                        "x_payload", "x_actor", "x_at", "x_run"],
                             "order": "id asc", "limit": 5000})
        _events_cache = [_shape(r) for r in rows]
        _events_at = now
        _events_stale = False
        return _events_cache
    except Exception as e:
        if _events_cache is not None and now - _events_at < STALE_TTL:
            _events_stale = True
            print(f"[commissions] Odoo unreachable, serving cached events: {e}")
            return _events_cache
        raise


# ═════════════════════════════════════════════════════════════════
#  Who sees what
# ═════════════════════════════════════════════════════════════════

def build_scope(person, role):
    if role in ("admin", "viewer"):
        return {"all": True, "regions": [], "names": [], "role": role}

    regions, names, seats = set(), {person}, []
    all_regions, held = [], {}
    for key in ("regional", "director", "vp"):
        for r in DATA.get(key) or []:
            if r.get("region") not in all_regions:
                all_regions.append(r.get("region"))
            if r.get("mgr"):
                held[r["region"]] = True

    def claim(key, tier):
        for r in DATA.get(key) or []:
            if r.get("mgr") == person:
                if tier not in seats:
                    seats.append(tier)
                regions.add(r["region"])

    claim("regional", "manager")
    claim("director", "director")
    claim("vp", "vp")

    # a region nobody holds a seat in rolls up to any Director or VP
    if "director" in seats or "vp" in seats:
        for r in all_regions:
            if not held.get(r):
                regions.add(r)
    if "vp" in seats:
        regions.update(all_regions)

    for e in DATA["employees"]:
        if e.get("manager") == person:
            names.add(e["name"])

    every = bool(all_regions) and all(r in regions for r in all_regions)
    return {"all": every, "regions": sorted(regions), "names": sorted(names),
            "role": "manager" if seats else "rep", "seats": seats}


def recruit_lines_for(names):
    """The recruiting bonus is the one number on a rep's statement computed
    from OTHER people's deals. Rather than ship those deals to the browser,
    work it out here and send only the finished lines. Deal values never leave
    the server."""
    want = set(names)
    by_month = {}
    for d in deals_now():
        if d.get("status") != "Won" or not d.get("close") or d["close"] < CUTOFF:
            continue
        by_month.setdefault(d["close"][:7], []).append(d)

    out = []
    sp = DATA.get("mavryckPlan") or {}
    for month, pool in by_month.items():
        selfg, closed, set_ = {}, {}, {}

        def bump(m, k, v):
            if k:
                m[k] = m.get(k, 0.0) + v

        for d in pool:
            base = (d["canvComm"] / RATES["canvasser"] if d.get("canvComm")
                    else d["closerComm"] / RATES["closer"] if d.get("closerComm")
                    else d.get("value") or 0)
            if d.get("selfGen"):
                bump(selfg, d.get("closer"), base)
            else:
                bump(closed, d.get("closer"), base)
                bump(set_, d.get("canvasser"), base)

        for e in DATA["employees"]:
            s = selfg.get(e["name"], 0.0)
            c = closed.get(e["name"], 0.0)
            t = set_.get(e["name"], 0.0)
            if not (s or c or t):
                continue
            a1 = s * RECRUIT["l1"]["self"] + c * RECRUIT["l1"]["closer"] + t * RECRUIT["l1"]["setter"]
            a2 = s * RECRUIT["l2"]["self"] + c * RECRUIT["l2"]["closer"] + t * RECRUIT["l2"]["setter"]
            if e.get("ref1") and a1 and e["ref1"] in want:
                out.append({"month": month, "to": e["ref1"], "from": e["name"], "lvl": "l1", "amt": a1})
            if e.get("ref2") and a2 and e["ref2"] in want:
                out.append({"month": month, "to": e["ref2"], "from": e["name"], "lvl": "l2", "amt": a2})

        if sp.get("owner") in want:
            for n in sp.get("members") or []:
                v = selfg.get(n, 0.0) + closed.get(n, 0.0) + set_.get(n, 0.0)
                if v:
                    out.append({"month": month, "to": sp["owner"], "from": n,
                                "lvl": "special", "amt": v * sp["rate"]})
    return out


def scope_data(person, scope):
    if scope["all"] and scope["role"] != "manager":
        payload = dict(DATA)
        payload["deals"] = deals_now()      # admins and viewers see the live feed too
        payload["session"] = None
        return payload

    regions, names = set(scope["regions"]), set(scope["names"])

    def visible(d):
        if d.get("team") and d["team"] in regions:
            return True
        return d.get("canvasser") in names or d.get("closer") in names

    deals = [d for d in deals_now() if visible(d)]

    keep = set(names)
    for d in deals:
        keep.add(d.get("canvasser"))
        keep.add(d.get("closer"))
    for e in DATA["employees"]:
        if e.get("ref1") in names or e.get("ref2") in names:
            keep.add(e["name"])                       # their recruits
        if e["name"] in names and e.get("manager"):
            keep.add(e["manager"])

    employees = []
    for e in DATA["employees"]:
        if e["name"] not in keep:
            continue
        if e["name"] in names or scope["role"] == "manager":
            employees.append(e)
        else:
            # a rep sees a recruit's name and team, never their pay plan detail
            employees.append({"name": e["name"], "active": e.get("active"),
                              "team": e.get("team"), "start": e.get("start")})

    keys = {f"{d['opp']}|{d['close']}" for d in deals}
    return {
        "rates": DATA.get("rates"), "regional": DATA.get("regional"),
        "director": DATA.get("director"), "vp": DATA.get("vp"),
        "frozenAt": DATA.get("frozenAt"), "mavryckPlan": DATA.get("mavryckPlan"),
        "deals": deals,
        "holds": [h for h in (DATA.get("holds") or [])
                  if f"{h.get('opp')}|{h.get('close')}" in keys],
        "employees": employees,
        "recruiters": DATA.get("recruiters") if scope["role"] == "manager" else [],
        "adjustments": [a for a in (DATA.get("adjustments") or []) if a.get("who") in names],
        "recruitLines": recruit_lines_for(scope["names"]),
    }


def events_for(events, deals, scope):
    """An event is visible to whoever can already see the thing it touches."""
    if scope["all"] and scope["role"] != "manager":
        return events
    keys = {f"{d['opp']}|{d['close']}" for d in deals}
    names = set(scope["names"])
    out = []
    for e in events:
        k = e["kind"]
        if k == "run.freeze":
            out.append(e)                       # everyone sees a frozen run
        elif k == "adjustment":
            if e.get("target") in names:
                out.append(e)
        elif k == "adjustment.remove":
            out.append(e)                       # an id and nothing else
        elif e.get("target") in keys:
            out.append(e)
    return out


def identify(session):
    """Map the signed-in Google account to someone on the roster."""
    email = str((session or {}).get("e") or "").lower().strip()
    if not email:
        return None
    listed = PEOPLE.get(email)
    if listed and listed.get("name"):
        return {"email": email, "name": listed["name"], "role": listed.get("role")}
    if not REPS_ENABLED:
        return None          # locked to the allowlist above
    # not listed: match the local part against the roster, so a new rep works
    # without an edit here. Own statement only.
    guess = email.split("@")[0].replace(".", " ").replace("_", " ").replace("-", " ").lower()
    for e in DATA["employees"]:
        if e["name"].lower() == guess:
            return {"email": email, "name": e["name"], "role": None}
    return None


# ═════════════════════════════════════════════════════════════════
#  Write validation - the browser sends an intent, this decides
#  whether it is a thing that can happen at all
# ═════════════════════════════════════════════════════════════════

def _deal_by_key(key):
    parts = str(key or "").split("|")
    if len(parts) < 2:
        return None
    for d in deals_now():
        if d.get("opp") == parts[0] and d.get("close") == parts[1]:
            return d
    return None


def _validate(kind, target, p):
    p = p or {}
    if kind in ("hold", "release"):
        if not _deal_by_key(target):
            return "That deal is not in the ledger."
        if p.get("leg") not in ("setter", "closer"):
            return "A hold is on the setter or the closer leg."
        if kind == "hold" and p.get("reason") and p["reason"] not in HOLD_REASONS:
            return "Unknown hold reason."
        return None
    if kind in ("changeorder.apply", "changeorder.decline", "changeorder.undo"):
        if not _deal_by_key(target):
            return "That deal is not in the ledger."
        return None
    if kind == "adjustment":
        if not target or not any(e["name"] == target for e in DATA["employees"]):
            return "That person is not on the roster."
        if p.get("type") not in ADJ_KINDS:
            return "Unknown adjustment type."
        try:
            amt = float(p.get("amount"))
        except Exception:
            return "An adjustment needs an amount."
        if not amt:
            return "An adjustment needs an amount."
        if abs(amt) > 100000:
            return "That amount looks wrong. Enter it under $100,000."
        if not _is_date(p.get("run")):
            return "An adjustment needs a pay run."
        return None
    if kind == "adjustment.remove":
        return None if str(target or "").isdigit() else "Which adjustment?"
    if kind == "run.freeze":
        return None if _is_date(target) else "Which run?"
    if kind in ("plan.schedule", "plan.cancel"):
        if not _is_date(target):
            return "A plan change needs the date it starts."
        # a plan may only ever start in the future: deals have already closed
        # under the current one and their rates cannot be moved underneath them
        if target <= _today():
            return "A plan has to start in the future."
        if kind == "plan.cancel":
            return None
        rates = p.get("rates")
        if not isinstance(rates, dict):
            return "A plan change needs its rates."
        for k in ("closer", "canvasser", "hourly"):
            try:
                v = float(rates[k])
            except (KeyError, TypeError, ValueError):
                return f"The {k} rate is missing or not a number."
            if not 0 <= v <= 1:
                return "Rates are a fraction of contract value, between 0 and 1."
        return None
    return "Unknown action."


def _is_date(s):
    s = str(s or "")
    return len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit()


def _sanitise(kind, p):
    p = p or {}
    cut = lambda v, n=200: None if v is None else str(v)[:n]
    if kind == "hold":
        return {"leg": cut(p.get("leg"), 10), "reason": cut(p.get("reason"), 120)}
    if kind == "release":
        return {"leg": cut(p.get("leg"), 10)}
    if kind == "adjustment":
        return {"type": cut(p.get("type"), 40), "amount": float(p.get("amount")),
                "run": cut(p.get("run"), 10), "note": cut(p.get("note"), 300)}
    if kind == "plan.schedule":
        def frac(v):
            try:
                return round(min(max(float(v), 0.0), 1.0), 6)
            except (TypeError, ValueError):
                return 0.0
        r = p.get("rates") or {}
        rc = p.get("recruit") or {}
        su = p.get("summer") or {}
        lvl = lambda d: {k: frac((d or {}).get(k)) for k in ("self", "closer", "setter")}
        return {
            "rates": {k: frac(r.get(k)) for k in ("closer", "canvasser", "hourly", "override")},
            "recruit": {"l1": lvl(rc.get("l1")), "l2": lvl(rc.get("l2"))},
            "summer": {"on": bool(su.get("on")), "start": cut(su.get("start"), 10),
                       "end": cut(su.get("end"), 10), "tier2": frac(su.get("tier2")),
                       "tier3": frac(su.get("tier3"))},
            "note": cut(p.get("note"), 300),
        }
    if kind == "run.freeze":
        # What each deal in the run was paid on, captured at the moment it froze.
        # This is what makes the run a record rather than a live recalculation,
        # so it is kept - bounded, and coerced to numbers.
        b = p.get("basis")
        if not isinstance(b, dict):
            return {}
        out = {}
        for k, v in list(b.items())[:600]:
            try:
                out[str(k)[:200]] = round(float(v), 2)
            except (TypeError, ValueError):
                continue
        return {"basis": out}
    return {}


# ═════════════════════════════════════════════════════════════════
#  HTTP - called from server.py
# ═════════════════════════════════════════════════════════════════

_DENY_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vivid Windows - Commissions</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#F7F9FA;
font:15px/1.6 "Plus Jakarta Sans",-apple-system,Segoe UI,sans-serif;color:#2B363B}}
.b{{max-width:440px;padding:34px;background:#fff;border:1px solid #E4E8EB;border-radius:10px;
text-align:center}} h1{{margin:0 0 10px;font-size:19px;color:#172024}} p{{margin:0;color:#777D80}}
code{{background:#EFF2F4;padding:2px 6px;border-radius:4px;color:#2B363B}}
</style><div class="b"><h1>{title}</h1><p>{body}</p></div>"""


def _html(handler, markup, status=200):
    payload = markup.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _unavailable(handler, why, as_json=False):
    if as_json:
        handler._json({"error": f"The commission ledger is not running: {why}"}, status=503)
    else:
        _html(handler, _DENY_PAGE.format(
            title="The commission ledger is not running",
            body="It could not start, so it has taken itself out of service rather than risk "
                 "showing anyone the wrong pay. Everything else on this site is unaffected."
                 f"<br><br>Send this to Abipsha: <code>{_esc(why)}</code>"), status=503)


def handles(path):
    return path == "/commissions" or path.startswith("/commissions/") or path == "/commissions.html"


def handle_get(handler, path, session):
    """Return True if this module answered the request."""
    if not handles(path):
        return False

    if path == "/commissions.html":
        handler._redirect("/commissions")
        return True

    if _FAILED:
        _unavailable(handler, _FAILED, as_json=path != "/commissions")
        return True

    who = identify(session)
    if not who:
        email = _esc((session or {}).get("e") or "unknown")
        if path == "/commissions":
            _html(handler, _DENY_PAGE.format(
                title="No commission record for this account",
                body=(f"You are signed in as <code>{email}</code>. The commission ledger "
                      "is currently open to a limited group.<br><br>Ask Abipsha or Biss "
                      "if you need access.")
                     if not REPS_ENABLED else
                     (f"You are signed in as <code>{email}</code>, but that account is not "
                      "matched to anyone on the sales roster.<br><br>Ask Abipsha or Biss to add you.")),
                status=403)
        else:
            handler._json({"error": "No commission record for this account."}, status=403)
        return True

    if path == "/commissions":
        if not os.path.exists(PAGE_FILE):
            handler._json({"error": "commissions.html not found"}, status=500)
            return True
        with open(PAGE_FILE, "rb") as f:
            content = f.read()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        handler.wfile.write(content)
        return True

    if path == "/commissions/data":
        scope = build_scope(who["name"], who["role"])
        payload = scope_data(who["name"], scope)
        payload["session"] = {
            "name": who["name"], "email": who["email"],
            "role": who["role"] or scope["role"],
            "all": bool(scope["all"]) or who["role"] in ("admin", "viewer"),
            "regions": scope["regions"],
            "canEdit": who["role"] == "admin",
        }
        try:
            payload["events"] = events_for(all_events(), payload["deals"], scope)
            if _events_stale:
                payload["eventsStale"] = True
            if _deals_stale:
                payload["dealsStale"] = True
        except Exception as e:
            handler._json({"error": "Odoo is not responding right now, so the ledger history "
                                    f"could not be read. Nothing is lost - try again in a minute. ({e})"},
                          status=503)
            return True
        handler._json(payload)
        return True

    if path == "/commissions/events/pending":
        if who["role"] != "admin":
            handler._json({"error": "Admins only."}, status=403)
        else:
            handler._json({"events": []})   # Odoo is the store; there is no outbox
        return True

    handler._json({"error": "Not found"}, status=404)
    return True


def handle_post(handler, path, body, session):
    """Return True if this module answered the request.

    The page hides edit controls from anyone who is not an admin, but that is
    cosmetic - anyone can POST directly. Every check that matters is here.
    """
    if path.rstrip("/") != "/commissions/events":
        return False

    if _FAILED:
        _unavailable(handler, _FAILED, as_json=True)
        return True

    who = identify(session)
    if not who:
        handler._json({"error": "No commission record for this account."}, status=403)
        return True
    if who["role"] != "admin":
        handler._json({"error": "Only a full admin can change what is paid."}, status=403)
        return True

    body = body or {}
    kind = str(body.get("kind") or "")
    if kind not in KINDS:
        handler._json({"error": f"Unknown action: {kind}"}, status=400)
        return True

    target = body.get("target")
    target = None if target is None else str(target)[:200]
    bad = _validate(kind, target, body.get("payload"))
    if bad:
        handler._json({"error": bad}, status=400)
        return True

    try:
        row = append_event(kind, target, _sanitise(kind, body.get("payload")),
                           who["name"], who["email"])
        handler._json({"event": row}, status=201)
    except Exception as e:
        traceback.print_exc()
        handler._json({"error": "Odoo is not responding, so that was not saved. Nothing was "
                                f"changed - try again in a minute. ({e})"}, status=503)
    return True
