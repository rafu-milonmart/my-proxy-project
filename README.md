# ZeroLive

**Free live sports streaming proxy/player** — watch football, cricket, basketball, tennis, F1, UFC, WWE, and more. Runs locally on your PC with zero server bandwidth for video.

## Quick Start

### Option 1: Installer (Windows, recommended)

1. Download **ZeroLive_Installer.exe** from the [download page](https://rafu-milonmart.github.io/my-proxy-project/)
2. Run the EXE — pick your theme & install path (default: `C:\Zero_live`)
3. Click **Launch** — your browser opens to `http://127.0.0.1:9090`

### Option 2: Manual (any OS)

```bash
pip install -r requirements.txt
python app.py 9090
```

## Features

### Player
- **Auto-format detection** — DASH (with ClearKey DRM), HLS, or direct MP4
- **Smart fallback** — auto-switches to next server on failure, no refresh needed
- **Double-tap fullscreen** — tap left/right half for 10s skip
- **Speed control** — persistent per-event playback speed
- **Stream info overlay** — quality, format, server name at a glance
- **Auto-watch next** — auto-plays the next available event when current ends
- **Smooth volume** — HUD-style volume slider, scroll or drag

### UI & Themes
- **11 themes** — dark, light, amethyst, emerald, ruby, ocean, cyberpunk, sunset, nord, matrix, midnight
- **Glassmorphism cards** — animated gradient orbs, glow effects, smooth transitions
- **Skeleton loading** — shimmer placeholders while data loads
- **Keyboard shortcuts** — `?` help, `/` search, `F` favorites, `R` refresh, `1-9` sport filters, `Esc` close modals
- **System theme detection** — auto-switches between dark/light based on OS preference
- **Settings modal** — theme picker, version info, update checker

### ZL1 — Event Grid (`/`)
- Live event cards with team logos, sport badges, and countdown timers
- Search by team name, league, or sport
- Filter by sport category or favorites
- Sorted by priority (live first, then starting soon)

### ZL2 — IPTV Sports Channels (`/iptv`)
- Auto-fetches M3U with sports channels (filters non-sports via keywords)
- Search + group filter
- Custom M3U URL support — load your own playlist
- Validate channels — auto-detects dead streams
- Channel preview with HLS.js player

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

Open these URLs in VLC, IPTV Smarters, TiviMate, or any IPTV app on any device.

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
- Zero-l.bat also auto-updates on launch

## Routes

| Route | Description |
|---|---|
| `/` | Full UI — event cards, search, sport filters, favorites |
| `/faster` | Lite index — table layout, no CSS |
| `/watch/<slug>` | Full player — 11 themes, custom SVG controls |
| `/lite/<slug>` | Minimal player — native controls |
| `/iptv` | ZL2 — sports IPTV channel grid |
| `/iptv/watch/<id>` | IPTV channel player |
| `/admin` | Admin panel — category mapping, validation |
| `/playlist.m3u` | Full M3U playlist |
| `/playlist/<slug>.m3u` | Event-specific M3U |
| `/<name>.m3u` | Custom named M3U |

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/version` | GET | Current version (commit SHA) |
| `/api/default-theme` | GET | Default theme from installer |
| `/api/custom-m3u` | GET/POST/DELETE | Custom M3U names CRUD |
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

## Architecture

```
User Browser  ←→  ZeroLive Server (Flask)  ←→  Upstream API (Sportzfy)
                       │
                       ├── /proxy/hls/*    →  proxied .m3u8 (few KB)
                       ├── /proxy/iptv/*   →  proxied M3U8 with custom UA/Referer
                       └── /stream/*       →  direct CDN URL (browser fetches segments)
```

- **DASH** — MPD + segments fetched directly by browser from CDN. ClearKey DRM via `setProtectionData()`. Server only proxies manifest URL.
- **HLS** — Manifest proxied (few KB), segments rewritten to direct CDN URLs. Zero server bandwidth for video.
- **IPTV** — Full proxy with custom User-Agent/Referer headers. Manifest + segments proxied with recursive sub-M3U8 rewriting.
- **CDN fix** — no `Referer` header sent to CDN (was causing 400 errors from Fastly).

## Tech Stack

- **Backend**: Flask + curl-cffi (TLS fingerprint impersonation) + cryptography (AES-GCM stream key decrypt)
- **Server**: Hypercorn (ASGI, 4 workers) → gunicorn gthread (4w×8t) → Flask dev fallback
- **Player**: hls.js, dash.js, native HTML5 video
- **Client**: Vanilla JS, CSS custom properties (11 themes), glassmorphism, SVG controls
- **Installer**: PyQt6 GUI → PyInstaller → `ZeroLive_Installer.exe` (~35 MB)
- **Deploy**: Embedded portable Python (no registry/ PATH pollution), robocopy updates

## Configuration

| File | Purpose |
|---|---|
| `MAINM3U.txt` | Default IPTV M3U source URL |
| `custom_m3u_url.txt` | Custom M3U URL override (gitignored) |
| `custom_m3u_names.json` | Custom M3U name mappings (gitignored) |
| `default_theme.txt` | Theme chosen at install (gitignored) |
| `admin_mappings.json` | Category-channel assignments + blocked IDs |
| `version.txt` | Current commit SHA (gitignored, managed by app) |

**Environment variables:**
- `ZL_ADMIN_PASS` — override admin password (default: `admin123`)
- `ZL_DEBUG=1` — enable debug logging + template auto-reload
- `PORT` — override port (default: `9090`)

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `?` | Toggle shortcuts help |
| `/` | Focus search |
| `F` | Toggle favorites filter |
| `R` | Refresh events |
| `1-9` | Quick sport filter |
| `Esc` | Close modal / exit fullscreen |
| `F` (player) | Fullscreen toggle |
| `M` (player) | Mute toggle |
| `Space` (player) | Play/pause |
| `←` `→` (player) | 10s skip |

---

MADE BY RAFIUL HASAN RAFI
