"""Quick sanity checks."""
import sys
from app import app
with app.test_client() as c:
    # Main pages
    for route in ['/', '/faster', '/iptv', '/admin']:
        r = c.get(route)
        html = r.data.decode('utf-8', errors='replace')
        brand = 'OK' if 'MADE BY RAFIUL' in html else 'MISS!'
        jinja_broken = '{{' in html
        j = 'JINJA!' if jinja_broken else 'ok'
        sys.stdout.write(f'{route}: {r.status_code} ({len(r.data)}b) brand={brand} j={j}\n')
    # check version in page
    r = c.get('/')
    vcount = r.data.decode().count('75a0e0d')
    sys.stdout.write(f'Version shown in index: {vcount}x\n')
    # Default theme
    dt = c.get('/api/default-theme')
    sys.stdout.write(f'Default theme: {dt.json.get("theme")}\n')
    # Version
    v = c.get('/api/version')
    sys.stdout.write(f'Version: {v.json.get("version")[:12]}\n')
    # Check watch renders
    r = c.get('/api/upstream/events')
    events = r.json
    if events.get('ok') and events.get('events'):
        for ev in events['events'][:1]:
            slug = ev.get('enc_parent') or ev.get('parent')
            if slug:
                w = c.get(f'/watch/{slug}')
                hw = w.data.decode('utf-8', errors='replace')
                sys.stdout.write(f'/watch/{slug}: {w.status_code} brand={"OK" if "MADE BY RAFIUL" in hw else "MISS!"}\n')
                l = c.get(f'/lite/{slug}')
                hl = l.data.decode('utf-8', errors='replace')
                sys.stdout.write(f'/lite/{slug}: {l.status_code} brand={"OK" if "MADE BY RAFIUL" in hl else "MISS!"}\n')
                break
sys.stdout.write('ALL CHECKS DONE\n')


