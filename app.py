import os, sys, re, base64, hashlib, json, logging, time, threading, subprocess, asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
from pathlib import Path
from flask import Flask, send_from_directory, Response, request, jsonify, render_template
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
UPSTREAM = "https://s1.sportzfytvlive.xyz"
DECRYPT_KEY = "ZESBtSlRTuF4Ac4k757OuasOWOA0W8LcqRn3SFgdInDoMyS8"
STATIC_DIR = Path(__file__).parent
VERSION_FILE = STATIC_DIR / 'version.txt'
CURRENT_VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else 'unknown'
GITHUB_API = 'https://api.github.com/repos/rafu-milonmart/my-proxy-project'
DEBUG = os.environ.get('ZL_DEBUG', '0') == '1'
_LAUNCH_ARGS = None  # saved by __main__ for restart

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = DEBUG
asgi_app = WsgiToAsgi(app)

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
_m3u_cache = {}
_DECRYPT_KEY_CACHE = {}

_EV_TTL = 15
_PB_TTL = 30
_M3U_TTL = 30

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

@app.context_processor
def inject_version():
    return {'current_version': CURRENT_VERSION}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/faster')
def faster():
    return render_template('faster.html')

# ---------------------------------------------------------------------------
# IPTV (ZeroLive 2) — sports only
# ---------------------------------------------------------------------------
IPTV_PLAYLIST = Path(__file__).parent / 'combined-playlist.m3u'
M3U_SOURCE_FILE = Path(__file__).parent / 'MAINM3U.txt'

def _get_m3u_url():
    if M3U_SOURCE_FILE.exists():
        return M3U_SOURCE_FILE.read_text(encoding='utf-8').strip()
    return 'https://raw.githubusercontent.com/abusaeeidx/IPTV-Scraper-Zilla/refs/heads/main/combined-playlist.m3u'
_IPTV_CHANNELS = []
_SPORTS_GROUP_NAMES = {'Pixelsports','CricHD'}
_SPORTS_KEYWORDS = ['nfl','nba','mlb','nhl','ncaa','espn','fox sports','nfl network','nba tv',
    'mlb network','nhl network','sport','cricket','football','tennis','soccer',
    'boxing','ufc','wwe','aew','f1','motogp','nascar','golf','olympic',
    'epl','la liga','serie a','bundesliga','ligue 1','champions league','premier league',
    'acc network','sec network','big ten','pac-12','racing','basketball','baseball',
    'hockey','sports','bein','sky sport','tnt sport','eurosport',
    'nfl redzone','nfl sunday','mlb strike','nhl center ice','nba league pass',
    'world sport','xtra sport','fight','wrestling','mma','motorsport',
    'indyc,ar','daytona','supercross','superbike']

def _is_sports(entry):
    name = (entry.get('name') or '').lower()
    group = (entry.get('group') or '').lower()
    if group in _SPORTS_GROUP_NAMES:
        return True
    text = name + ' ' + group
    for kw in _SPORTS_KEYWORDS:
        if kw in text:
            return True
    return False

def _load_iptv():
    global _IPTV_CHANNELS
    _IPTV_CHANNELS = []
    url = _get_m3u_url()
    st, body = _http_get(url)
    if st == 200 and body:
        IPTV_PLAYLIST.write_text(body, encoding='utf-8')
    if not IPTV_PLAYLIST.exists():
        return
    text = IPTV_PLAYLIST.read_text(encoding='utf-8')
    lines = text.splitlines()
    i = 0
    cid = 0
    while i < len(lines):
        if lines[i].startswith('#EXTINF:'):
            entry = {'id': cid, 'name': '', 'logo': '', 'group': '', 'url': '', 'user_agent': '', 'referer': '', 'tvg_id': ''}
            infoline = lines[i]
            m = re.search(r'tvg-id="([^"]*)"', infoline)
            if m: entry['tvg_id'] = m.group(1)
            m = re.search(r'tvg-logo="([^"]*)"', infoline)
            if m: entry['logo'] = m.group(1)
            m = re.search(r'group-title="([^"]*)"', infoline)
            if m: entry['group'] = m.group(1)
            comma = infoline.rfind(',')
            if comma >= 0:
                entry['name'] = infoline[comma+1:].strip()
            i += 1
            while i < len(lines) and lines[i].startswith('#EXTVLCOPT:'):
                opt = lines[i].replace('#EXTVLCOPT:', '')
                if opt.startswith('http-user-agent='):
                    entry['user_agent'] = opt.split('=', 1)[1]
                elif opt.startswith('http-referrer='):
                    entry['referer'] = opt.split('=', 1)[1]
                i += 1
            if i < len(lines) and not lines[i].startswith('#'):
                entry['url'] = lines[i].strip()
            if entry['name'] and entry['url'] and _is_sports(entry):
                entry['id'] = cid
                _IPTV_CHANNELS.append(entry)
                cid += 1
        i += 1

