import os, sys, re, base64, hashlib, json, logging, time, threading, subprocess, asyncio, shutil, urllib.request
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
UPSTREAM = "https://live.sportzfy.life"
DECRYPT_KEY = "ZESBtSlRTuF4Ac4k757OuasOWOA0W8LcqRn3SFgdInDoMyS8"
STATIC_DIR = Path(__file__).parent
VERSION_FILE = STATIC_DIR / 'version.txt'
DEFAULT_THEME_FILE = STATIC_DIR / 'default_theme.txt'
GITHUB_API = 'https://api.github.com/repos/rafu-milonmart/my-proxy-project'
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
    except Exception as e:
        _log.debug('Cache fetch failed for %s: %s', key, e)
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

@app.route('/')
def index():
    return render_template('index.html')

# ---------------------------------------------------------------------------
# IPTV channels (from M3U)
# ---------------------------------------------------------------------------
IPTV_PLAYLIST = Path(__file__).parent / 'combined-playlist.m3u'
CUSTOM_M3U_NAMES_FILE = Path(__file__).parent / 'custom_m3u_names.json'
_custom_m3u_names = {}  # name -> slug

def _save_custom_m3u_names():
    try:
        CUSTOM_M3U_NAMES_FILE.write_text(json.dumps(_custom_m3u_names), encoding='utf-8')
    except Exception:
        pass

def _load_custom_m3u_names():
    global _custom_m3u_names
    try:
        if CUSTOM_M3U_NAMES_FILE.exists():
            _custom_m3u_names = json.loads(CUSTOM_M3U_NAMES_FILE.read_text(encoding='utf-8'))
    except Exception:
        _custom_m3u_names = {}

_load_custom_m3u_names()

M3U_SOURCE_FILE = Path(__file__).parent / 'MAINM3U.txt'

def _get_m3u_urls():
    if M3U_SOURCE_FILE.exists():
        lines = [l.strip() for l in M3U_SOURCE_FILE.read_text(encoding='utf-8').splitlines() if l.strip()]
        return lines if lines else ['https://raw.githubusercontent.com/abusaeeidx/Mrgify-BDIX-IPTV/main/playlist.m3u']
    return ['https://raw.githubusercontent.com/abusaeeidx/Mrgify-BDIX-IPTV/main/playlist.m3u']
_IPTV_CHANNELS = []
_SPORTS_GROUP_NAMES = {'Pixelsports','CricHD','Sports','Live Sports','Sport'}
_iptv_lock = threading.Lock()
_SPORTS_KEYWORDS = [
    'nfl','nba','mlb','nhl','ncaa','ncaab','ncaaf','espn','fox sports',
    'nfl network','nba tv','mlb network','nhl network','nfl redzone','nfl sunday',
    'mlb strike','nhl center ice','nba league pass',
    'sport','sports','live sport','sports live','sports hd','sport hd',
    'cricket','football','tennis','soccer','basketball','baseball','hockey',
    'boxing','ufc','wwe','aew','wrestling','mma','fight','fighting',
    'f1','motogp','nascar','indycar','daytona','supercross','superbike',
    'motorsport','motorsports','racing','race','rally','formula 1','grand prix',
    'golf','pga','lpga','masters','olympic','olympics','paralympic',
    'epl','premier league','la liga','serie a','bundesliga','ligue 1',
    'champions league','europa league','uefa','ucl','uel','fifa','world cup',
    'super bowl','superbowl','playoffs','play-off','final','finals','cup',
    'acc network','sec network','big ten','big 12','pac-12','ivy league',
    'bein','bein sport','sky sport','sky sports','tnt sport','tnt sports',
    'eurosport','bt sport','bt sports','cbs sports','nbcsports','abc sport',
    'world sport','xtra sport','sportsnet','tsn','thescore',
    'rugby','volleyball','handball','badminton','table tennis','swimming',
    'athletics','cycling','darts','snooker','billiards','poker','esports',
    'derby','classic','series','tournament','championship','league','match',
    'tennis channel','golf channel','olympic channel','fight network',
    'red bull','extreme','outdoor','fishing',
    'mls','afl','nrl','cfl','wnba','pba','nba tv','nhl network',
    'bein sports','sky sports main event','sky sports premier',
    'sports 18','star sports','ten sports','sony sports','super sport',
]

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
    new_channels = []
    seen_ids = set()
    old_idx = 0
    merged_text = ''
    for url in _get_m3u_urls():
        st, body = _http_get(url)
        if st == 200 and body:
            merged_text += '\n' + body
    if merged_text.strip():
        IPTV_PLAYLIST.write_text(merged_text.strip(), encoding='utf-8')
    if not IPTV_PLAYLIST.exists():
        return
    text = IPTV_PLAYLIST.read_text(encoding='utf-8')
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith('#EXTINF:'):
            entry = {'id': '', 'name': '', 'logo': '', 'group': '', 'url': '', 'user_agent': '', 'referer': '', 'tvg_id': ''}
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
                entry['id'] = hashlib.md5(entry['url'].encode()).hexdigest()[:12]
                if entry['id'] not in seen_ids:
                    seen_ids.add(entry['id'])
                    entry['_old_idx'] = old_idx
                    old_idx += 1
                    new_channels.append(entry)
        i += 1
    with _iptv_lock:
        _IPTV_CHANNELS = new_channels

