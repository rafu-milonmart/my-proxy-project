import os, sys, re, base64, hashlib, json, logging, time, threading
from urllib.parse import urlparse, urljoin, quote
from pathlib import Path
from flask import Flask, send_from_directory, Response, request, jsonify, render_template
from curl_cffi import requests as curl_requests
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
logging.basicConfig(level=logging.INFO)

UPSTREAM = "https://s1.sportzfytvlive.xyz"
DECRYPT_KEY = "ZESBtSlRTuF4Ac4k757OuasOWOA0W8LcqRn3SFgdInDoMyS8"
STATIC_DIR = Path(__file__).parent
M3U_CACHE = {"time": 0, "data": ""}
M3U_CACHE_TTL = 30
M3U_CACHE_LOCK = threading.Lock()
_EVENTS_CACHE = {"time": 0, "data": []}
_EVENTS_CACHE_LOCK = threading.Lock()
_EVENTS_CACHE_TTL = 15
_http_headers = {
    "Accept": "application/json",
    "X-Requested-With": "lsp",
    "X-LSP-Enc": "1",
}
_media_headers = {
    "Accept": "*/*",
    "Referer": UPSTREAM + "/",
    "Origin": UPSTREAM,
}
_tlocal = threading.local()


def get_http():
    if not hasattr(_tlocal, "session"):
        _tlocal.session = curl_requests.Session(headers=_http_headers, impersonate="chrome124", timeout=15)
    return _tlocal.session


def get_media_http():
    if not hasattr(_tlocal, "media_session"):
        _tlocal.media_session = curl_requests.Session(headers=_media_headers, impersonate="chrome124", timeout=30)
    return _tlocal.media_session


def http_get(url: str) -> tuple[int, str]:
    for attempt in range(2):
        try:
            r = get_http().get(url)
            if r.status_code == 200:
                return r.status_code, r.text
            logging.warning(f"http_get attempt {attempt+1} got {r.status_code} for {url}")
        except Exception as e:
            logging.warning(f"http_get attempt {attempt+1} failed: {e}")
    return 0, ""

def http_get_raw(url: str) -> tuple[int, bytes, str]:
    for attempt in range(2):
        try:
            r = get_http().get(url)
            if r.status_code == 200:
                return r.status_code, r.content, r.headers.get('content-type', 'application/octet-stream')
            logging.warning(f"http_get_raw attempt {attempt+1} got {r.status_code} for {url}")
        except Exception as e:
            logging.warning(f"http_get_raw attempt {attempt+1} failed: {e}")
    return 0, b"", ""


def fetch_events():
    now = time.time()
    if now - _EVENTS_CACHE["time"] < _EVENTS_CACHE_TTL:
        return _EVENTS_CACHE["data"]
    try:
        st, body = http_get(f"{UPSTREAM}/api/upstream/events")
        if st != 200:
            return []
        data = json.loads(body)
        if isinstance(data, list):
            result = data
        elif isinstance(data, dict):
            if 'events' in data and isinstance(data['events'], list):
                result = data['events']
            elif 'data' in data:
                v = data['data']
                if isinstance(v, list):
                    result = v
                elif isinstance(v, dict) and 'events' in v and isinstance(v['events'], list):
                    result = v['events']
                else:
                    result = []
            else:
                found = []
                for k in ('matches', 'result'):
                    if k in data and isinstance(data[k], list):
                        found = data[k]
                        break
                result = found
        else:
            result = []
        with _EVENTS_CACHE_LOCK:
            _EVENTS_CACHE["time"] = time.time()
            _EVENTS_CACHE["data"] = result
        return result
    except Exception as e:
        logging.warning(f"fetch_events failed: {e}")
        return []


def fetch_playback(slug: str):
    try:
        st, body = http_get(f"{UPSTREAM}/api/upstream/playback/{slug}")
        if st != 200:
            return []
        data = json.loads(body)
        if isinstance(data, dict) and 'enc' in data:
            return [data]
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logging.warning(f"fetch_playback({slug}) failed: {e}")
        return []


def decrypt_payload(enc_b64: str, bucket: str) -> dict | None:
    if not HAS_CRYPTO:
        return None
    try:
        buf = base64.b64decode(enc_b64)
        iv, ct, tag = buf[:12], buf[12:-16], buf[-16:]
        key = hashlib.sha256(f"{DECRYPT_KEY}|lsp-v1|{bucket}".encode()).digest()
        pt = AESGCM(key).decrypt(iv, ct + tag, None)
        return json.loads(pt.decode())
    except Exception as e:
        logging.warning(f"Decrypt failed: {e}")
        return None


