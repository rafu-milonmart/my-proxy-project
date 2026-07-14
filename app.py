import os, sys, re, base64, hashlib, json, logging, time, threading, subprocess, asyncio, shutil, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
from pathlib import Path
from flask import Flask, redirect, send_from_directory, Response, request, jsonify, render_template, url_for
from curl_cffi import requests as curl_requests
from asgiref.wsgi import WsgiToAsgi

os.environ['PYTHONUNBUFFERED'] = '1'
sys.stdout.reconfigure(encoding='utf-8', line_buffering=False)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=False)
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
UPSTREAM = "https://live.sportzfy.life"
DECRYPT_KEY = "ZESBtSlRTuF4Ac4k757OuasOWOA0W8LcqRn3SFgdInDoMyS8"
STATIC_DIR = Path(__file__).parent
VERSION_FILE = STATIC_DIR / 'version.txt'
DEFAULT_THEME_FILE = STATIC_DIR / 'default_theme.txt'
GITHUB_API = 'https://api.github.com/repos/rafu-milonmart/my-proxy-project'
CUSTOM_M3U_FILE = STATIC_DIR / 'custom_m3u.json'
DEBUG = os.environ.get('ZL_DEBUG', '0') == '1'
_LAUNCH_ARGS = None  # saved by __main__ for restart

def _get_current_version():
    return VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else 'unknown'

def _get_current_version_short():
    v = _get_current_version()
    return v[:12] if v != 'unknown' else v

def _get_default_theme():
    return DEFAULT_THEME_FILE.read_text().strip() if DEFAULT_THEME_FILE.exists() else 'dark'

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = DEBUG
asgi_app = WsgiToAsgi(app)

@app.context_processor
def _inject_globals():
    return {
        'default_theme': _get_default_theme(),
        'current_version': _get_current_version_short(),
    }

LOG_DIR = STATIC_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)
_log = logging.getLogger('zl')
_log.setLevel(logging.DEBUG if DEBUG else logging.INFO)
_fh = logging.FileHandler(LOG_DIR / 'app.log', encoding='utf-8')
_fh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(message)s', datefmt='%H:%M:%S'))
_log.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(message)s', datefmt='%H:%M:%S'))
_log.addHandler(_sh)

_POOL = ThreadPoolExecutor(max_workers=64)
_tlocal = threading.local()

_http_headers = {
    "Accept": "application/json",
    "X-Requested-With": "lsp",
    "X-LSP-Enc": "1",
}
_media_headers = {
    "Accept": "*/*",
    "Origin": UPSTREAM,
    "X-Requested-With": "lsp",
    "X-LSP-Enc": "1",
}

# ---------------------------------------------------------------------------
# Lock-free caches: dict with (timestamp, value) tuples, no locks
# ---------------------------------------------------------------------------
_ev_cache = {}       # key -> (ts, data)
_pb_cache = {}       # slug -> (ts, streams_list)
_manifest_cache = {} # (slug, idx) -> (ts, body)
_m3u_cache = {}
_DECRYPT_KEY_CACHE = {}

def _clean_url(url):
    if '|' in url:
        return url[:url.index('|')]
    return url

def _fetch_fancode():
    """Fetch FanCode events and normalize into upstream event format."""
    try:
        raw = urllib.request.urlopen(FC_SOURCE, timeout=10).read().decode('utf-8')
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
    except Exception as e:
        _log.debug('FanCode fetch failed: %s', e)
        return []
    events = []
    for item in items:
        mid = item.get('match_id', '')
        if not mid:
            continue
        slug = f'fc_{mid}'
        t1 = item.get('team_1', 'TBD')
        t2 = item.get('team_2', 'TBD')
        status = (item.get('status') or '').upper()
        hls_bd = item.get('fancode_bd', '')
        hls_in = item.get('fancode_in', '')
        events.append({
            'id': slug,
            'enc_parent': slug,
            'parent': slug,
            'team_a_name': t1,
            'team_b_name': t2,
            'team_a_logo': item.get('team_1_logo', ''),
            'team_b_logo': item.get('team_2_logo', ''),
            'sport': item.get('event_category', 'Sports'),
            'league': 'FanCode',
            'title': item.get('event_name', f'{t1} vs {t2}'),
            'status': status,
            'is_fancode': True,
            'fancode_bd': hls_bd,
            'fancode_in': hls_in,
        })
    return events

_EV_TTL = 15
_PB_TTL = 30
_M3U_TTL = 30
_FC_TTL = 60