_load_iptv()
_log.info('Loaded %d IPTV channels', len(_IPTV_CHANNELS))

# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------
def _fetch_events():
    return json.loads((_http_get(f"{UPSTREAM}/api/upstream/events")[1] or '{}')).get('events') or []

def _get_events():
    return _cached('events', _EV_TTL, _fetch_events, _ev_cache)

# ---------------------------------------------------------------------------
# Admin Mappings (category -> IPTV channel IDs) — always read from disk
# so multi-worker gunicorn sees latest state.
# ---------------------------------------------------------------------------
MAPPINGS_FILE = STATIC_DIR / 'admin_mappings.json'

def _read_mappings():
    if MAPPINGS_FILE.exists():
        try:
            d = json.loads(MAPPINGS_FILE.read_text())
            return d.get('category_mappings', {}), set(d.get('excluded_ids', []))
        except Exception:
            pass
    return {}, set()

def _save_mappings(cat_map, excluded):
    MAPPINGS_FILE.write_text(json.dumps({
        'category_mappings': cat_map,
        'excluded_ids': list(excluded)
    }, indent=2))

def _get_mapped_ids():
    cat_map, _ = _read_mappings()
    ids = set()
    for v in cat_map.values():
        ids.update(v)
    return ids

def _get_unmapped_channels():
    _, excluded = _read_mappings()
    return [c for c in _IPTV_CHANNELS if c['id'] not in excluded]

def _get_iptv_channels_for_slug(sport=''):
    cat_map, excluded = _read_mappings()
    seen = set()
    out = []
    for c in _IPTV_CHANNELS:
        if c['id'] in cat_map.get(sport, []):
            if c['id'] not in seen and c['id'] not in excluded:
                seen.add(c['id'])
                out.append(c)
    return out

def _dedup_events(events):
    seen = {}
    for e in events:
        key = (e.get('team1',''), e.get('team2',''), e.get('title',''), e.get('sport',''))
        if key not in seen:
            seen[key] = e
    return list(seen.values())

ADMIN_PASS = os.environ.get('ZL_ADMIN_PASS', 'admin123')

@app.route('/admin')
def admin_panel():
    ev_fut = _POOL.submit(_get_events)
    events = _dedup_events(ev_fut.result())
    cats = sorted(set(e.get('sport','') or 'Other' for e in events))
    by_cat = {}
    for e in events:
        cat = e.get('sport','') or 'Other'
        by_cat.setdefault(cat, []).append(e)
    groups = sorted(set(c['group'] for c in _IPTV_CHANNELS if c['group']))
    cat_mappings, excluded_ids = _read_mappings()
    return render_template('admin.html',
        events=events,
        categories=cats,
        events_by_cat=by_cat,
        channels=_IPTV_CHANNELS,
        groups=groups,
        total_channels=len(_IPTV_CHANNELS),
        cat_mappings=cat_mappings,
        excluded_ids=list(excluded_ids))

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.get_json(force=True, silent=True)
    pwd = (data or {}).get('password', '')
    if pwd == ADMIN_PASS:
        return jsonify({'ok': True})
    return jsonify({'ok': False}), 401

@app.route('/api/admin/events')
def admin_events():
    ev_fut = _POOL.submit(_get_events)
    events = _dedup_events(ev_fut.result())
    return jsonify({'ok': True, 'events': events})

