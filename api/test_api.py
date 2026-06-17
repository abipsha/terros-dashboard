"""
Quick sanity-check script — run before starting the full server.
Tests each Terros endpoint and prints what comes back.

Usage:
  python test_api.py
  python test_api.py 2026-06-01 2026-06-07
"""
import sys
import json
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
import terros

def pp(label, data):
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print('─'*60)
    print(json.dumps(data, indent=2, default=str)[:3000])

def main():
    start_s = sys.argv[1] if len(sys.argv) > 1 else "2026-06-09"
    end_s   = sys.argv[2] if len(sys.argv) > 2 else "2026-06-15"

    from datetime import datetime, timezone, timedelta
    def to_ms(s, eod=False):
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if eod:
            dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
        return int(dt.timestamp() * 1000)

    start_ms = to_ms(start_s)
    end_ms   = to_ms(end_s, eod=True)

    print(f"\n  Testing Terros API for {start_s} → {end_s}")

    # 1. Workflow
    try:
        wf = terros.get_workflow()
        pp("WORKFLOW", {
            "workflowId": wf.get("workflowId"),
            "name":       wf.get("name"),
            "actions":    wf.get("actions", []),
        })
    except Exception as e:
        print(f"  WORKFLOW ERROR: {e}")
        return

    # 2. Action map
    am = terros.get_action_map()
    pp("ACTION MAP", am)

    # 3. Users (first 5)
    users = terros.get_users()
    pp("USERS (first 5)", {"total": len(users), "sample": users[:5]})

    # 4. Activities (first page only for the test)
    print(f"\n{'─'*60}")
    print("  ACTIVITIES (fetching first page…)")
    print('─'*60)
    acts = terros.get_activities(start_ms, end_ms)
    print(f"  Total records fetched: {len(acts)}")
    if acts:
        print(f"  Sample record:")
        print(json.dumps(acts[0], indent=4, default=str))

    # 5. Weekly report
    if acts:
        print(f"\n{'─'*60}")
        print("  WEEKLY REPORT")
        print('─'*60)
        report = terros.build_weekly_report(start_ms, end_ms)
        print(f"  Reps active: {report['activeCount']} / {report['rosterCount']}")
        print(f"  Totals: {json.dumps(report['totals'])}")
        print(f"\n  Top 5 reps by pitches:")
        for r in report["reps"][:5]:
            print(f"    {r['name']:<25}  knocks={r['knocks']:>4}  pitches={r['pitches']:>4}  "
                  f"acs={r['acs']:>3}  sras={r['sras']:>3}")

    print(f"\n{'─'*60}")
    print("  Done.")
    print('─'*60)

if __name__ == "__main__":
    main()
