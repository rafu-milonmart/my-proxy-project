import os, sys, re, base64, hashlib, json, logging, time, threading, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
from pathlib import Path
from flask import Flask, send_from_directory, Response, request, jsonify, render_template
from curl_cffi import requests as curl_requests
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
UPSTREAM = "https://s1.sportzfytvlive.xyz"
DECRYPT_KEY = "ZESBtSlRTuF4Ac4k757OuasOWOA0W8LcqRn3SFgdInDoMyS8"
STATIC_DIR = Path(__file__).parent
VERSION_FILE = STATIC_DIR / 'version.txt'
CURRENT_VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else 'unknown'
GITHUB_API = 'https://api.github.com/repos/rafu-milonmart/my-proxy-project'
DEBUG = os.environ.get('ZL_DEBUG', '0') == '1'

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = DEBUG

if not DEBUG:
    logging.disable(logging.CRITICAL)

_POOL = ThreadPoolExecutor(max_workers=16)
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
_m3u_cache_val = None
_m3u_cache_ts = 0
_DECRYPT_KEY_CACHE = {}

_EV_TTL = 15
_PB_TTL = 30
_M3U_TTL = 30

def _cached(key, ttl, fetcher, store=None):
    """Atomic cache get-or-set. No locks — cheap dict read wins."""
    now = time.time()
    store = store or _ev_cache
    hit = store.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fetcher()
        store[key] = (time.time(), val)
        return val
    except Exception:
        return hit[1] if hit else []

def _pb_cached(slug):
    now = time.time()
    hit = _pb_cache.get(slug)
    if hit and now - hit[0] < _PB_TTL:
        return hit[1]
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
    for _ in range(2):
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
    streams = _pb_cached(slug)
    for s in streams:
        u = s.get('stream_url', '')
        if '.m3u8' in u:
            return u
    return streams[0]['stream_url'] if streams else None

def _resolve_many(slugs):
    fut = {_POOL.submit(_resolve_one, s): s for s in slugs}
    out = {}
    for f in as_completed(fut, timeout=30):
        try:
            out[fut[f]] = f.result()
        except Exception:
            out[fut[f]] = None
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

@app.context_processor
def inject_version():
    return {'current_version': CURRENT_VERSION}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/faster')
def faster():
    return render_template('faster.html')

@app.route('/watch/<slug>')
def watch(slug):
    ev_fut = _POOL.submit(lambda: _cached('events', _EV_TTL, lambda: json.loads((_http_get(f"{UPSTREAM}/api/upstream/events")[1] or '{}')).get('events') or [], _ev_cache))
    streams = _pb_cached(slug)
    events = ev_fut.result()
    event = None
    for e in events:
        if e.get('enc_parent') == slug or e.get('parent') == slug or e.get('id') == slug:
            event = e
            break
    return render_template('watch.html', slug=slug, event=event, streams=streams)

@app.route('/lite/<slug>')
def watch_lite(slug):
    ev_fut = _POOL.submit(lambda: _cached('events', _EV_TTL, lambda: json.loads((_http_get(f"{UPSTREAM}/api/upstream/events")[1] or '{}')).get('events') or [], _ev_cache))
    streams = _pb_cached(slug)
    events = ev_fut.result()
    event = None
    for e in events:
        if e.get('enc_parent') == slug or e.get('parent') == slug or e.get('id') == slug:
            event = e
            break
    return render_template('watch_lite.html', slug=slug, event=event, streams=streams)

@app.route('/stream/<slug>/<int:idx>')
def stream_json(slug, idx):
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    s = streams[idx]
    return jsonify({
        "url": s['stream_url'],
        "type": s.get('stream_type', ''),
        "drm": {"key": s['drm_key'], "kid": s['drm_kid']} if s.get('drm_key') else None,
    })

@app.route('/playlist.m3u')
def playlist_m3u():
    global _m3u_cache_val, _m3u_cache_ts
    now = time.time()
    if now - _m3u_cache_ts >= _M3U_TTL:
        events = _cached('events', _EV_TTL, lambda: json.loads((_http_get(f"{UPSTREAM}/api/upstream/events")[1] or '{}')).get('events') or [], _ev_cache)
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
        _m3u_cache_val = '\n'.join(lines)
        _m3u_cache_ts = time.time()
    return Response(_m3u_cache_val, mimetype='audio/x-mpegurl')

@app.route('/playlist/<slug>.m3u')
def event_playlist_m3u(slug):
    streams = _pb_cached(slug)
    lines = ["#EXTM3U"]
    for i, s in enumerate(streams):
        u = s.get('stream_url', '')
        if u:
            lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="LIVE {i+1}",LIVE {i+1}')
            lines.append(u)
    return Response('\n'.join(lines), mimetype='audio/x-mpegurl')

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

@app.route('/proxy/hls/<slug>/<int:idx>/')
def proxy_hls(slug, idx):
    target = request.args.get('url', '')
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    s = streams[idx]
    if not target:
        target = s['stream_url']
    p = urlparse(target)
    if not p.netloc:
        target = urljoin(s['stream_url'][:s['stream_url'].rfind('/') + 1], target)
    http = _media_sess()
    for attempt in range(2):
        try:
            resp = http.get(target)
            if resp.status_code == 200:
                text = resp.text
                if '.m3u8' in target or text.startswith('#EXT'):
                    return Response(_hls_rewrite(text, target[:target.rfind('/') + 1]), content_type='application/vnd.apple.mpegurl')
                return Response(resp.content, content_type=resp.headers.get('content-type', 'application/octet-stream'))
        except Exception:
            if attempt == 0:
                time.sleep(1)
    return jsonify({"error": "Upstream failed"}), 502