@app.route('/api/admin/mappings', methods=['GET', 'POST'])
def admin_mappings():
    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
        cat = data.get('category', '')
        cids = data.get('channel_ids', [])
        if not cat:
            return jsonify({'ok': False, 'error': 'Category required'}), 400
        cat_map, excluded = _read_mappings()
        cat_map[cat] = cids
        _save_mappings(cat_map, excluded)
        _log.info('Mappings saved for category=%s (%d channels)', cat, len(cids))
        return jsonify({'ok': True})
    cat_map, _ = _read_mappings()
    return jsonify({
        'ok': True,
        'category_mappings': cat_map
    })

@app.route('/api/admin/exclude', methods=['POST'])
def admin_exclude():
    data = request.get_json(force=True, silent=True) or {}
    cid = data.get('channel_id')
    exclude = data.get('exclude', True)
    if cid is None:
        return jsonify({'ok': False, 'error': 'channel_id required'}), 400
    cat_map, excluded = _read_mappings()
    if exclude:
        excluded.add(cid)
    else:
        excluded.discard(cid)
    _save_mappings(cat_map, excluded)
    return jsonify({'ok': True, 'excluded_ids': list(excluded)})

def _validate_channel(ch):
    target = ch['url']
    ua = ch.get('user_agent', '')
    ref = ch.get('referer', '')
    headers = {}
    if ua: headers['User-Agent'] = ua
    if ref: headers['Referer'] = ref
    http = _media_sess()
    base_url = target[:target.rfind('/') + 1]
    try:
        resp = http.get(target, headers=headers or None, timeout=10)
        if resp.status_code != 200:
            return ch['id'], False
        text = resp.text
        try:
            _iptv_rewrite(text, ch, base_url)
        except Exception:
            return ch['id'], False
        seg_url = None
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith('#'):
                seg_url = urljoin(base_url, s) if not s.startswith('http') else s
                break
        if seg_url:
            seg_resp = http.get(seg_url, headers=headers or None, timeout=10)
            if seg_resp.status_code != 200 or len(seg_resp.content) == 0:
                return ch['id'], False
        key_match = re.search(r'#EXT-X-KEY[^:]*:.*URI="([^"]*)"', text)
        if key_match:
            key_url = key_match.group(1)
            if not key_url.startswith('http'):
                key_url = urljoin(base_url, key_url)
            try:
                kr = http.get(key_url, headers=headers or None, timeout=10)
                if kr.status_code != 200 or len(kr.content) == 0:
                    return ch['id'], False
            except Exception:
                return ch['id'], False
    except Exception:
        return ch['id'], False
    return ch['id'], True

def _validate_channels(to_check, exclude_failures=True):
    results = {}
    done = 0
    n = len(to_check)
    with ThreadPoolExecutor(max_workers=30) as pool:
        futures = [pool.submit(_validate_channel, c) for c in to_check]
        for f in as_completed(futures):
            try:
                cid, ok = f.result()
                results[str(cid)] = ok
                done += 1
                if done % 50 == 0:
                    _log.info('  Progress: %d/%d', done, n)
            except Exception:
                pass
    if exclude_failures:
        cat_map, excluded = _read_mappings()
        changed = False
        for cid_str, ok in results.items():
            cid = int(cid_str)
            if not ok and cid not in excluded:
                excluded.add(cid)
                changed = True
            elif ok and cid in excluded:
                excluded.discard(cid)
                changed = True
        if changed:
            _save_mappings(cat_map, excluded)
    return results

@app.route('/api/admin/validate', methods=['POST'])
def admin_validate():
    data = request.get_json(force=True, silent=True) or {}
    ids = data.get('channel_ids')
    to_check = [c for c in _IPTV_CHANNELS if ids is None or c['id'] in ids]
    _log.info('Validating %d IPTV channels...', len(to_check))
    results = _validate_channels(to_check)
    ok_count = sum(1 for v in results.values() if v)
    _log.info('Validation done: %d ok, %d blocked', ok_count, len(results) - ok_count)
    _, excluded = _read_mappings()
    return jsonify({'ok': True, 'results': {k: 'ok' if v else 'failed' for k, v in results.items()}, 'excluded_ids': list(excluded)})

