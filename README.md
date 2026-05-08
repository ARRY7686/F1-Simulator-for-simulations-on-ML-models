# F1 Race Simulator

A browser-based Formula 1 race simulator that replays real telemetry data using [FastF1](https://github.com/theOehrly/Fast-F1). Watch every car move around the circuit in real-time, follow drivers, track tyre strategies, and see pit stops — all visualised directly in the browser.

![F1 Race Simulator](https://img.shields.io/badge/FastF1-3.x-red?style=flat-square) ![Python](https://img.shields.io/badge/Python-3.12-blue?style=flat-square) ![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## Features

| Feature | Description |
|---|---|
| **Live car positions** | All 20 cars animated in real-time using official telemetry |
| **Follow mode** | Click any car dot or driver name to lock the camera on them |
| **Pit lane** | Amber-highlighted pit lane route drawn from telemetry |
| **Live standings** | Race order with gaps, tyre compounds, and pit status |
| **Strategy strip** | Per-driver tyre stint bar with white pit-stop markers |
| **Battle detection** | Rows within 1.5 s of each other pulse amber |
| **SC / VSC banners** | Safety Car and Virtual Safety Car periods shown live |
| **Fastest Lap badge** | Purple badge tracks the FL holder in real-time |
| **Overtake log** | Flash notifications whenever a position change happens |
| **Mini-map** | Inset overview showing all cars on the circuit |
| **Final Classification** | End-of-race results screen with stints and FL |
| **Scrubber** | Click/drag timeline to jump to any race moment |
| **Speed control** | 1×, 5×, 15×, 30×, 60× playback speeds |

---

## Quick Start (local)

**Prerequisites:** Python 3.12+

```bash
git clone https://github.com/ARRY7686/F1-Simulator-of-simulations-on-ML-models.git
cd F1-Simulator-of-simulations-on-ML-models

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python serve.py
```

The browser opens automatically at `http://localhost:3000/simulator.html`.

---

## How It Works

1. **Select** a year, Grand Prix, and session (Race / Qualifying / Practice)
2. **Load** — the server fetches telemetry via FastF1 on first load (takes ~1–2 min), then caches it locally
3. **Simulate** — watch the race replay at up to 60× speed

First load for a race downloads ~200 MB of raw telemetry from the F1 timing API and caches it in `race_cache/`. Subsequent loads are instant.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Space` | Play / Pause |
| `← / →` | Rewind / Skip 30 seconds |
| `1` – `5` | Set speed (1×, 5×, 15×, 30×, 60×) |
| `S` | Toggle strategy strips |
| `H` | Open / close help panel |
| `Esc` | Close overlay / exit follow mode |

---

## Project Structure

```
├── serve.py            # Python HTTP server (API + static files)
├── fetch_data.py       # FastF1 telemetry fetcher → race_cache/*.json
├── simulator.html      # Single-page simulator (HTML5 Canvas)
├── build_tracks.py     # Generates track_layouts.json via OpenF1 API
├── track_layouts.json  # Pre-built circuit outlines for 24 tracks
├── Dockerfile          # For deployment (e.g. Render)
└── requirements.txt
```

---

## Deployment (Render)

The app is Docker-ready. See the [Dockerfile](Dockerfile) for details.

1. Push to GitHub
2. Create a **Web Service** on [Render](https://render.com) → select Docker
3. Add two **Persistent Disks**:
   - `/app/race_cache` — stores processed race JSON (1 GB)
   - `/app/cache` — stores FastF1 raw cache (1 GB)
4. Deploy — your app will be live at `https://your-service.onrender.com/simulator.html`

The `PORT` environment variable is read automatically.

---

## Data Source

Telemetry is sourced from the [FastF1](https://github.com/theOehrly/Fast-F1) Python library, which interfaces with the official F1 timing API. Circuit layouts are fetched from the [OpenF1 REST API](https://openf1.org).

---

## License

MIT
