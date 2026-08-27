"""
Build commissions.html from the two source files in this folder.

    python build.py           ->  ../commissions.html      (what the server serves)
    python build.py --hook    ->  commissions-test.html    (same page + a test hook)

part1.html  the shell: <head>, the stylesheet, and the empty layout
part2.js    the engine: every calculation and every view

The built page carries NO data. It fetches /commissions/data on load, so what
reaches the browser is only what the signed-in person is allowed to see. That
is deliberate and api/commissions.py enforces it: it refuses to serve a page
with the dataset compiled in, so a stray standalone build cannot leak the whole
company's pay by being copied into place.

Everything is forced to ASCII: non-ASCII characters become HTML entities, so the
file survives any encoding on the way to Render.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def asciify(t):
    return ''.join(c if ord(c) < 128 else '&#%d;' % ord(c) for c in t)


def read(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return asciify(f.read())


html = read('part1.html')
js = read('part2.js')
head, body = html.split('</style>', 1)

BOOT = """
<script>
(function () {
  const topnav = document.getElementById('topnav'), context = document.getElementById('context'),
    view = document.getElementById('view'), tip = document.getElementById('tip');

  const fail = (title, detail) => {
    view.innerHTML = '<div class="card pad" style="max-width:520px;margin:60px auto;text-align:center">'
      + '<h2 style="margin:0 0 10px">' + title + '</h2>'
      + '<p class="hint" style="margin:0">' + detail + '</p></div>';
  };

  view.innerHTML = '<p class="muted" style="text-align:center;margin:80px 0">Loading your commissions...</p>';

  fetch('/commissions/data', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    .then(r => {
      if (r.status === 401 || r.status === 403) { throw new Error('forbidden'); }
      if (!r.ok) { throw new Error('http ' + r.status); }
      return r.json();
    })
    .then(D => { boot(D, topnav, context, view, tip); })
    .catch(e => {
      if (String(e.message) === 'forbidden') {
        fail('No commission record for this account',
          'You are signed in, but this Google account is not matched to anyone on the sales roster. '
          + 'Ask Abipsha or Biss to add you.');
      } else {
        fail('Could not load your commissions',
          'The server did not return the data. Try reloading; if it keeps happening, send this to Abipsha: '
          + String(e.message));
      }
    });

  function boot(D, topnav, context, view, tip) {
__JS__
__HOOK__
  }
})();
</script>
"""

hook = ("\n    window.__t = { get S(){return S}, sheet, SESSION, render, RUNDATES2, DEALS, EMP };\n"
        if '--hook' in sys.argv else '')

out = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
       + head + '</style>\n</head>\n<body>\n' + body
       + BOOT.replace('__JS__', js).replace('__HOOK__', hook)
       + "\n</body>\n</html>\n")

assert all(ord(c) < 128 for c in out), 'non-ASCII survived asciify'
assert '/commissions/data' in out, 'the built page must fetch its data'
assert '"canvComm":' not in out and 'const D = {"' not in out, 'dataset must not be baked in'

dest = (os.path.join(HERE, 'commissions-test.html') if '--hook' in sys.argv
        else os.path.join(ROOT, 'commissions.html'))
with open(dest, 'w', encoding='utf-8') as f:
    f.write(out)
print('built %s  (%d bytes)' % (dest, len(out)))