@app.route('/api/admin/recheck', methods=['POST'])
def admin_recheck():
    _, excluded = _read_mappings()
    to_check = [c for c in _IPTV_CHANNELS if c['id'] in excluded]
    _log.info('Rechecking %d blocked channels...', len(to_check))
    results = _validate_channels(to_check)
    ok_count = sum(1 for v in results.values() if v)
    _, excluded = _read_mappings()
    _log.info('Recheck done: %d unblocked, %d still blocked', ok_count, len(results) - ok_count)
    return jsonify({'ok': True, 'results': {k: 'ok' if v else 'failed' for k, v in results.items()}, 'excluded_ids': list(excluded)})

@app.route('/api/iptv/mappings')
def iptv_mappings():
    sport = request.args.get('sport', '')
    channels = _get_iptv_channels_for_slug(sport)
    return jsonify({'ok': True, 'channels': channels, 'count': len(channels)})

@app.route('/iptv')
def iptv_index():
    chs = _get_unmapped_channels()
    groups = sorted(set(c['group'] for c in chs if c['group']))
    return render_template('iptv.html', channels=chs, groups=groups, total=len(chs))

@app.route('/api/debug/exclusive')
def debug_exclusive():
    cat_map, excluded = _read_mappings()
    mapped = _get_mapped_ids()
    unmapped = _get_unmapped_channels()
    return jsonify({
        'cat_map': cat_map,
        'mapped_ids': list(mapped),
        'excluded_ids': list(excluded),
        'total_channels': len(_IPTV_CHANNELS),
        'unmapped_count': len(unmapped),
        'unmapped_ids': [c['id'] for c in unmapped],
    })

@app.route('/api/iptv/channels')
def iptv_channels():
    chs = _get_unmapped_channels()
    return jsonify({'ok': True, 'channels': chs, 'total': len(chs)})

@app.route('/api/iptv/validate', methods=['POST'])
def iptv_validate():
    to_check = _get_unmapped_channels()
    _log.info('Public validate on %d channels...', len(to_check))
    results = _validate_channels(to_check)
    ok_count = sum(1 for v in results.values() if v)
    _, excluded = _read_mappings()
    _log.info('Public validate done: %d ok, %d blocked', ok_count, len(results) - ok_count)
    return jsonify({'ok': True, 'results': ['ok' if v else 'failed' for v in results.values()], 'excluded_ids': list(excluded)})

@app.route('/iptv/watch/<int:channel_id>')
def iptv_watch(channel_id):
    for c in _IPTV_CHANNELS:
        if c['id'] == channel_id:
            return render_template('iptv_watch.html', channel=c)
    return jsonify({'error': 'Not found'}), 404

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

@app.route('/lite/<slug>')
def watch_lite(slug):
    ev_fut = _POOL.submit(_get_events)
    streams = _pb_cached(slug)
    events = ev_fut.result()
    event = None
    for e in events:
        if e.get('enc_parent') == slug or e.get('parent') == slug or e.get('id') == slug:
            event = e
            break
    sport = (event or {}).get('sport', '')
    return render_template('watch_lite.html', slug=slug, event=event, streams=streams, sport=sport)

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

@app.route('/playlist/<slug>.m3u')
def event_playlist_m3u(slug):
    streams = _pb_cached(slug)
    lines = ["#EXTM3U"]
    for i, s in enumerate(streams):
        u = s.get('stream_url', '')
        if u:
            lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="LIVE {i+1}",LIVE {i+1}')
            lines.append(u)
    ev = _get_events()
    for e in ev:
        if (e.get('enc_parent') or e.get('parent') or e.get('id')) == slug:
            sport = e.get('sport', '')
            iptv_chs = _get_iptv_channels_for_slug(sport)
            for ch in iptv_chs:
                lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" group-title="IPTV {sport}",{ch["name"]}')
                lines.append(request.host_url.rstrip('/') + f'/proxy/iptv/{ch["id"]}/')
            break
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

