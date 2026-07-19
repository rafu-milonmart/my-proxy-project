import os, sys, re, base64, hashlib, json, logging, time, threading, subprocess, asyncio, shutil, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin, quote as url_quote, unquote
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
_UA_MOBILE = "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36"
_media_headers = {
    "Accept": "*/*",
    "User-Agent": _UA_MOBILE,
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
        data = json.loads(raw)
        items = data.get('matches', []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not items:
            return []
    except Exception as e:
        _log.warning('FanCode fetch failed: %s', e)
        return []
    _bad_team = re.compile(r'^(Day \d|World Feed|Court \d|Feed \d|Coverage)', re.IGNORECASE)
    events = []
    for item in items:
        mid = item.get('match_id', '')
        if not mid:
            continue
        slug = f'fc_{mid}'
        t1 = item.get('team_1', 'TBD')
        t2 = item.get('team_2', 'TBD')
        if _bad_team.match(str(t1)) or _bad_team.match(str(t2)):
            t1 = item.get('event_name', t1)
            t2 = item.get('event_name', t2)
        status = (item.get('status') or '').upper()
        stream_url = item.get('stream_url', '')
        clearkey = item.get('clearkey', '')
        is_drm = item.get('is_drm', False)
        ev = {
            'id': slug,
            'enc_parent': slug,
            'parent': slug,
            'team_a_name': t1,
            'team_b_name': t2,
            'team_a_logo': item.get('src', ''),
            'team_b_logo': '',
            'sport': item.get('event_category', 'Sports'),
            'league': item.get('event_name', '') or 'FanCode',
            'title': item.get('event_name', f'{t1} vs {t2}'),
            'status': status,
            'is_fancode': True,
            'fancode_stream_url': stream_url,
            'fancode_language': item.get('language', ''),
        }
        if is_drm and clearkey:
            parts = clearkey.split(':') if ':' in clearkey else ['', clearkey]
            ev['fancode_drm_kid'] = parts[0]
            ev['fancode_drm_key'] = parts[1]
        events.append(ev)
    return events

_EV_TTL = 15
_PB_TTL = 30
_M3U_TTL = 30
_FC_TTL = 60
_TM_TTL = 60
_SF_TTL = 60
_ES_TTL = 60

FC_SOURCE = 'https://raw.githubusercontent.com/srhady/Fancode-bd/refs/heads/main/main_playlist.json'
TAPMAD_SOURCE = 'https://raw.githubusercontent.com/srhady/tapmad-bd/refs/heads/main/tapmad_bd.json'
SF_API = 'https://streamfree.top/api/v1'
SF_BASE = 'https://streamfree.top'
ES_API = 'https://api.esportex.site/api'
_fc_cache = {}  # key -> (ts, data)
_tm_cache = {}
_sf_cache = {}
_es_cache = {}

_seg_prefetch = {}       # url -> (ts, bytes, content_type)
_SEG_PREFETCH_TTL = 30   # seconds
_SEG_PREFETCH_WORKERS = 8
_seg_pool = ThreadPoolExecutor(max_workers=_SEG_PREFETCH_WORKERS)

def _prefetch_segments(urls, ref=''):
    """Background-prefetch a list of segment URLs into _seg_prefetch cache."""
    now = time.time()
    # Evict stale entries
    stale = [k for k, v in _seg_prefetch.items() if now - v[0] > _SEG_PREFETCH_TTL * 2]
    for k in stale[:50]:
        _seg_prefetch.pop(k, None)
    to_fetch = []
    for u in urls:
        if u in _seg_prefetch and now - _seg_prefetch[u][0] < _SEG_PREFETCH_TTL:
            continue
        to_fetch.append(u)
    if not to_fetch:
        return
    def _fetch_one(url):
        try:
            hdrs = _build_seg_headers(url, ref)
            r = _media_sess().get(url, headers=hdrs, timeout=10)
            if r.status_code == 200:
                _seg_prefetch[url] = (time.time(), r.content, r.headers.get('content-type', 'video/mp2t'))
        except Exception:
            pass
    for u in to_fetch[:15]:
        _seg_pool.submit(_fetch_one, u)

def _prefetch_hls_segments(m3u8_text, ref=''):
    """Parse rewritten m3u8, extract segment URLs, prefetch them in background."""
    urls = []
    for line in m3u8_text.split('\n'):
        s = line.strip()
        if s and not s.startswith('#') and '/proxy/hls/seg/' in s:
            m = re.search(r'[?&]url=([^&]+)', s)
            if m:
                urls.append(unquote(m.group(1)))
    if urls:
        _prefetch_segments(urls, ref)

def _prefetch_dash_segments(mpd_text, base_url, ref=''):
    """Parse DASH manifest, extract segment URLs, prefetch them in background."""
    urls = []
    for m in re.finditer(r'<SegmentURL\s+media="([^"]+)"', mpd_text):
        seg = m.group(1)
        urls.append(urljoin(base_url, seg))
    for m in re.finditer(r'<SegmentTemplate[^>]+media="([^"]+)"', mpd_text):
        urls.append(urljoin(base_url, m.group(1)))
    if urls:
        _prefetch_segments(urls[:20], ref)

_SF_CATEGORIES = ['soccer', 'cricket']

_SF_QUALITY_PREF = ['720p', '1080p', '540p', '2160p']

def _fetch_tapmad():
    """Fetch TapMad events and normalize."""
    try:
        raw = urllib.request.urlopen(TAPMAD_SOURCE, timeout=10).read().decode('utf-8')
        data = json.loads(raw)
        items = data.get('Matches', []) if isinstance(data, dict) else []
        if not items:
            return []
    except Exception as e:
        _log.warning('TapMad fetch failed: %s', e)
        return []
    events = []
    for item in items:
        mid = str(item.get('EntityId', ''))
        if not mid:
            continue
        video_name = item.get('VideoName', '')
        parts = video_name.split(' vs ')
        t1 = parts[0].strip() if len(parts) > 0 else 'TBD'
        t2 = parts[1].strip().removesuffix(' Live').strip() if len(parts) > 1 else 'TBD'
        stream_url = item.get('stream_url', '')
        status = (item.get('Status', '') or '').upper()
        if not stream_url:
            continue
        cat = (item.get('CategoryName', '') or '').lower()
        if 'dota' in cat or 'esport' in cat:
            sport = 'Esports'
        elif 'cricket' in cat:
            sport = 'Cricket'
        else:
            sport = 'Sports'
        ev = {
            'id': f'tm_{mid}',
            'enc_parent': f'tm_{mid}',
            'parent': f'tm_{mid}',
            'team_a_name': t1,
            'team_b_name': t2,
            'team_a_logo': item.get('ThumbnailStandard', ''),
            'team_b_logo': '',
            'sport': sport,
            'league': item.get('CategoryName', 'Tapmad'),
            'title': f'{t1} vs {t2}',
            'status': status,
            'is_tapmad': True,
            'streams': [
                {
                    'source': 'TapMad',
                    'stream_url': stream_url,
                    'stream_type': 'hls',
                    'referer': 'https://www.tapmad.com/',
                    'user_agent': _UA_MOBILE,
                    'needs_proxy': True,
                }
            ],
        }
        events.append(ev)
    return events


def _fetch_streamfree():
    """Fetch StreamFree events — extract real HLS URLs via token scraping."""
    def _fetch_cat(cat):
        try:
            _, body = _http_get(f"{SF_API}/streams?category={cat}")
            if not body:
                return []
            data = json.loads(body)
            return data.get('streams', []) if isinstance(data, dict) else []
        except Exception as e:
            _log.warning('StreamFree fetch failed (%s): %s', cat, e)
            return []
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(_fetch_cat, _SF_CATEGORIES))
    except Exception:
        results = [_fetch_cat(c) for c in _SF_CATEGORIES]
    raw_streams = []
    for streams in results:
        raw_streams.extend(streams)
    if not raw_streams:
        return []

    def _resolve_stream(item):
        """For one StreamFree stream: fetch embed page, extract tokens, build HLS URL."""
        key = item.get('stream_key') or item.get('id', '')
        if not key:
            return None
        embed_url = item.get('embed_url', '')
        if not embed_url:
            return None
        try:
            _, html = _http_get(embed_url)
            if not html:
                return None
            m = re.search(r'const _0x\s*=\s*(\{.*?\});', html)
            if not m:
                return None
            tokens = json.loads(m.group(1))
        except Exception as e:
            _log.debug('StreamFree token scrape failed (%s): %s', key, e)
            return None
        # Check stream status for available qualities
        best_q = '720p'
        try:
            _, sb = _http_get(f"{SF_BASE}/api/stream-status/{key}")
            if sb:
                status = json.loads(sb)
                quals = status.get('qualities', {})
                for q in _SF_QUALITY_PREF:
                    if quals.get(q):
                        best_q = q
                        break
        except Exception:
            pass
        # Get server type
        server_name = 'origin'
        try:
            _, kb = _http_get(f"{SF_BASE}/get-stream-key/{key}")
            if kb:
                kdata = json.loads(kb)
                server_name = kdata.get('server_name', 'origin')
        except Exception:
            pass
        # Build HLS URL
        path = 'live-cdn' if server_name != 'origin' else 'live'
        p = tokens.get(best_q) or tokens.get('720p') or next(iter(tokens.values()), None)
        if not p:
            return None
        url = f"https://streamfree.top/{path}/{key}{best_q}/index.m3u8?_t={p['_t']}&_e={p['_e']}&_n={p['_n']}"
        return {
            'source': 'StreamFree',
            'stream_url': url,
            'stream_type': 'hls',
            'needs_proxy': True,
            'referer': 'https://streamfree.top/',
        }

    events = []
    for item in raw_streams:
        mid = item.get('stream_key') or item.get('id', '')
        if not mid:
            continue
        t1 = (item.get('team1') or {}).get('name', '') or 'TBD'
        t2 = (item.get('team2') or {}).get('name', '') or 'TBD'
        ts = item.get('match_timestamp', 0)
        ev = {
            'id': f'sf_{mid}',
            'enc_parent': f'sf_{mid}',
            'parent': f'sf_{mid}',
            'team_a_name': t1,
            'team_b_name': t2,
            'team_a_logo': (item.get('team1') or {}).get('logo', ''),
            'team_b_logo': (item.get('team2') or {}).get('logo', ''),
            'sport': item.get('category', 'Sports'),
            'league': 'StreamFree',
            'title': item.get('name', f'{t1} vs {t2}'),
            'starts_at': '',
            'is_live': 1 if ts and (ts * 1000) <= (time.time() * 1000) else 0,
            'is_streamfree': True,
            'streams': [],
        }
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                resolved = pool.submit(_resolve_stream, item).result(timeout=10)
            if resolved:
                ev['streams'] = [resolved]
        except Exception:
            pass
        if not ev['streams']:
            ev['streams'] = [{'source': 'StreamFree', 'stream_url': item.get('embed_url', ''), 'stream_type': 'embed'}]
        events.append(ev)
    return events


def _fetch_esportex():
    """Fetch ESportex events — scrape iframe pages for real HLS URLs."""
    try:
        _, body = _http_get(f"{ES_API}/streams")
        if not body:
            return []
        data = json.loads(body)
        if not isinstance(data, dict) or not data.get('success'):
            return []
    except Exception as e:
        _log.warning('ESportex fetch failed: %s', e)
        return []
    _cat_map = {
        'football': 'football', 'cricket': 'cricket',
    }
    events = []
    for api_cat, matches in data.items():
        if api_cat not in _cat_map or not isinstance(matches, list):
            continue
        sport = _cat_map.get(api_cat, api_cat)
        for item in matches:
            tag = item.get('tag', '')
            parts = tag.split(' vs ') if ' vs ' in tag else tag.split(' - ')
            t1 = parts[0].strip() if len(parts) > 0 else 'TBD'
            t2 = parts[1].strip() if len(parts) > 1 else 'TBD'
            slug = item.get('slug', '')
            iframes = item.get('iframes', [])
            if not iframes:
                continue
            streams = []
            for iframe in iframes:
                url = iframe.get('url', '').replace('http://', 'https://')
                server = iframe.get('server', 'ESportex')
                if not url:
                    continue
                hls_url = _scrape_iframe_hls(url)
                if hls_url:
                    streams.append({
                        'source': server,
                        'stream_url': hls_url,
                        'stream_type': 'hls',
                        'needs_proxy': True,
                        'referer': url.split('/')[0] + '//' + urlparse(url).netloc + '/',
                    })
                else:
                    streams.append({
                        'source': server,
                        'stream_url': url,
                        'stream_type': 'embed',
                    })
            kickoff = item.get('kickoff', '')
            is_live = 0
            if kickoff:
                try:
                    kt = _parse_kickoff(kickoff)
                    if kt and kt <= time.time():
                        is_live = 1
                except Exception:
                    pass
            ev = {
                'id': f'es_{slug}',
                'enc_parent': f'es_{slug}',
                'parent': f'es_{slug}',
                'team_a_name': t1,
                'team_b_name': t2,
                'team_a_logo': '',
                'team_b_logo': '',
                'sport': item.get('league', sport),
                'league': 'ESportex',
                'title': tag,
                'starts_at': '',
                'is_live': is_live,
                'is_esportex': True,
                'streams': streams,
            }
            events.append(ev)
    return events


def _scrape_iframe_hls(url):
    """Fetch an iframe page and extract an HLS (.m3u8) URL if present."""
    try:
        _, html = _http_get(url)
        if html:
            urls = re.findall(r'https?://[^\s"\'\\]+\.m3u8[^\s"\'\\]*', html)
            if urls:
                return urls[0]
    except Exception:
        pass
    return None


def _parse_kickoff(kickoff):
    """Parse ESportex kickoff string to timestamp."""
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.strptime(kickoff, '%Y-%m-%d %H:%M').replace(tzinfo=timezone(timedelta(hours=7)))
        return dt.timestamp()
    except Exception:
        try:
            return datetime.fromisoformat(kickoff.replace(' ', 'T')).timestamp()
        except Exception:
            return None


def _make_dedup_key(team_a, team_b):
    a = (team_a or '').lower().strip()
    b = (team_b or '').lower().strip()
    if not a or not b:
        return None
    return '|'.join(sorted([a, b]))


def _teams_match(a1, b1, a2, b2):
    a1l, b1l = a1.lower().strip(), b1.lower().strip()
    a2l, b2l = a2.lower().strip(), b2.lower().strip()
    if sorted([a1l, b1l]) == sorted([a2l, b2l]):
        return True
    if (a1l in a2l or a2l in a1l) and (b1l in b2l or b2l in b1l):
        return True
    if (a1l in b2l or b2l in a1l) and (b1l in a2l or a2l in b1l):
        return True
    return False


def _dedup_merge(all_events):
    merged = {}
    merged_keys = list(merged.keys())
    for ev in all_events:
        key = _make_dedup_key(ev.get('team_a_name'), ev.get('team_b_name'))
        if not key:
            merged[ev.get('id', str(id(ev)))] = ev
            continue
        matched_key = None
        if key in merged:
            matched_key = key
        else:
            for mk, mev in merged.items():
                if _teams_match(
                    ev.get('team_a_name', ''), ev.get('team_b_name', ''),
                    mev.get('team_a_name', ''), mev.get('team_b_name', '')
                ):
                    matched_key = mk
                    break
        if matched_key is None:
            merged[key] = ev
        else:
            existing = merged[matched_key]
            existing_streams = existing.get('streams', [])
            new_streams = ev.get('streams', [])
            if existing_streams and new_streams:
                existing['streams'] = existing_streams + new_streams
            elif new_streams:
                existing['streams'] = new_streams
            if (ev.get('status', '').upper() == 'LIVE'):
                existing['status'] = 'LIVE'
            if not existing.get('team_a_logo') and ev.get('team_a_logo'):
                existing['team_a_logo'] = ev['team_a_logo']
            if not existing.get('team_b_logo') and ev.get('team_b_logo'):
                existing['team_b_logo'] = ev['team_b_logo']
            for flag in ('is_fancode', 'is_tapmad', 'is_streamfree', 'is_esportex'):
                if ev.get(flag):
                    existing[flag] = True
            # Copy FanCode stream data into merged event
            if ev.get('is_fancode') and ev.get('fancode_stream_url'):
                existing['fancode_stream_url'] = ev['fancode_stream_url']
            for k in ('fancode_drm_kid', 'fancode_drm_key', 'fancode_language'):
                if ev.get(k) and not existing.get(k):
                    existing[k] = ev[k]
            # If upstream event merged into a new-source event, swap key to
            # upstream slug so _pb_cached() can resolve upstream streams.
            is_upstream_merge = (not ev.get('is_fancode') and not ev.get('is_tapmad'))
            if is_upstream_merge and matched_key != key:
                ev_id = ev.get('enc_parent') or ev.get('parent') or ev.get('id')
                if ev_id and ev_id != matched_key:
                    del merged[matched_key]
                    existing['enc_parent'] = ev.get('enc_parent', ev_id)
                    existing['parent'] = ev.get('parent', ev_id)
                    merged[key] = existing
    return list(merged.values())


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
    # Find event by slug
    ev = None
    for e in _get_events():
        if (e.get('enc_parent') or e.get('parent') or e.get('id')) == slug:
            ev = e
            break
    if not ev:
        return []
    # Always try upstream stream resolution (fails safely for non-upstream slugs)
    upstream_streams = []
    is_new_source = ev.get('is_fancode') or ev.get('is_tapmad') or ev.get('is_streamfree') or ev.get('is_esportex')
    try:
        servers = _fetch_playback(slug)
        if servers:
            r = _decrypt(servers[0]['enc'], str(servers[0]['bucket']))
            if r and 'streams' in r:
                upstream_streams = r['streams']
    except Exception:
        pass
    # FanCode: synthetic stream from direct HLS URL
    fc_streams = []
    if ev.get('is_fancode'):
        stream_url = ev.get('fancode_stream_url', '')
        if stream_url:
            s = {'stream_url': stream_url, 'stream_type': 'hls', 'user_agent': _UA_MOBILE, 'referer': '', 'source': 'FanCode', 'needs_proxy': True}
            if ev.get('fancode_drm_kid'):
                s['drm_kid'] = ev['fancode_drm_kid']
                s['drm_key'] = ev['fancode_drm_key']
            fc_streams = [s]
    # Extra streams from merge (TapMad)
    extra_streams = ev.get('streams', [])
    streams = upstream_streams + fc_streams + extra_streams
    # Deduplicate by (source, URL) — keep one per source for fallback
    seen = set()
    unique = []
    for s in streams:
        url = (s.get('stream_url') or '').strip()
        src = (s.get('source') or '').strip()
        key = (src, url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(s)
    streams = unique
    if streams:
        _pb_cache[slug] = (now, streams)
    return streams

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
                return e.get('fancode_stream_url') or None
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
    tm = _cached('tapmad', _TM_TTL, _fetch_tapmad, _tm_cache)
    sf = _cached('streamfree', _SF_TTL, _fetch_streamfree, _sf_cache)
    es = _cached('esportex', _ES_TTL, _fetch_esportex, _es_cache)
    all_events = (upstream or []) + (fc or []) + (tm or []) + (sf or []) + (es or [])
    return _dedup_merge(all_events)

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
    """Return all events (upstream + FanCode + TapMad) merged and deduped."""
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

def _hls_rewrite_proxy(body, base_url, slug, idx, referer=''):
    """Rewrite m3u8 so all sub-playlists and segments go through our proxy.

    Sub-playlists go through /proxy/hls/{slug}/{idx}/ (needs slug for further rewriting).
    Binary segments go through /proxy/hls/seg/ (lightweight, streams directly).
    """
    lines = body.split('\n')
    out = []
    base_dir = base_url[:base_url.rfind('/') + 1]
    pl_prefix = f'/proxy/hls/{slug}/{idx}/'
    seg_prefix = '/proxy/hls/seg/'
    ref_qs = ('&referer=' + url_quote(referer, safe='')) if referer else ''
    for line in lines:
        s = line.strip()
        if not s:
            out.append(line)
        elif s[0] == '#':
            def _rewrite_uri(m, _base=base_dir, _pfx=pl_prefix, _refqs=ref_qs):
                raw = m.group(1) or m.group(2)
                abs_url = urljoin(_base, raw)
                if '.m3u8' in abs_url.lower():
                    return 'URI="' + _pfx + '?url=' + url_quote(abs_url, safe='') + '&rewrite=1' + _refqs + '"'
                return 'URI="' + _pfx + '?url=' + url_quote(abs_url, safe='') + _refqs + '"'
            out.append(re.sub(r'''URI=["']([^"']*)["']|URI=(\S+)''', _rewrite_uri, line))
        else:
            abs_url = urljoin(base_dir, s)
            if '.m3u8' in abs_url.lower():
                out.append(pl_prefix + '?url=' + url_quote(abs_url, safe='') + '&rewrite=1' + ref_qs)
            else:
                out.append(seg_prefix + '?url=' + url_quote(abs_url, safe='') + ref_qs)
    return '\n'.join(out)

def _proxy_fetch(url, ua, ref='', timeout=10):
    hdrs = {'Accept': '*/*'}
    if ref:
        hdrs['Referer'] = ref
        if '://' in ref:
            hdrs['Origin'] = ref.rsplit('/', 1)[0]
    else:
        if 'akamaized.net' in url or 'tapmad' in url.lower():
            hdrs['Referer'] = 'https://www.tapmad.com/'
            hdrs['Origin'] = 'https://www.tapmad.com'
        else:
            p = urlparse(url)
            if p.netloc:
                hdrs['Referer'] = f'{p.scheme}://{p.netloc}/'
                hdrs['Origin'] = f'{p.scheme}://{p.netloc}'
    for attempt in range(3):
        try:
            r = _media_sess().get(url, headers=hdrs, timeout=timeout)
            if r.status_code == 200:
                return r.status_code, r.content, r.headers.get('content-type', '')
            _log.warning('proxy_fetch attempt %d: %s -> HTTP %d', attempt+1, url[:80], r.status_code)
        except Exception as e:
            _log.warning('proxy_fetch attempt %d: %s -> %s', attempt+1, url[:80], e)
        if attempt < 2:
            time.sleep(0.5)
    return 0, b'', ''

def _build_seg_headers(url, ref=''):
    hdrs = {'Accept': '*/*'}
    if ref:
        hdrs['Referer'] = ref
        if '://' in ref:
            hdrs['Origin'] = ref.rsplit('/', 1)[0]
    else:
        p = urlparse(url)
        if p.netloc:
            hdrs['Referer'] = f'{p.scheme}://{p.netloc}/'
            hdrs['Origin'] = f'{p.scheme}://{p.netloc}'
    return hdrs

@app.route('/proxy/hls/seg/')
def proxy_hls_seg():
    """Streaming segment proxy — no slug lookup, streams bytes directly. Serves from prefetch cache if available."""
    url = request.args.get('url', '')
    ref = request.args.get('referer', '')
    if not url:
        return jsonify({"error": "Missing url"}), 400
    url = _clean_url(url)
    hit = _seg_prefetch.get(url)
    if hit and time.time() - hit[0] < _SEG_PREFETCH_TTL:
        return Response(hit[1], status=200, headers={
            'Content-Type': hit[2], 'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=30', 'X-Cache': 'HIT',
        })
    hdrs = _build_seg_headers(url, ref)
    for attempt in range(2):
        try:
            r = _media_sess().get(url, headers=hdrs, timeout=12, stream=True)
            if r.status_code == 200:
                ct = r.headers.get('content-type', 'video/mp2t')
                cl = r.headers.get('content-length', '')
                resp_headers = {
                    'Content-Type': ct,
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'public, max-age=30',
                }
                if cl:
                    resp_headers['Content-Length'] = cl
                return Response(r.iter_content(chunk_size=65536), status=200,
                                headers=resp_headers)
            _log.debug('seg_proxy attempt %d: %s -> HTTP %d', attempt+1, url[:80], r.status_code)
        except Exception as e:
            _log.debug('seg_proxy attempt %d: %s -> %s', attempt+1, url[:80], e)
    return jsonify({"error": "Upstream failed"}), 502

@app.route('/proxy/hls/<slug>/<int:idx>/')
def proxy_hls(slug, idx):
    target = request.args.get('url', '')
    rewrite = request.args.get('rewrite', '') == '1'
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
    ref = request.args.get('referer', '') or s.get('referer', '')
    if not ref and ('akamaized.net' in target or 'tapmad' in target.lower()):
        ref = 'https://www.tapmad.com/'
    ua = request.args.get('user_agent', '') or s.get('user_agent', '') or _UA_MOBILE
    is_media = not ('.m3u8' in target)
    if is_media:
        hdrs = _build_seg_headers(target, ref)
        for attempt in range(2):
            try:
                r = _media_sess().get(target, headers=hdrs, timeout=12, stream=True)
                if r.status_code == 200:
                    ct = r.headers.get('content-type', 'video/mp2t')
                    cl = r.headers.get('content-length', '')
                    resp_h = {'Content-Type': ct, 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=30'}
                    if cl:
                        resp_h['Content-Length'] = cl
                    return Response(r.iter_content(chunk_size=65536), status=200, headers=resp_h)
            except Exception:
                pass
        return jsonify({"error": "Upstream failed"}), 502
    code, body, ct = _proxy_fetch(target, ua, ref, 15)
    if code == 200:
        text = body.decode('utf-8', errors='replace')
        if text.startswith('#EXT'):
            if rewrite:
                rewritten = _hls_rewrite_proxy(text, target, slug, idx, ref)
                _prefetch_hls_segments(rewritten, ref)
                return Response(rewritten, content_type='application/vnd.apple.mpegurl')
            normal = _hls_rewrite(text, target[:target.rfind('/') + 1])
            _prefetch_hls_segments(normal, ref)
            return Response(normal, content_type='application/vnd.apple.mpegurl')
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
    target = _clean_url(target)
    hit = _seg_prefetch.get(target)
    if hit and time.time() - hit[0] < _SEG_PREFETCH_TTL:
        return Response(hit[1], status=200, headers={
            'Content-Type': hit[2], 'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'public, max-age=30', 'X-Cache': 'HIT',
        })
    ua = s.get('user_agent', '') or _UA_MOBILE
    ref = s.get('referer', '') or ''
    if not ref:
        p = urlparse(base_url)
        ref = f'{p.scheme}://{p.netloc}/' if p.netloc else ''
    code, body, ct = _proxy_fetch(target, ua, ref, 10)
    if code == 200:
        return Response(body, content_type=ct or 'application/octet-stream')
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
    ua = s.get('user_agent', '') or _UA_MOBILE
    ref = s.get('referer', '') or ''
    if not ref:
        p = urlparse(url)
        ref = f'{p.scheme}://{p.netloc}/' if p.netloc else ''
    for attempt in range(3):
        try:
            hdrs = {'Accept': '*/*'}
            if ref:
                hdrs['Referer'] = ref
                hdrs['Origin'] = ref.rstrip('/')
            r = _media_sess().get(url, headers=hdrs, timeout=15)
            if r.status_code == 200:
                body = r.text
            else:
                _log.debug('proxy_manifest attempt %d: %s -> HTTP %d', attempt+1, url[:80], r.status_code)
                raise Exception(f'HTTP {r.status_code}')
            if '.mpd' in url:
                body = re.sub(r'<ContentProtection schemeIdUri="urn:uuid:[^"]*"[^>]*/>', '', body)
                body = re.sub(r'<ContentProtection schemeIdUri="urn:uuid:[^"]*"[^>]*>.*?</ContentProtection>', '', body, flags=re.DOTALL)
                if drm_kid and drm_key:
                    body = body.replace(
                        '</AdaptationSet>',
                        '<ContentProtection schemeIdUri="urn:uuid:e2719d58-a985-b3c9-781a-b030af78d12e" value="ClearKey"/>\n</AdaptationSet>',
                        0)
                body = re.sub(r'<BaseURL>[^<]*</BaseURL>', '', body)
            _manifest_cache[key] = (time.time(), body)
            if '.mpd' in url:
                base_dir = url[:url.rfind('/') + 1]
                _prefetch_dash_segments(body, base_dir, ref)
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