def resolve_playback_url(slug: str) -> str | None:
    servers = fetch_playback(slug)
    if not servers:
        return None
    result = decrypt_payload(servers[0]['enc'], str(servers[0]['bucket']))
    if result and 'streams' in result and result['streams']:
        for s in result['streams']:
            u = s.get('stream_url', '')
            if '.m3u8' in u:
                return u
        return result['streams'][0].get('stream_url', '')
    return None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/watch/<slug>')
def watch(slug):
    servers = fetch_playback(slug)
    streams = []
    if servers:
        srv = servers[0]
        result = decrypt_payload(srv['enc'], str(srv['bucket']))
        if result and 'streams' in result:
            streams = result['streams']
    event = None
    for e in fetch_events():
        if e.get('enc_parent') == slug or e.get('parent') == slug or e.get('id') == slug:
            event = e
            break
    return render_template('watch.html', slug=slug, event=event, streams=streams)


@app.route('/stream/<slug>/<int:idx>')
def stream_json(slug, idx):
    servers = fetch_playback(slug)
    if not servers:
        return jsonify({"error": "No servers found"}), 404
    srv = servers[0]
    result = decrypt_payload(srv['enc'], str(srv['bucket']))
    if result and 'streams' in result and 0 <= idx < len(result['streams']):
        s = result['streams'][idx]
        return jsonify({
            "url": s['stream_url'],
            "type": s.get('stream_type', ''),
            "drm": {
                "key": s.get('drm_key', ''),
                "kid": s.get('drm_kid', ''),
            } if s.get('drm_key') else None,
        })
    return jsonify({"error": "Invalid stream index or decryption failed"}), 500


@app.route('/playlist.m3u')
def playlist_m3u():
    if time.time() - M3U_CACHE["time"] >= M3U_CACHE_TTL:
        events = fetch_events()
        with M3U_CACHE_LOCK:
            if time.time() - M3U_CACHE["time"] >= M3U_CACHE_TTL:
                lines = ["#EXTM3U"]
                for ev in events:
                    slug = ev.get('enc_parent') or ev.get('parent') or ev.get('id')
                    if not slug:
                        continue
                    title = f"{ev.get('team_a_name', '?')} vs {ev.get('team_b_name', '?')}"
                    league = ev.get('league', '')
                    sport = ev.get('sport', 'Sports')
                    group = sport + (" - " + league if league else "")
                    url = resolve_playback_url(slug)
                    if url:
                        lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="{title}" group-title="{group}",{title}')
                        lines.append(url)
                M3U_CACHE["data"] = "\n".join(lines)
                M3U_CACHE["time"] = time.time()
    return Response(M3U_CACHE["data"], mimetype='audio/x-mpegurl')


@app.route('/playlist/<slug>.m3u')
def event_playlist_m3u(slug):
    lines = ["#EXTM3U"]
    servers = fetch_playback(slug)
    if not servers:
        return Response("#EXTM3U\n", mimetype='audio/x-mpegurl')
    srv = servers[0]
    result = decrypt_payload(srv['enc'], str(srv['bucket']))
    if not result or 'streams' not in result:
        return Response("#EXTM3U\n", mimetype='audio/x-mpegurl')
    for i, s in enumerate(result['streams']):
        label = s.get('label', f'Server {i}')
        url = s.get('stream_url', '')
        if url:
            lines.append(f'#EXTINF:-1 tvg-id="{slug}" tvg-name="{label}",{label}')
            lines.append(url)
    return Response("\n".join(lines), mimetype='audio/x-mpegurl')


@app.route('/api/<path:subpath>')
def proxy_api(subpath):
    if '..' in subpath or '~' in subpath:
        return jsonify({"error": "Forbidden"}), 403
    url = f"{UPSTREAM}/api/{subpath}"
    qs = request.query_string
    if qs:
        if isinstance(qs, bytes):
            url += "?" + qs.decode("utf-8")
        else:
            url += "?" + qs
    try:
        st, body = http_get(url)
        if st == 0:
            return jsonify({"error": "Could not connect to upstream server"}), 502
        return Response(body, status=st, content_type='application/json')
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _hls_rewrite(body, base_dir, slug, idx):
    def rw(line_url):
        p = urlparse(line_url)
        abs_url = line_url if p.netloc else urljoin(base_dir, line_url)
        return '/proxy/hls/' + slug + '/' + str(idx) + '/?url=' + quote(abs_url, safe='')

    lines = body.split('\n')
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
        elif stripped.startswith('#'):
            out.append(re.sub(r'URI="([^"]*)"', lambda m: 'URI="' + rw(m.group(1)) + '"', line))
        else:
            out.append(rw(stripped))
    return '\n'.join(out)


