# Commission ledger - page source

`../commissions.html` is **built**, not edited. Change it here and rebuild, or
your change is gone the next time anyone builds.

```
python build.py
```

That writes `../commissions.html`. Commit both the source you changed and the
rebuilt page.

## The two files

| File | What it is |
|---|---|
| `part1.html` | the shell - `<head>`, the whole stylesheet, the empty layout |
| `part2.js` | the engine - every calculation, every view, every control |

`build.py` glues them together and wraps the engine in a loader that fetches
the data at run time.

## Why the page carries no data

The built page has no dataset in it. On load it calls `/commissions/data`, and
the server sends back **only what the signed-in person is allowed to see** - a
rep gets their own deals and nothing else, so there is nothing in the browser
to go looking for.

`api/commissions.py` enforces this at boot: it refuses to serve a page with the
dataset compiled in, and says so in the log. That check exists because a
standalone build - one with everything baked in, handy for testing - would
otherwise hand every rep the whole company's pay just by being copied into
place. If you ever see the service refuse to start with a message about the
dataset being compiled in, that is this check doing its job. Rebuild with
`python build.py` and it will clear.

`build.py` asserts the same three things before it writes, so a bad build fails
here rather than in production.

## Testing

```
python build.py --hook
```

Writes `commissions-test.html` in this folder with `window.__t` exposed, so a
browser test can reach `S`, `sheet()`, `DEALS` and friends directly instead of
clicking through the UI. Never deploy that file - it is gitignored.

## Who can see it

Access is data, not code, and lives in `api/commissions_people.json`:

- `role: admin` - sees everything, can hold, adjust and freeze runs
- `role: viewer` - sees everything, changes nothing
- absent - denied

Reps are locked out by default. Set `COMMISSIONS_REPS=1` in the environment to
let each rep open their own statement (their own deals and their own pay only).
`part2.js` carries `ADMINS` / `VIEWERS` too, but only for how the page labels
itself - the server is what actually decides, and it never sends a person data
they are not entitled to.

## A note on where the money comes from

Nothing on this page is typed in twice. Commission, bonuses, recruiting,
overrides and Vivid Adder are all derived from the deals. Holds, adjustments,
change orders and run freezes are stored as append-only events and replayed on
load, so a frozen run stays exactly as it was paid and a late correction rolls
forward into the next open run instead of quietly rewriting history.