_load_iptv()
with _iptv_lock:
    _log.info('Loaded %d IPTV channels', len(_IPTV_CHANNELS))

def _refresh_iptv_loop():
    while True:
        time.sleep(3600)
        try:
            _load_iptv()
            _refresh_sport_index()
            with _iptv_lock:
                _log.info('M3U refreshed: %d channels', len(_IPTV_CHANNELS))
        except Exception:
            _log.warning('M3U refresh failed', exc_info=True)

threading.Thread(target=_refresh_iptv_loop, daemon=True).start()

# ---------------------------------------------------------------------------
# Event helpers + IPTV channel linking
# ---------------------------------------------------------------------------
EVENT_CHANNEL_LINKS_FILE = STATIC_DIR / 'event_channel_links.json'
_excluded_iptv = set()  # dead channel IDs (auto-detected on startup)

def _read_event_links():
    if EVENT_CHANNEL_LINKS_FILE.exists():
        try:
            return json.loads(EVENT_CHANNEL_LINKS_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_event_links(links):
    EVENT_CHANNEL_LINKS_FILE.write_text(json.dumps(links, indent=2))

def _fetch_events():
    return json.loads((_http_get(f"{UPSTREAM}/api/upstream/events")[1] or '{}')).get('events') or []

def _get_events():
    return _cached('events', _EV_TTL, _fetch_events, _ev_cache)

def _dedup_events(events):
    seen = {}
    for e in events:
        key = (e.get('team1',''), e.get('team2',''), e.get('title',''), e.get('sport',''))
        if key not in seen:
            seen[key] = e
    return list(seen.values())

def _validate_channel(ch, timeout=6):
    start = time.time()
    target = ch['url']
    ua = ch.get('user_agent', '')
    ref = ch.get('referer', '')
    headers = {}
    if ua: headers['User-Agent'] = ua
    if ref: headers['Referer'] = ref
    http = _media_sess()
    base_url = target[:target.rfind('/') + 1]
    try:
        resp = http.get(target, headers=headers or None, timeout=min(5, timeout))
        if resp.status_code != 200:
            return ch['id'], False, f'status {resp.status_code}'
        ct = (resp.headers.get('content-type', '') or '').lower()
        text = resp.text
        if text.startswith('#EXT') or '.m3u8' in target:
            if not text.startswith('#EXT'):
                return ch['id'], False, 'not a playlist'
            elapsed = time.time() - start
            if elapsed > timeout:
                return ch['id'], False, 'timeout'
            try:
                _iptv_rewrite(text, ch, base_url)
            except Exception:
                return ch['id'], False, 'rewrite failed'
            remaining = timeout - elapsed
            seg_url = None
            for line in text.splitlines():
                s = line.strip()
                if s and not s.startswith('#'):
                    seg_url = urljoin(base_url, s) if not s.startswith('http') else s
                    break
            if seg_url and remaining > 1.5:
                seg_timeout = min(3, remaining)
                try:
                    seg_resp = http.get(seg_url, headers=headers or None, timeout=seg_timeout)
                    if seg_resp.status_code not in (200, 206) or len(seg_resp.content) == 0:
                        return ch['id'], False, 'bad segment'
                except Exception:
                    return ch['id'], False, 'seg timeout'
                remaining2 = timeout - (time.time() - start)
                key_match = re.search(r'#EXT-X-KEY[^:]*:.*URI="([^"]*)"', text)
                if key_match and remaining2 > 1:
                    key_url = key_match.group(1)
                    if not key_url.startswith('http'):
                        key_url = urljoin(base_url, key_url)
                    try:
                        kr = http.get(key_url, headers=headers or None, timeout=min(3, remaining2))
                        if kr.status_code != 200 or len(kr.content) == 0:
                            return ch['id'], False, 'bad key'
                    except Exception:
                        return ch['id'], False, 'key timeout'
        else:
            if 'text/html' in ct or 'text/plain' in ct:
                if len(resp.content) < 2048:
                    return ch['id'], False, 'not video content'
    except Exception as e:
        return ch['id'], False, str(e)[:50]
    return ch['id'], True, 'ok'

_validation_in_progress = False

def _validate_all_channels(timeout=6):
    global _excluded_iptv, _validation_in_progress
    _validation_in_progress = True
    with _iptv_lock:
        channels = list(_IPTV_CHANNELS)
    _log.info('Validating %d IPTV channels (%ds timeout, 60 workers)...', len(channels), timeout)
    results = {}
    with ThreadPoolExecutor(max_workers=60) as pool:
        fut_map = {pool.submit(_validate_channel, c, timeout): c for c in channels}
        for f in as_completed(fut_map):
            try:
                cid, ok, reason = f.result()
                results[cid] = (ok, reason)
            except Exception:
                pass
    dead = set(cid for cid, (ok, _) in results.items() if not ok)
    with _iptv_lock:
        _excluded_iptv = dead
    ok_count = len(results) - len(dead)
    _log.info('Validation done: %d ok, %d dead', ok_count, len(dead))
    for c in channels:
        cid = c['id']
        if cid in results and not results[cid][0]:
            _log.info('  DEAD: %s — %s', c.get('name', '?'), results[cid][1])
    _validation_in_progress = False

# Retry dead channels periodically
def _retry_dead_loop():
    while True:
        time.sleep(120)
        try:
            with _iptv_lock:
                dead = set(_excluded_iptv)
            if not dead:
                continue
            with _iptv_lock:
                channels = list(_IPTV_CHANNELS)
            to_retry = [c for c in channels if c['id'] in dead]
            if not to_retry:
                continue
            _log.info('Retrying %d dead channels...', len(to_retry))
            with ThreadPoolExecutor(max_workers=30) as pool:
                fut_map = {pool.submit(_validate_channel, c, 5): c for c in to_retry}
                revived = set()
                for f in as_completed(fut_map):
                    try:
                        cid, ok, _ = f.result()
                        if ok:
                            revived.add(cid)
                    except Exception:
                        pass
            if revived:
                with _iptv_lock:
                    _excluded_iptv -= revived
                _log.info('Revived %d channels', len(revived))
        except Exception:
            _log.warning('Retry loop error', exc_info=True)

# Run validation in background after startup
def _startup_validate():
    time.sleep(15)
    try:
        _validate_all_channels(timeout=6)
    except Exception as e:
        _log.warning('Startup validation failed: %s', e)
        _validation_in_progress = False

threading.Thread(target=_startup_validate, daemon=True).start()
threading.Thread(target=_retry_dead_loop, daemon=True).start()

# ---------------------------------------------------------------------------
# Event-channel linking API
# ---------------------------------------------------------------------------
@app.route('/api/sport-links')
def api_sport_links():
    with _sport_iptv_index_lock:
        return jsonify({'ok': True, 'links': dict(_sport_iptv_index)})

@app.route('/api/sport-link', methods=['POST', 'DELETE'])
def api_sport_link():
    data = request.get_json(force=True, silent=True) or {}
    sport = (data.get('sport') or '').strip()
    channel_id = (data.get('channel_id') or '').strip()
    if not sport or not channel_id:
        return jsonify({'ok': False, 'error': 'sport and channel_id required'}), 400
    links = _read_event_links()
    if request.method == 'DELETE':
        cur = links.get(sport, [])
        if isinstance(cur, list):
            links[sport] = [c for c in cur if c != channel_id]
        elif isinstance(cur, str) and cur == channel_id:
            del links[sport]
        else:
            links.pop(sport, None)
    else:
        cur = links.get(sport, [])
        if isinstance(cur, str):
            cur = [cur]
        if channel_id not in cur:
            cur.append(channel_id)
        links[sport] = cur
    _save_event_links(links)
    _refresh_sport_index()
    return jsonify({'ok': True})

@app.route('/api/iptv-channels')
def api_iptv_channels():
    with _iptv_lock:
        alive = [c for c in _IPTV_CHANNELS if c['id'] not in _excluded_iptv]
        return jsonify({'ok': True, 'channels': alive, 'total': len(_IPTV_CHANNELS), 'dead': len(_excluded_iptv), 'validating': _validation_in_progress})

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
    with _sport_iptv_index_lock:
        sport_iptv_ids = list(_sport_iptv_index.get(sport, []))
    with _iptv_lock:
        sport_iptv_channels = [ch for ch in _IPTV_CHANNELS if ch['id'] in sport_iptv_ids]
    return render_template('watch.html', slug=slug, event=event, streams=streams, sport=sport, sport_iptv_channels=sport_iptv_channels)

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

def _find_event(slug):
    """Find event by slug (enc_parent / parent / id)."""
    for e in _get_events():
        if (e.get('enc_parent') or e.get('parent') or e.get('id')) == slug:
            return e
    return None

def _build_playlist_for_event(e, seen_slugs=None):
    """Build M3U lines for a single event + its IPTV channels."""
    if seen_slugs is None:
        seen_slugs = set()
    slug = e.get('enc_parent') or e.get('parent') or e.get('id')
    if not slug or slug in seen_slugs:
        return []
    seen_slugs.add(slug)
    sport = e.get('sport', 'Sports')
    lines = []
    streams = _pb_cached(slug)
    t = f"{e.get('team_a_name', '?')} vs {e.get('team_b_name', '?')}"
    for i, s in enumerate(streams):
        u = s.get('stream_url', '')
        if u:
            lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="{t} LIVE {i+1}",{t} LIVE {i+1}')
            lines.append(u)
    with _sport_iptv_index_lock:
        linked_ids = list(_sport_iptv_index.get(sport, []))
    if linked_ids:
        with _iptv_lock:
            for ch in _IPTV_CHANNELS:
                if ch['id'] in linked_ids and ch['id'] not in _excluded_iptv:
                    lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" group-title="IPTV {sport}",{ch["name"]}')
                    lines.append(request.host_url.rstrip('/') + f'/proxy/iptv/{ch["id"]}/')
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
    """Friendly .m3u — exact custom name first, then fuzzy match."""
    events = _get_events()
    name_lower = name.lower().replace('-', ' ').replace('_', ' ')

    # exact custom name match
    if name in _custom_m3u_names:
        slug = _custom_m3u_names[name]
        for e in events:
            eslug = e.get('enc_parent') or e.get('parent') or e.get('id')
            if eslug == slug:
                lines = ["#EXTM3U"]
                lines.extend(_build_playlist_for_event(e, set()))
                return Response('\n'.join(lines), mimetype='audio/x-mpegurl')

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
        return jsonify({'ok': True, 'names': dict(_custom_m3u_names)})
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get('name') or '').strip().lower().replace(' ', '-')
    slug = (data.get('slug') or '').strip()
    if request.method == 'DELETE':
        _custom_m3u_names.pop(name, None)
        _save_custom_m3u_names()
        return jsonify({'ok': True})
    if not name or not slug:
        return jsonify({'ok': False, 'error': 'name and slug required'}), 400
    if not re.match(r'^[a-z0-9-]+$', name):
        return jsonify({'ok': False, 'error': 'name must be lowercase alphanumeric with hyphens'}), 400
    _custom_m3u_names[name] = slug
    _save_custom_m3u_names()
    return jsonify({'ok': True, 'name': name, 'slug': slug})

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