@app.route('/proxy/hls/<slug>/<int:idx>/')
def proxy_hls(slug, idx):
    target = request.args.get('url', '')
    servers = fetch_playback(slug)
    if not servers:
        return jsonify({"error": "No servers found"}), 404
    srv = servers[0]
    result = decrypt_payload(srv['enc'], str(srv['bucket']))
    if not result or 'streams' not in result or not (0 <= idx < len(result['streams'])):
        return jsonify({"error": "Invalid stream"}), 404
    s = result['streams'][idx]

    if not target:
        target = s['stream_url']

    p = urlparse(target)
    if not p.netloc:
        base_url = s['stream_url']
        base_dir = base_url[:base_url.rfind('/') + 1]
        target = urljoin(base_dir, target)

    http = get_media_http()
    resp = None
    for attempt in range(2):
        try:
            resp = http.get(target)
            if resp.status_code == 200:
                break
            logging.warning(f"proxy_hls attempt {attempt+1} got {resp.status_code} for {target[:80]}")
            if attempt == 0:
                time.sleep(1)
        except Exception as e:
            logging.warning(f"proxy_hls attempt {attempt+1} failed: {e}")
            if attempt == 0:
                time.sleep(1)
            continue
    else:
        code = resp.status_code if resp is not None else 'connection failed'
        return jsonify({"error": f"Upstream returned {code}"}), 502

    text = resp.text
    is_playlist = '.m3u8' in target or text.startswith('#EXTM3U') or text.startswith('#EXT-X-')

    if is_playlist:
        base_dir = target[:target.rfind('/') + 1]
        body = _hls_rewrite(text, base_dir, slug, idx)
        return Response(body, content_type='application/vnd.apple.mpegurl')
    else:
        return Response(resp.content, content_type=resp.headers.get('content-type', 'application/octet-stream'))


@app.route('/proxy/manifest/<slug>/<int:idx>')
def proxy_manifest(slug, idx):
    servers = fetch_playback(slug)
    if not servers:
        return jsonify({"error": "No servers found"}), 404
    srv = servers[0]
    result = decrypt_payload(srv['enc'], str(srv['bucket']))
    if not result or 'streams' not in result or not (0 <= idx < len(result['streams'])):
        return jsonify({"error": "Invalid stream"}), 404
    s = result['streams'][idx]
    url = s['stream_url']
    drm_kid = s.get('drm_kid', '')
    drm_key = s.get('drm_key', '')

    http = get_media_http()
    try:
        r = http.get(url)
        if r.status_code != 200:
            return jsonify({"error": f"Could not fetch manifest, upstream returned {r.status_code}"}), 502
        body = r.text
    except Exception as e:
        return jsonify({"error": f"Could not fetch manifest: {e}"}), 502

    if drm_kid and drm_key and '.mpd' in url:
        base_url = url[:url.rfind('/') + 1]
        # Insert BaseURL after <MPD ...> opening tag using regex to keep tag intact
        if '<BaseURL>' not in body:
            body = re.sub(r'(<MPD[^>]*>)', r'\1\n<BaseURL>' + base_url + '</BaseURL>', body, count=1)
        # Inject ClearKey ContentProtection before first AdaptationSet
        kid_clean = drm_kid.replace('-', '').replace(' ', '')
        if len(kid_clean) == 32:
            kid_uuid = f"{kid_clean[:8]}-{kid_clean[8:12]}-{kid_clean[12:16]}-{kid_clean[16:20]}-{kid_clean[20:32]}"
            clearkey_xml = f'''<ContentProtection schemeIdUri="urn:uuid:1077efec-c0b2-4d02-ace3-3c1e52e2fb4b" value="ClearKey">
<cenc:default_KID>{kid_uuid}</cenc:default_KID>
</ContentProtection>'''
            body = re.sub(r'(<AdaptationSet)', clearkey_xml + r'\1', body, count=1)
            body = re.sub(r'(<MPD)([^>]*>)', lambda m: m.group(1) + (' xmlns:cenc="urn:mpeg:cenc:2013"' if 'xmlns:cenc=' not in m.group(2) else '') + m.group(2), body, count=1)

    return Response(body, content_type='application/dash+xml')


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.route('/<path:filename>')
def serve_static(filename):
    resolved = (STATIC_DIR / filename).resolve()
    if not str(resolved).startswith(str(STATIC_DIR.resolve())):
        return jsonify({"error": "Not found"}), 404
    if resolved.is_file():
        return send_from_directory(STATIC_DIR, filename)
    return jsonify({"error": "Not found"}), 404


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090
    print(f"Server: http://0.0.0.0:{port}")
    print(f"M3U:   http://0.0.0.0:{port}/playlist.m3u")
    app.run(host='0.0.0.0', port=port, threaded=True)
