"""
Probe Odoo employee data for onboarding tracker development.
Run: python probe_employee.py
"""
import os, json, xmlrpc.client

# Load from environment or hardcode for one-off probe
ODOO_URL  = os.environ.get("ODOO_URL",      "https://myvivid.odoo.com")
ODOO_DB   = os.environ.get("ODOO_DB",       "myvivid")
ODOO_USER = os.environ.get("ODOO_USER",     "abipsha.joshi@vividwindows.com")
ODOO_PASS = os.environ.get("ODOO_PASSWORD", "")

EMPLOYEE_ID = 201  # Alex Aussem

def common():  return xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
def models():  return xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")

uid = common().authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
print(f"Authenticated as uid={uid}\n")

def kw(model, method, args, kwargs={}):
    return models().execute_kw(ODOO_DB, uid, ODOO_PASS, model, method, args, kwargs)

# ── 1. All fields on the employee record ─────────────────────────────────────
print("=" * 60)
print("1. EMPLOYEE RECORD — all fields")
print("=" * 60)
rec = kw("hr.employee", "read", [[EMPLOYEE_ID]], {"context": {"active_test": False}})
if rec:
    emp = rec[0]
    for k, v in sorted(emp.items()):
        if v not in (False, None, [], ""):
            print(f"  {k}: {v}")
else:
    print("  Employee not found!")

# ── 2. Open activities on this employee ──────────────────────────────────────
print("\n" + "=" * 60)
print("2. OPEN ACTIVITIES (mail.activity)")
print("=" * 60)
acts = kw("mail.activity", "search_read",
    [[["res_model", "=", "hr.employee"], ["res_id", "=", EMPLOYEE_ID]]],
    {"fields": ["id", "activity_type_id", "summary", "note", "date_deadline", "state", "user_id"]}
)
if acts:
    for a in acts:
        print(f"  [{a['state']}] {a['activity_type_id'][1] if a['activity_type_id'] else '?'}"
              f" | {a['summary']} | due {a['date_deadline']} | assigned {a['user_id'][1] if a['user_id'] else '?'}")
else:
    print("  No open activities.")

# ── 3. Signature requests linked to this employee ────────────────────────────
print("\n" + "=" * 60)
print("3. SIGN REQUESTS (sign.request.item)")
print("=" * 60)
try:
    # Try partner_id-based lookup
    partner_id = emp.get("address_home_id") or emp.get("user_id")
    if isinstance(partner_id, list): partner_id = partner_id[0]

    items = kw("sign.request.item", "search_read",
        [[["partner_id.employee_ids", "in", [EMPLOYEE_ID]]]],
        {"fields": ["id", "sign_request_id", "state", "partner_id", "signing_date"]}
    )
    if items:
        for i in items:
            req_name = i.get("sign_request_id", [None, "?"])
            print(f"  [{i['state']}] {req_name[1] if isinstance(req_name, list) else req_name}"
                  f" | signed: {i.get('signing_date','–')}")
    else:
        print("  No sign request items found via partner lookup.")
except Exception as e:
    print(f"  sign.request.item error: {e}")

# ── 4. Sign requests directly on employee model ───────────────────────────────
print("\n" + "=" * 60)
print("4. SIGN REQUESTS (sign.request — direct)")
print("=" * 60)
try:
    reqs = kw("sign.request", "search_read",
        [[["state", "!=", "signed"]]],
        {"fields": ["id", "reference", "state", "request_item_ids"], "limit": 5}
    )
    print(f"  Sample open sign requests (first 5): {json.dumps(reqs, indent=2)}")
except Exception as e:
    print(f"  sign.request error: {e}")

# ── 5. List all x_studio fields on hr.employee ───────────────────────────────
print("\n" + "=" * 60)
print("5. CUSTOM FIELDS (x_studio_*) on hr.employee")
print("=" * 60)
fields_meta = kw("hr.employee", "fields_get", [], {"attributes": ["string", "type"]})
for fname, fdata in sorted(fields_meta.items()):
    if fname.startswith("x_studio"):
        print(f"  {fname} ({fdata['type']}): {fdata['string']}")