def _iptv_rewrite(body, ch, base_dir=''):
    lines = body.split('\n')
    out = []
    prefix = f'/proxy/iptv/{ch["id"]}/seg/'
    for line in lines:
        s = line.strip()
        if not s:
            out.append(line)
        elif s[0] == '#':
            def repl_uri(m):
                url = m.group(1) or m.group(2)
                abs_url = urljoin(base_dir, url) if base_dir else url
                b64 = base64.urlsafe_b64encode(abs_url.encode()).decode().rstrip('=')
                return f'URI="{prefix}{b64}"'
            out.append(re.sub(r'''URI=["']([^"']*)["']|URI=(\S+)''', repl_uri, line))
        else:
            abs_url = urljoin(base_dir, s) if base_dir else s
            b64 = base64.urlsafe_b64encode(abs_url.encode()).decode().rstrip('=')
            out.append(f'{prefix}{b64}')
    return '\n'.join(out)

@app.route('/proxy/iptv/<int:channel_id>/')
def proxy_iptv(channel_id):
    ch = None
    for c in _IPTV_CHANNELS:
        if c['id'] == channel_id:
            ch = c
            break
    if not ch:
        return jsonify({"error": "Not found"}), 404
    target = ch['url']
    ua = ch.get('user_agent', '')
    ref = ch.get('referer', '')
    headers = {}
    if ua:
        headers['User-Agent'] = ua
    if ref:
        headers['Referer'] = ref
    http = _media_sess()
    for attempt in range(2):
        try:
            resp = http.get(target, headers=headers or None)
            if resp.status_code == 200:
                ct = resp.headers.get('content-type', '')
                is_m3u = '.m3u8' in target or 'mpegurl' in ct or 'm3u8' in ct
                if is_m3u:
                    text = resp.text
                    base_dir = target[:target.rfind('/') + 1]
                    r = Response(_iptv_rewrite(text, ch, base_dir), content_type='application/vnd.apple.mpegurl')
                    r.headers['Access-Control-Allow-Origin'] = '*'
                    return r
                r = Response(resp.content, content_type=ct or 'application/octet-stream')
                r.headers['Access-Control-Allow-Origin'] = '*'
                return r
        except Exception:
            if attempt == 0:
                time.sleep(1)
    return jsonify({"error": "Upstream failed"}), 502

@app.route('/proxy/iptv/<int:channel_id>/seg/<path:b64url>')
def proxy_iptv_seg(channel_id, b64url):
    ch = None
    for c in _IPTV_CHANNELS:
        if c['id'] == channel_id:
            ch = c
            break
    if not ch:
        return jsonify({"error": "Not found"}), 404
    try:
        padding = 4 - len(b64url) % 4
        if padding != 4:
            b64url += '=' * padding
        target = base64.urlsafe_b64decode(b64url).decode()
    except Exception:
        return jsonify({"error": "Bad URL"}), 400
    ua = ch.get('user_agent', '')
    ref = ch.get('referer', '')
    headers = {}
    if ua:
        headers['User-Agent'] = ua
    if ref:
        headers['Referer'] = ref
    http = _media_sess()
    for attempt in range(2):
        try:
            resp = http.get(target, headers=headers or None)
            if resp.status_code in (200, 206):
                ct = resp.headers.get('content-type', '')
                is_m3u = '.m3u8' in target or 'mpegurl' in ct or 'm3u8' in ct
                if is_m3u:
                    text = resp.text
                    seg_base = target[:target.rfind('/') + 1] if '/' in target else ''
                    r = Response(_iptv_rewrite(text, ch, seg_base), content_type='application/vnd.apple.mpegurl')
                    r.headers['Access-Control-Allow-Origin'] = '*'
                    return r
                cors = Response(resp.content, content_type=ct or 'application/octet-stream')
                cors.headers['Access-Control-Allow-Origin'] = '*'
                return cors
        except Exception:
            if attempt == 0:
                time.sleep(1)
    return jsonify({"error": "Upstream failed"}), 502

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
                if item.is_dir(): shutil.copytree(item, dst, dirs_exist_ok=True)
                else: shutil.copy2(item, dst)
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
    def _do_restart():
        time.sleep(1)
        if _LAUNCH_ARGS:
            subprocess.Popen(_LAUNCH_ARGS, shell=True)
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