@app.route('/proxy/iptv/<channel_id>/')
def proxy_iptv(channel_id):
    ch = None
    with _iptv_lock:
        for c in _IPTV_CHANNELS:
            if c['id'] == channel_id:
                ch = c
                break
        if not ch:
            return jsonify({"error": "Not found"}), 404
        if ch['id'] in _excluded_iptv:
            return jsonify({"error": "Channel dead"}), 503
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

@app.route('/proxy/iptv/<channel_id>/seg/<path:b64url>')
def proxy_iptv_seg(channel_id, b64url):
    ch = None
    with _iptv_lock:
        for c in _IPTV_CHANNELS:
            if c['id'] == channel_id:
                ch = c
                break
        if not ch:
            return jsonify({"error": "Not found"}), 404
        if ch['id'] in _excluded_iptv:
            return jsonify({"error": "Channel dead"}), 503
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
    url, drm_kid, drm_key = s['stream_url'], s.get('drm_kid', ''), s.get('drm_key', '')
    http = _media_sess()
    for attempt in range(3):
        try:
            resp = http.get(url, headers={'Origin': UPSTREAM, 'Accept': '*/*', 'X-Requested-With': 'lsp', 'X-LSP-Enc': '1'})
            if resp.status_code == 200:
                body = resp.text
                if '.mpd' in url and drm_kid and drm_key:
                    body = re.sub(r'<ContentProtection[^>]*/>', '', body)
                    body = re.sub(r'<ContentProtection[^>]*>.*?</ContentProtection>', '', body, flags=re.DOTALL)
                    ck = '<ContentProtection schemeIdUri="urn:uuid:e2719d58-a985-b3c9-781a-b030af78d12e" value="ClearKey"/>'
                    body = body.replace('</AdaptationSet>', ck + '\n</AdaptationSet>')
                _manifest_cache[key] = (time.time(), body)
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
                'combined-playlist.m3u', 'custom_m3u_names.json',
                'custom_m3u_url.txt', '.update_staging', '.launch_args',
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

# Build sport->channel links index from event_channel_links.json
def _rebuild_sport_index():
    links = _read_event_links()
    index = {}
    for key, val in links.items():
        if isinstance(val, list):
            index[key] = val
        elif isinstance(val, str):
            index[key] = [val]
        else:
            index[key] = []
    return index

_sport_iptv_index = {}
_sport_iptv_index_lock = threading.Lock()

def _refresh_sport_index():
    global _sport_iptv_index
    idx = _rebuild_sport_index()
    with _sport_iptv_index_lock:
        _sport_iptv_index = idx

_refresh_sport_index()

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