@app.route('/proxy/dashseg/<slug>/<int:idx>/<path:seg_path>')
def proxy_dash_seg(slug, idx, seg_path):
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    base_dir = streams[idx]['stream_url']
    base_dir = base_dir[:base_dir.rfind('/') + 1]
    target = urljoin(base_dir, seg_path)
    qs = request.query_string
    if qs:
        target += '?' + (qs.decode() if isinstance(qs, bytes) else qs)
    http = _media_sess()
    for _ in range(2):
        try:
            resp = http.get(target)
            if resp.status_code in (200, 206):
                return Response(resp.content, content_type=resp.headers.get('content-type', 'application/octet-stream'))
            if resp.status_code in (301, 302) and resp.headers.get('location'):
                resp = http.get(resp.headers['location'])
                if resp.status_code in (200, 206):
                    return Response(resp.content, content_type=resp.headers.get('content-type', 'application/octet-stream'))
        except Exception:
            time.sleep(0.5)
    return jsonify({"error": "Segment failed"}), 502

@app.route('/proxy/manifest/<slug>/<int:idx>')
def proxy_manifest(slug, idx):
    streams = _pb_cached(slug)
    if not streams or idx >= len(streams):
        return jsonify({"error": "Not found"}), 404
    s = streams[idx]
    url, drm_kid, drm_key = s['stream_url'], s.get('drm_kid', ''), s.get('drm_key', '')
    http = _media_sess()
    for attempt in range(3):
        try:
            resp = http.get(url, headers={'Origin': UPSTREAM, 'Accept': '*/*', 'X-Requested-With': 'lsp', 'X-LSP-Enc': '1'})
            if resp.status_code == 200:
                body = resp.text
                if '.mpd' in url and drm_kid and drm_key:
                    ck = '\n<ContentProtection schemeIdUri="urn:uuid:e2719d58-a985-b3c9-781a-b030af78d12e" value="ClearKey"/>'
                    body = re.sub(r'(<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011"[^>]*/>)', r'\1' + ck, body)
                return Response(body, content_type='application/dash+xml')
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1)
            _pb_cache.pop(slug, None)
            streams = _pb_cached(slug)
            if streams and idx < len(streams):
                url = streams[idx]['stream_url']
    return jsonify({"error": "Manifest failed"}), 502

# ---------------------------------------------------------------------------
# Version / Update
# ---------------------------------------------------------------------------
@app.route('/api/version')
def api_version():
    return jsonify({'ok': True, 'version': CURRENT_VERSION})

@app.route('/api/update/check')
def update_check():
    try:
        import urllib.request
        r = urllib.request.Request(f'{GITHUB_API}/commits/master', headers={'User-Agent': 'ZeroLive'})
        with urllib.request.urlopen(r, timeout=10) as f:
            latest = json.loads(f.read())['sha']
        return jsonify({'ok': True, 'current': CURRENT_VERSION[:12], 'latest': latest[:12], 'has_updates': latest != CURRENT_VERSION})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/update/apply')
def update_apply():
    try:
        import urllib.request, zipfile, io, tempfile, shutil
        base = Path(__file__).parent
        r = urllib.request.Request(f'{GITHUB_API}/zipball/master', headers={'User-Agent': 'ZeroLive'})
        with urllib.request.urlopen(r, timeout=60) as f:
            z = zipfile.ZipFile(io.BytesIO(f.read()))
            d = Path(tempfile.mkdtemp())
            z.extractall(d)
            src = next(p for p in d.iterdir() if p.is_dir())
            for item in src.iterdir():
                if item.name in ('python', 'Zero_live.bat', 'version.txt'): continue
                dst = base / item.name
                (shutil.copytree if item.is_dir() else shutil.copy2)(item, dst)
            shutil.rmtree(d, ignore_errors=True)
        pip = str(base / 'python' / 'Scripts' / 'pip.exe')
        subprocess.run([pip if os.path.exists(pip) else sys.executable, 'install', '-r', str(base / 'requirements.txt'), '--quiet'], timeout=60)
        r2 = urllib.request.Request(f'{GITHUB_API}/commits/master', headers={'User-Agent': 'ZeroLive'})
        with urllib.request.urlopen(r2, timeout=10) as f:
            new_sha = json.loads(f.read())['sha']
        (base / 'version.txt').write_text(new_sha)
        global CURRENT_VERSION
        CURRENT_VERSION = new_sha
        return jsonify({'ok': True, 'message': 'Update applied. Restarting...'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/api/update/restart')
def update_restart():
    threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0))).start()
    return jsonify({'ok': True, 'message': 'Restarting...'})

# ---------------------------------------------------------------------------
# Background pre-warm — fetch ALL events + ALL playback on loop
# ---------------------------------------------------------------------------
def _warm_all():
    while True:
        try:
            events = _cached('events', _EV_TTL, lambda: json.loads((_http_get(f"{UPSTREAM}/api/upstream/events")[1] or '{}')).get('events') or [], _ev_cache)
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
    try:
        import webbrowser
        webbrowser.open(f'http://localhost:{port}')
    except Exception:
        pass
    try:
        import gunicorn.app.wsgiapp
        sys.argv = ['gunicorn', 'app:app', '--bind', f'0.0.0.0:{port}', '--workers', '4', '--threads', '8', '--worker-class', 'gthread', '--access-logfile', '-']
        gunicorn.app.wsgiapp.run()
    except ImportError:
        print(f"ZeroLive @ http://0.0.0.0:{port}")
        app.run(host='0.0.0.0', port=port, threaded=True)