FC_SOURCE = 'https://raw.githubusercontent.com/sm-monirulislam/FanCode-Auto-Update-Playlist/refs/heads/main/FanCode_data.json'
_fc_cache = {}  # key -> (ts, data)

def _cached(key, ttl, fetcher, store=None):
    """Atomic cache get-or-set. No locks — cheap dict read wins."""
    now = time.time()
    if store is None:
        store = _ev_cache
    hit = store.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fetcher()
        store[key] = (time.time(), val)
        return val
    except Exception as e:
        _log.debug('Cache fetch failed for %s: %s', key, e)
        return hit[1] if hit else []

def _pb_cached(slug):
    now = time.time()
    hit = _pb_cache.get(slug)
    if hit and now - hit[0] < _PB_TTL:
        return hit[1]
    # FanCode events: synthetic stream from direct HLS URL
    if slug.startswith('fc_'):
        ev = None
        for e in _get_events():
            if (e.get('enc_parent') or e.get('parent') or e.get('id')) == slug:
                ev = e
                break
        if ev and ev.get('is_fancode'):
            streams = []
            bd = ev.get('fancode_bd', '')
            ind = ev.get('fancode_in', '')
            if bd:
                streams.append({'stream_url': bd, 'stream_type': 'hls', 'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'referer': ''})
            if ind:
                streams.append({'stream_url': ind, 'stream_type': 'hls', 'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'referer': ''})
            if streams:
                _pb_cache[slug] = (time.time(), streams)
                return streams
        return []
    try:
        servers = _fetch_playback(slug)
        streams = []
        if servers:
            r = _decrypt(servers[0]['enc'], str(servers[0]['bucket']))
            if r and 'streams' in r:
                streams = r['streams']
        _pb_cache[slug] = (time.time(), streams)
        return streams
    except Exception:
        return hit[1] if hit else []

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _sess():
    if not hasattr(_tlocal, "session"):
        _tlocal.session = curl_requests.Session(headers=_http_headers, impersonate="chrome124", timeout=15)
    return _tlocal.session

def _media_sess():
    if not hasattr(_tlocal, "media_session"):
        _tlocal.media_session = curl_requests.Session(headers=_media_headers, impersonate="chrome124", timeout=30)
    return _tlocal.media_session

def _http_get(url, raw=False):
    delays = [0, 1, 3]
    for i, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            r = _sess().get(url)
            if r.status_code == 200:
                return (r.content, r.text, r.headers.get('content-type', '')) if raw else (r.status_code, r.text)
        except Exception:
            pass
    return (b'', '', '') if raw else (0, '')

# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------
def _fetch_playback(slug):
    _, body = _http_get(f"{UPSTREAM}/api/upstream/playback/{slug}")
    if not body:
        return []
    try:
        d = json.loads(body)
        if isinstance(d, dict) and 'enc' in d:
            return [d]
        return d if isinstance(d, list) else []
    except Exception:
        return []

def _decrypt_key(bucket):
    k = _DECRYPT_KEY_CACHE.get(bucket)
    if not k:
        k = hashlib.sha256(f"{DECRYPT_KEY}|lsp-v1|{bucket}".encode()).digest()
        _DECRYPT_KEY_CACHE[bucket] = k
    return k

def _decrypt(enc_b64, bucket):
    if not HAS_CRYPTO:
        return None
    try:
        buf = base64.b64decode(enc_b64)
        iv, ct, tag = buf[:12], buf[12:-16], buf[-16:]
        pt = AESGCM(_decrypt_key(bucket)).decrypt(iv, ct + tag, None)
        return json.loads(pt.decode())
    except Exception:
        return None

def _resolve_one(slug):
    # FanCode: direct HLS URL, no decryption needed
    if slug.startswith('fc_'):
        for e in _get_events():
            if (e.get('enc_parent') or e.get('parent') or e.get('id')) == slug:
                return e.get('fancode_bd') or e.get('fancode_in') or None
        return None
    streams = _pb_cached(slug)
    for s in streams:
        u = _clean_url(s.get('stream_url', ''))
        if '.m3u8' in u:
            return u
    return _clean_url(streams[0]['stream_url']) if streams else None

def _resolve_many(slugs):
    if not slugs:
        return {}
    fut = {_POOL.submit(_resolve_one, s): s for s in slugs}
    out = {}
    try:
        for f in as_completed(fut, timeout=60):
            try:
                out[fut[f]] = f.result()
            except Exception:
                out[fut[f]] = None
    except TimeoutError:
        for f, s in fut.items():
            if s not in out:
                out[s] = None
    return out

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.after_request
def add_cors(resp):
    if request.path.startswith(('/stream/', '/proxy/', '/api/', '/playlist.')):
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Headers'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return resp

@app.route('/')
def index():
    return render_template('index.html')

# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------
def _fetch_events():
    return json.loads((_http_get(f"{UPSTREAM}/api/upstream/events")[1] or '{}')).get('events') or []

def _get_events():
    upstream = _cached('events', _EV_TTL, _fetch_events, _ev_cache)
    fc = _cached('fancode', _FC_TTL, _fetch_fancode, _fc_cache)
    return (upstream or []) + (fc or [])

@app.route('/watch/<slug>')
def watch(slug):
    ev_fut = _POOL.submit(_get_events)
    streams = _pb_cached(slug)
    events = ev_fut.result()
    event = None
    for e in events:
        if e.get('enc_parent') == slug or e.get('parent') == slug or e.get('id') == slug:
            event = e
            break
    sport = (event or {}).get('sport', '')
    return render_template('watch.html', slug=slug, event=event, streams=streams, sport=sport)

@app.route('/stream/<slug>/<int:idx>')
def stream_json(slug, idx):
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    s = streams[idx]
    return jsonify({
        "url": _clean_url(s['stream_url']),
        "type": s.get('stream_type', ''),
        "drm": {"key": s['drm_key'], "kid": s['drm_kid']} if s.get('drm_key') else None,
    })

@app.route('/playlist.m3u')
def playlist_m3u():
    def _build():
        events = _get_events()
        slugs, ev_map = [], {}
        for ev in events:
            s = ev.get('enc_parent') or ev.get('parent') or ev.get('id')
            if s:
                slugs.append(s)
                ev_map[s] = ev
        urls = _resolve_many(slugs)
        lines = ["#EXTM3U"]
        for slug, url in urls.items():
            if not url:
                continue
            ev = ev_map[slug]
            t = f"{ev.get('team_a_name', '?')} vs {ev.get('team_b_name', '?')}"
            g = ev.get('sport', 'Sports')
            l = ev.get('league', '')
            if l:
                g += ' - ' + l
            lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="{t}" group-title="{g}",{t}')
            lines.append(url)
        return '\n'.join(lines)
    return Response(_cached('m3u', _M3U_TTL, _build, _m3u_cache), mimetype='audio/x-mpegurl')

def _find_event(slug):
    """Find event by slug (enc_parent / parent / id)."""
    for e in _get_events():
        if (e.get('enc_parent') or e.get('parent') or e.get('id')) == slug:
            return e
    return None

def _build_playlist_for_event(e, seen_slugs=None):
    """Build M3U lines for a single event."""
    if seen_slugs is None:
        seen_slugs = set()
    slug = e.get('enc_parent') or e.get('parent') or e.get('id')
    if not slug or slug in seen_slugs:
        return []
    seen_slugs.add(slug)
    lines = []
    streams = _pb_cached(slug)
    t = f"{e.get('team_a_name', '?')} vs {e.get('team_b_name', '?')}"
    for i, s in enumerate(streams):
        u = _clean_url(s.get('stream_url', ''))
        if u:
            lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="{t} LIVE {i+1}",{t} LIVE {i+1}')
            lines.append(u)
    return lines

@app.route('/playlist/<slug>.m3u')
def event_playlist_m3u(slug):
    e = _find_event(slug)
    lines = ["#EXTM3U"]
    if e:
        lines.extend(_build_playlist_for_event(e))
    else:
        # fallback: treat slug as raw slug even without event match
        streams = _pb_cached(slug)
        for i, s in enumerate(streams):
            u = s.get('stream_url', '')
            if u:
                lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="LIVE {i+1}",LIVE {i+1}')
                lines.append(u)
    return Response('\n'.join(lines), mimetype='audio/x-mpegurl')

@app.route('/<name>.m3u')
def custom_playlist_m3u(name):
    """Friendly .m3u — check custom name first, then fuzzy match."""
    name_key = name.strip().lower().replace(' ', '-')
    try:
        data = json.loads(CUSTOM_M3U_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if name_key in data:
        # exact custom name match -> redirect to playlist by slug
        slug = data[name_key].get('slug', '')
        if slug:
            return redirect(url_for('event_playlist_m3u', slug=slug))

    events = _get_events()
    name_lower = name.lower().replace('-', ' ').replace('_', ' ')

    # fuzzy match
    seen = set()
    matches = []
    for e in events:
        slug = e.get('enc_parent') or e.get('parent') or e.get('id')
        if not slug or slug in seen:
            continue
        s_slug = str(slug).lower()
        ta = (e.get('team_a_name') or '').lower()
        tb = (e.get('team_b_name') or '').lower()
        title = (e.get('title') or '').lower()
        sport = (e.get('sport') or '').lower()
        league = (e.get('league') or '').lower()
        if (name_lower == s_slug or s_slug.startswith(name_lower) or
            name_lower in ta or name_lower in tb or name_lower in title or
            name_lower in sport or name_lower in league):
            seen.add(slug)
            matches.append(e)
    if not matches:
        return jsonify({"error": "No events found", "query": name}), 404
    lines = ["#EXTM3U"]
    seen_slugs = set()
    for e in matches:
        lines.extend(_build_playlist_for_event(e, seen_slugs))
    return Response('\n'.join(lines), mimetype='audio/x-mpegurl')

@app.route('/api/custom-m3u', methods=['GET', 'POST', 'DELETE'])
def api_custom_m3u():
    if request.method == 'GET':
        try:
            data = json.loads(CUSTOM_M3U_FILE.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        names = {n: e['slug'] for n, e in data.items()}
        return jsonify({'ok': True, 'names': names})
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip().lower().replace(' ', '-')
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'}), 400
    try:
        data = json.loads(CUSTOM_M3U_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if request.method == 'POST':
        slug = body.get('slug', '')
        data[name] = {'slug': slug}
        CUSTOM_M3U_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return jsonify({'ok': True})
    elif request.method == 'DELETE':
        data.pop(name, None)
        CUSTOM_M3U_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Method not allowed'}), 405

@app.route('/api/events')
def api_events():
    """Return all events (upstream + FanCode) merged."""
    return jsonify({'ok': True, 'events': _get_events()})

@app.route('/api/<path:subpath>')
def proxy_api(subpath):
    if '..' in subpath or '~' in subpath:
        return jsonify({"error": "Forbidden"}), 403
    url = f"{UPSTREAM}/api/{subpath}"
    qs = request.query_string
    if qs:
        url += '?' + (qs.decode() if isinstance(qs, bytes) else qs)
    st, body = _http_get(url)
    return Response(body, status=st or 502, content_type='application/json')

def _hls_rewrite(body, base_dir):
    lines = body.split('\n')
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            out.append(line)
        elif s[0] == '#':
            out.append(re.sub(r'''URI=["']([^"']*)["']|URI=(\S+)''', lambda m: 'URI="' + (urljoin(base_dir, m.group(1) or m.group(2))) + '"', line))
        else:
            out.append(urljoin(base_dir, s))
    return '\n'.join(out)

def _proxy_fetch(url, ua, ref='', timeout=15):
    hdrs = {'User-Agent': ua, 'Accept': '*/*', 'Origin': UPSTREAM}
    if ref: hdrs['Referer'] = ref
    for _ in range(3):
        try:
            r = _media_sess().get(url, headers=hdrs, timeout=timeout)
            if r.status_code == 200:
                return r.status_code, r.content, r.headers.get('content-type', '')
        except Exception:
            pass
        time.sleep(1)
    return 0, b'', ''

@app.route('/proxy/hls/<slug>/<int:idx>/')
def proxy_hls(slug, idx):
    target = request.args.get('url', '')
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    s = streams[idx]
    if not target:
        target = _clean_url(s['stream_url'])
    else:
        target = _clean_url(target)
    p = urlparse(target)
    if not p.netloc:
        target = urljoin(_clean_url(s['stream_url'][:s['stream_url'].rfind('/') + 1]), target)
    ref = s.get('referer', '')
    ua = s.get('user_agent', '') or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    code, body, ct = _proxy_fetch(target, ua, ref, 15)
    if code == 200:
        text = body.decode('utf-8', errors='replace')
        if '.m3u8' in target or text.startswith('#EXT'):
            return Response(_hls_rewrite(text, target[:target.rfind('/') + 1]), content_type='application/vnd.apple.mpegurl')
        return Response(body, content_type=ct or 'application/octet-stream')
    return jsonify({"error": "Upstream failed"}), 502

@app.route('/proxy/dashseg/<slug>/<int:idx>/<path:seg_path>')
def proxy_dash_seg(slug, idx, seg_path):
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    s = streams[idx]
    base_url = _clean_url(s['stream_url'])
    base_dir = base_url[:base_url.rfind('/') + 1]
    target = urljoin(base_dir, seg_path)
    qs = request.query_string
    if qs:
        target += '?' + (qs.decode() if isinstance(qs, bytes) else qs)
    ua = s.get('user_agent', '') or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    code, body, ct = _proxy_fetch(target, ua, '', 10)
    if code == 200:
        return Response(body, content_type=ct or 'application/octet-stream')
    # fallback: redirect browser directly to CDN
    return redirect(target)

@app.route('/proxy/manifest/<slug>/<int:idx>/')
@app.route('/proxy/manifest/<slug>/<int:idx>')
def proxy_manifest(slug, idx):
    key = (slug, idx)
    now = time.time()
    hit = _manifest_cache.get(key)
    if hit and now - hit[0] < 30:
        return Response(hit[1], content_type='application/dash+xml')
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    s = streams[idx]
    raw_url, drm_kid, drm_key = s['stream_url'], s.get('drm_kid', ''), s.get('drm_key', '')
    url = _clean_url(raw_url)
    ua = s.get('user_agent', '') or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    for attempt in range(3):
        try:
            r = _media_sess().get(url, headers={'User-Agent': ua, 'Accept': '*/*', 'Origin': UPSTREAM}, timeout=10)
            if r.status_code == 200:
                body = r.text
            else:
                raise Exception(f'HTTP {r.status_code}')
            if '.mpd' in url:
                body = re.sub(r'<ContentProtection schemeIdUri="urn:uuid:[^"]*"[^>]*/>', '', body)
                body = re.sub(r'<ContentProtection schemeIdUri="urn:uuid:[^"]*"[^>]*>.*?</ContentProtection>', '', body, flags=re.DOTALL)
                if drm_kid and drm_key:
                    body = body.replace(
                        '</AdaptationSet>',
                        '<ContentProtection schemeIdUri="urn:uuid:e2719d58-a985-b3c9-781a-b030af78d12e" value="ClearKey"/>\n</AdaptationSet>',
                        0)
            _manifest_cache[key] = (time.time(), body)
            return Response(body, content_type='application/dash+xml')
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1)
            _pb_cache.pop(slug, None)
            streams = _pb_cached(slug)
            if streams and idx < len(streams):
                raw_url = streams[idx]['stream_url']
                url = _clean_url(raw_url)
    return jsonify({"error": "Manifest failed"}), 502

@app.route('/proxy/manifest/<slug>/<int:idx>/<path:seg_path>')
def proxy_manifest_seg(slug, idx, seg_path):
    """Serve DASH segments requested relative to manifest URL."""
    from flask import redirect as _rd
    return _rd(f'/proxy/dashseg/{slug}/{idx}/{seg_path}{"?%s" % request.query_string.decode() if request.query_string else ""}')

# ---------------------------------------------------------------------------
# Version / Update
# ---------------------------------------------------------------------------
@app.route('/api/default-theme')
def api_default_theme():
    return jsonify({'ok': True, 'theme': _get_default_theme()})

@app.route('/api/version')
def api_version():
    return jsonify({'ok': True, 'version': _get_current_version()})

@app.route('/api/update/check')
def update_check():
    try:
        cv = _get_current_version()
        r = urllib.request.Request(f'{GITHUB_API}/commits/master', headers={'User-Agent': 'ZeroLive'})
        with urllib.request.urlopen(r, timeout=10) as f:
            latest = json.loads(f.read())['sha']
        return jsonify({'ok': True, 'current': cv[:12], 'latest': latest[:12], 'has_updates': latest != cv})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/update/apply')
def update_apply():
    try:
        import urllib.request, zipfile, io, tempfile
        base = Path(__file__).parent
        STAGING = base / '.update_staging'
        # Download to staging
        r = urllib.request.Request(f'{GITHUB_API}/zipball/master', headers={'User-Agent': 'ZeroLive'})
        with urllib.request.urlopen(r, timeout=60) as f:
            z = zipfile.ZipFile(io.BytesIO(f.read()))
            staging = Path(tempfile.mkdtemp())
            z.extractall(staging)
            src = next(p for p in staging.iterdir() if p.is_dir())
            # Save staging path — restart will apply
            STAGING.write_text(str(src))
        return jsonify({'ok': True, 'message': 'Update ready. Restart to apply.'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


def _apply_staging():
    """Called at startup to apply a staged update if one exists."""
    base = Path(__file__).parent
    sf = base / '.update_staging'
    if not sf.exists():
        return
    try:
        staging_path = sf.read_text().strip()
        if not staging_path or not Path(staging_path).exists():
            sf.unlink(missing_ok=True)
            return
        src = Path(staging_path)
        for item in src.iterdir():
            if item.name in ('python', 'Zero_live.bat', 'version.txt',
                '.update_staging', '.launch_args',
                'default_theme.txt', 'logs'):
                continue
            dst = base / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        # Update version
        try:
            r = urllib.request.Request(f'{GITHUB_API}/commits/master', headers={'User-Agent': 'ZeroLive'})
            with urllib.request.urlopen(r, timeout=10) as f:
                new_sha = json.loads(f.read())['sha']
            (base / 'version.txt').write_text(new_sha)
        except Exception:
            pass
        # pip install
        pip = base / 'python' / 'Scripts' / 'pip.exe'
        py = base / 'python' / 'python.exe'
        if pip.exists():
            subprocess.run([str(pip), 'install', '-r', str(base / 'requirements.txt'), '--quiet'], timeout=60)
        elif py.exists():
            subprocess.run([str(py), '-m', 'pip', 'install', '-r', str(base / 'requirements.txt'), '--quiet'], timeout=60)
        shutil.rmtree(src, ignore_errors=True)
        sf.unlink(missing_ok=True)
        _log.info('Staged update applied')
    except Exception as e:
        _log.warning(f'Staging apply failed: {e}')
        sf.unlink(missing_ok=True)


# Apply staged update before anything else
_apply_staging()



@app.route('/api/update/restart')
def update_restart():
    def _do_restart():
        time.sleep(1)
        # Zero_live.bat restart loop handles respawn — just exit
        args = _LAUNCH_ARGS
        if not args:
            # Try saved launch args (hypercorn/gunicorn path)
            la = STATIC_DIR / '.launch_args'
            if la.exists():
                try:
                    args = json.loads(la.read_text())
                except Exception:
                    pass
        if args:
            subprocess.Popen(args, shell=False)
        os._exit(0)
    threading.Thread(target=_do_restart).start()
    return jsonify({'ok': True, 'message': 'Restarting...'})

# ---------------------------------------------------------------------------
# Background pre-warm — fetch ALL events + ALL playback on loop
# ---------------------------------------------------------------------------
def _warm_all():
    while True:
        try:
            events = _get_events()
            if events:
                slugs = []
                for ev in events:
                    s = ev.get('enc_parent') or ev.get('parent') or ev.get('id')
                    if s:
                        slugs.append(s)
                _resolve_many(slugs)
        except Exception:
            pass
        time.sleep(15)

threading.Thread(target=_warm_all, daemon=True).start()

# ---------------------------------------------------------------------------
# Error + static
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.route('/<path:filename>')
def serve_static(filename):
    resolved = (STATIC_DIR / filename).resolve()
    if not str(resolved).startswith(str(STATIC_DIR.resolve())) or not resolved.is_file():
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(STATIC_DIR, filename)

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', sys.argv[1] if len(sys.argv) > 1 else 9090))
    _LAUNCH_ARGS = [sys.executable] + sys.argv[:1] + [str(port)]
    (STATIC_DIR / '.launch_args').write_text(json.dumps(_LAUNCH_ARGS))
    try:
        import webbrowser
        webbrowser.open(f'http://localhost:{port}')
    except Exception:
        pass
    try:
        # Hypercorn ASGI — fastest for async I/O, no buffer
        from hypercorn.config import Config
        from hypercorn.asyncio import serve
        cfg = Config()
        cfg.bind = [f'0.0.0.0:{port}']
        cfg.backlog = 2048
        cfg.keep_alive_timeout = 30
        cfg.access_log_format = '%(h)s %(r)s %(s)s %(b)s'
        asyncio.run(serve(asgi_app, cfg))
    except ImportError:
        try:
            import gunicorn.app.wsgiapp
            sys.argv = ['gunicorn', 'app:app', '--bind', f'0.0.0.0:{port}', '--workers', '4', '--threads', '8', '--worker-class', 'gthread', '--access-logfile', '-', '--log-level', 'info']
            gunicorn.app.wsgiapp.run()
        except ImportError:
            print(f"ZeroLive @ http://0.0.0.0:{port}")
            app.run(host='0.0.0.0', port=port, threaded=True)
