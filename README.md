# ZeroLive

**Free live sports streaming proxy & player** — watch football, cricket, basketball, tennis, F1, UFC, WWE, and more. Runs locally on your PC with zero server bandwidth for video.

ZeroLive acts as a lightweight local proxy that fetches stream metadata from upstream sources, decrypts playback tokens, and serves a polished web UI with a custom-built video player. Video segments are fetched directly by your browser from the CDN — the server never touches the actual video data.

---

## Quick Start

### Option 1: Installer (Windows)

1. Download **ZeroLive_Installer.exe** from the [download page](https://rafu-milonmart.github.io/my-proxy-project/)
2. Run the EXE — pick your theme & install path (default: `C:\Zero_live`)
3. Click **Launch** — your browser opens to `http://127.0.0.1:9090`

### Option 2: Manual (any OS)

```bash
git clone https://github.com/rafu-milonmart/my-proxy-project.git
cd my-proxy-project
pip install -r requirements.txt
python app.py 9090
```

Then open `http://127.0.0.1:9090` in your browser.

### Option 3: Docker

```bash
docker build -t zerolive .
docker run -p 9090:9090 zerolive
```

---

## Features

### Player

- **Auto-format detection** — DASH (with ClearKey DRM), HLS, or direct MP4
- **Smart server fallback** — auto-switches to the next server on failure, no refresh needed
- **Custom progress bar** — full-width scrubber with hover tooltip, smooth thumb, no jitter
- **Double-tap fullscreen** — tap left/right half of player for 10s skip
- **Speed control** — persistent per-event playback speed (0.25x–3x)
- **Stream info overlay** — quality, format, server name at a glance
- **Auto-watch next** — auto-plays the next available event when current ends
- **Volume** — scroll wheel, drag slider, keyboard shortcuts
- **Keyboard-driven** — play/pause, mute, seek, fullscreen, all accessible without mouse

### UI & Themes

- **11 themes** — dark, light, amethyst, emerald, ruby, ocean, cyberpunk, sunset, nord, matrix, midnight
- **Glassmorphism cards** — animated gradient orbs, glow effects, smooth transitions
- **Skeleton loading** — shimmer placeholders while data loads
- **System theme detection** — auto-switches between dark/light based on OS preference
- **Settings modal** — theme picker, version info, update checker
- **Responsive** — works on mobile, tablet, and desktop

### ZL1 — Event Grid (`/`)

- Live event cards with team logos, sport badges, and countdown timers
- Search by team name, league, or sport
- Filter by sport category or favorites
- Sorted by priority (live first, then starting soon)
- Custom M3U naming — give any event a short name and access it at `/<name>.m3u`

### ZL2 — IPTV Sports Channels (`/iptv`)

- Auto-fetches M3U with sports channels (filters non-sports via keyword matching)
- Search + group filter
- Custom M3U URL support — load your own playlist
- Validate channels — auto-detects dead streams
- Channel preview with built-in player

### M3U Support

| Route | Description |
|---|---|
| `/playlist.m3u` | Full playlist of all live events |
| `/playlist/<slug>.m3u` | Single event M3U (includes mapped IPTV channels) |
| `/<custom-name>.m3u` | Custom named M3U (see below) |
| `/+M3U` button | Name any event, access it at `/<name>.m3u` |

**Custom M3U naming**: Click **`+M3U`** on any event card → type a name (e.g. `morocco-vs-canada`) → Save → stream at `/morocco-vs-canada.m3u`. Names persist across restarts. Fuzzy matching works too — `/england.m3u`, `/cricket.m3u`, `/f1.m3u`.

### M3U URLs (copy/share)

Each event card has:
- **VLC** — downloads the `.m3u` file directly
- **M3U** — copies the M3U URL to clipboard

Open these URLs in VLC, IPTV Smarters, TiviMate, or any IPTV app on any device on your local network.

### Lite Version

- `/faster` — zero-CSS event table, search, click to watch
- `/lite/<slug>` — minimal player with native controls, no themes

### Admin Panel (`/admin`)

- Password-gated (default: `admin123`, override via `ZL_ADMIN_PASS`)
- Assign IPTV channels to sport categories
- Validate streams — auto-exclude dead channels
- Recheck blocked channels
- Channels assigned to a category appear in that sport's M3U playlist

### Auto-Update

- Checks GitHub for new commits on startup
- One-click **Check** + **Apply** in settings modal
- Staging system — downloads to temp, applies on next restart
- `Zero_live.bat` also auto-updates on launch

---

## Routes

| Route | Description |
|---|---|
| `/` | Full UI — event cards, search, sport filters, favorites |
| `/faster` | Lite index — table layout, minimal CSS |
| `/watch/<slug>` | Full player — 11 themes, custom SVG controls |
| `/lite/<slug>` | Minimal player — native browser controls |
| `/iptv` | ZL2 — sports IPTV channel grid |
| `/iptv/watch/<id>` | IPTV channel player |
| `/admin` | Admin panel — category mapping, validation |
| `/playlist.m3u` | Full M3U playlist |
| `/playlist/<slug>.m3u` | Event-specific M3U |
| `/<name>.m3u` | Custom named M3U |

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/events` | GET | All live events (upstream + FanCode + TapMad) |
| `/api/version` | GET | Current version (commit SHA) |
| `/api/default-theme` | GET | Default theme from installer |
| `/api/custom-m3u` | GET/POST/DELETE | Custom M3U name CRUD |
| `/api/iptv/m3u-url` | GET/POST | Get/set custom M3U URL |
| `/api/iptv/channels` | GET | All non-excluded IPTV channels |
| `/api/iptv/mappings` | GET | Channels mapped to a sport category |
| `/api/iptv/validate` | POST | Validate all channels |
| `/api/admin/login` | POST | Admin authentication |
| `/api/admin/mappings` | GET/POST | Category-channel mappings |
| `/api/admin/exclude` | POST | Block/unblock a channel |
| `/api/admin/validate` | POST | Validate + auto-exclude dead channels |
| `/api/admin/recheck` | POST | Recheck previously blocked channels |
| `/api/update/check` | GET | Check GitHub for newer commit |
| `/api/update/apply` | GET | Download update (staging) |
| `/api/update/restart` | GET | Restart the server |

---

## Architecture

```
User Browser  ←→  ZeroLive Server (Flask)  ←→  Upstream API (Sportzfy)
                       │
                       ├── /proxy/hls/*    →  proxied .m3u8 (few KB)
                       ├── /proxy/iptv/*   →  proxied M3U8 with custom UA/Referer
                       └── /stream/*       →  direct CDN URL (browser fetches segments)
```

### How streaming works

- **DASH** — MPD manifest + segments fetched directly by browser from CDN. ClearKey DRM decryption via `setProtectionData()`. Server only provides the decrypted manifest URL.
- **HLS** — Manifest proxied through server (few KB), sub-playlists rewritten to route `.ts` segments through CDN directly. Zero server bandwidth for video data.
- **IPTV** — Full proxy with custom User-Agent/Referer headers. Manifest + segments proxied with recursive sub-M3U8 rewriting. Required for streams that check `Referer`.
- **CDN fix** — No `Referer` header sent to CDN endpoints (was causing 400 errors from Fastly).

### Deduplication & merge

Events from multiple sources (upstream, FanCode, TapMad) are merged by fuzzy team-name matching. When the same match appears in multiple sources, streams from all sources are combined into a single event card. This gives you fallback options if one source goes down.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Flask + curl-cffi (TLS fingerprint impersonation) + cryptography (AES-GCM decrypt) |
| **Server** | Hypercorn (ASGI, 4 workers) → gunicorn gthread (4w×8t) → Flask dev fallback |
| **Player** | hls.js, dash.js, native HTML5 video |
| **Frontend** | Vanilla JS, CSS custom properties (11 themes), glassmorphism, SVG controls |
| **Installer** | PyQt6 GUI → PyInstaller → `ZeroLive_Installer.exe` (~35 MB) |
| **Deploy** | Embedded portable Python (no registry/PATH pollution), robocopy updates |

---

## Configuration

### Files

| File | Purpose |
|---|---|
| `MAINM3U.txt` | Default IPTV M3U source URL |
| `custom_m3u_url.txt` | Custom M3U URL override (gitignored) |
| `custom_m3u_names.json` | Custom M3U name mappings (gitignored) |
| `default_theme.txt` | Theme chosen at install (gitignored) |
| `admin_mappings.json` | Category-channel assignments + blocked IDs |
| `version.txt` | Current commit SHA (gitignored, managed by app) |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ZL_ADMIN_PASS` | `admin123` | Override admin panel password |
| `ZL_DEBUG` | `0` | Set to `1` for debug logging + template auto-reload |
| `PORT` | `9090` | Override the listening port |

---

## Keyboard Shortcuts

### Global (event grid)

| Key | Action |
|---|---|
| `?` | Toggle shortcuts help |
| `/` | Focus search |
| `F` | Toggle favorites filter |
| `R` | Refresh events |
| `1-9` | Quick sport filter |
| `Esc` | Close modal / exit fullscreen |

### Player

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `M` | Mute toggle |
| `F` | Fullscreen toggle |
| `←` | Skip back 10s |
| `→` | Skip forward 10s |
| `↑` | Volume up |
| `↓` | Volume down |
| `I` | Stream info overlay |

---

## FAQ

### What is ZeroLive?

ZeroLive is a local streaming proxy that fetches live sports streams from various sources and serves them through a clean web UI on your PC. It never stores or re-broadcasts video — your browser fetches segments directly from the CDN.

### Is it free?

Yes. ZeroLive is open source and free to use. It streams from publicly available sources.

### What sports are supported?

Football (soccer), cricket, basketball, tennis, F1, UFC, WWE, rugby, and more. It depends on what's live on the upstream sources at any given time.

### Do I need a VPN?

It depends on your region and the source. Some streams may be geo-restricted. A VPN can help if a source blocks your region.

### Why does the video buffer or fail to load?

- **Source down** — the upstream CDN may be temporarily unavailable. Try switching servers (click a different server button below the player).
- **Geo-restricted** — some streams only work in certain countries. A VPN may help.
- **Network issues** — try lowering the quality if your connection is slow.
- **ISP blocking** — some ISPs block streaming CDNs. A VPN usually fixes this.

### Can I watch on my phone or TV?

Yes. Open `http://<your-pc-ip>:9090` on any device connected to the same local network. You can also use the M3U URLs in VLC, IPTV Smarters, TiviMate, or any IPTV player app.

### How do I use the M3U URLs in VLC?

1. Copy the M3U URL from an event card (click the **M3U** button)
2. Open VLC → **Media** → **Open Network Stream**
3. Paste the URL and click **Play**

Or on mobile: copy the URL, open VLC, tap the three dots → **Streams** → paste.

### What's the difference between ZL1 and ZL2?

- **ZL1** (`/`) — live event cards from the upstream API. Shows matches that are live or starting soon.
- **ZL2** (`/iptv`) — IPTV sports channels from an M3U playlist. These are 24/7 sports channels, not event-specific streams.

### What's the difference between `/watch` and `/lite`?

- `/watch/<slug>` — full custom player with 11 themes, custom SVG controls, speed control, and keyboard shortcuts.
- `/lite/<slug>` — minimal player using native browser controls. Lighter, works everywhere.

### How does the auto-fallback work?

When a stream fails (CDN error, timeout, etc.), the player automatically tries the next available server for that event. You'll see a brief spinner and then playback resumes on the new server. No manual intervention needed.

### Can I add my own IPTV sources?

Yes. Go to `/iptv` and click the settings icon to enter a custom M3U URL. You can also manage channel-to-sport mappings in the admin panel.

### How do I update ZeroLive?

- **Installer version**: Open settings (gear icon) → **Check for Updates** → **Apply**
- **Manual version**: `git pull origin master`, then restart

### Does it work on Mac/Linux?

Yes. The manual install works on any OS with Python 3.10+. The installer is Windows-only, but you can run the same commands manually.

### What ports does it use?

Default is `9090`. You can change it via the `PORT` environment variable or by passing a port number to `python app.py <port>`.

### Is my data private?

Yes. ZeroLive runs entirely on your local machine. No data is sent to any external server except the upstream stream sources. There's no telemetry, no analytics, no tracking.

---

## License

Open source — use however you like.

---

MADE BY RAFIUL HASAN RAFI
