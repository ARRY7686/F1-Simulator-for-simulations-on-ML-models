#!/usr/bin/env python3
"""
Build track_layouts.json using the OpenF1 REST API.

The OpenF1 /location endpoint returns X/Y coordinates in the same
coordinate system as FastF1 car-position data, so the layouts are
guaranteed to align with driver dots in the simulator.

Run once (resumable – already-built tracks are skipped):
    python build_tracks.py
Or for a specific year:
    python build_tracks.py 2023
"""
import json
import math
import sys
import time
from pathlib import Path

import requests

OPENF1 = "https://api.openf1.org/v1"
OUT    = Path(__file__).parent / "track_layouts.json"
MIN_PTS = 120   # reject layout if fewer unique points than this


# ── helpers ──────────────────────────────────────────────────────────────────

def _slug(s: str) -> str:
    return s.lower().replace(" ", "_").replace("/", "_").replace("'", "")


def _get(endpoint: str, **params) -> list:
    """GET from OpenF1 with simple retry."""
    url = f"{OPENF1}/{endpoint}"
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 3:
                print(f"\n    [warn] {endpoint} failed: {e}", flush=True)
                return []
            time.sleep(2 ** attempt)
    return []


def _get_url(url: str) -> list:
    """GET an arbitrary URL with retry (needed for OpenF1 date-filter params)."""
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 3:
                print(f"\n    [warn] url fetch failed: {e}", flush=True)
                return []
            time.sleep(2 ** attempt)
    return []


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ── core builder ─────────────────────────────────────────────────────────────

def _pts_from_locs(locs: list) -> list:
    """
    Convert a time-ordered list of OpenF1 location dicts for ONE lap into a
    clean [[X,Y], ...] circuit outline:
      1. Extract raw (x, y) pairs, skip nulls.
      2. Linearly interpolate over short dropouts (< 400 m jump).
      3. Deduplicate spatially (keep points >= 8 m apart).
    """
    raw = [(float(d["x"]), float(d["y"]))
           for d in locs
           if d.get("x") is not None and d.get("y") is not None]
    if len(raw) < 80:
        return []

    # interpolate gaps
    filled = [raw[0]]
    for cur in raw[1:]:
        prev = filled[-1]
        d = _dist(prev, cur)
        if 20 < d < 400:           # genuine short dropout - fill it
            steps = max(2, int(d / 10))
            for k in range(1, steps):
                t = k / steps
                filled.append((prev[0] + t * (cur[0] - prev[0]),
                                prev[1] + t * (cur[1] - prev[1])))
        # if d > 400 it is likely a teleport (pit-lane jump) - just append cur
        filled.append(cur)

    # spatial deduplication
    pts = []
    last = None
    for p in filled:
        if last is None or _dist(p, last) >= 8:
            pts.append([round(p[0], 1), round(p[1], 1)])
            last = p

    return pts


def build_track_for_session(session_key: int):
    """
    Try up to the first 8 drivers in the session.
    For each driver find their fastest clean lap, fetch location data for
    exactly that lap window, and return the circuit outline.
    """
    drivers = _get("drivers", session_key=session_key)
    if not drivers:
        return None

    for drv_info in drivers[:8]:
        drv_num = drv_info["driver_number"]

        laps = _get("laps", session_key=session_key, driver_number=drv_num)
        valid = [
            l for l in laps
            if l.get("lap_duration") and 55 < float(l["lap_duration"]) < 600
            and l.get("date_start")
        ]
        if not valid:
            continue

        fastest = min(valid, key=lambda l: float(l["lap_duration"]))
        lap_start  = fastest["date_start"]
        lap_dur    = float(fastest["lap_duration"])

        try:
            from datetime import datetime, timedelta
            fmt_in   = lap_start.replace("Z", "+00:00")
            start_dt = datetime.fromisoformat(fmt_in)
            end_dt   = start_dt + timedelta(seconds=lap_dur + 4)
            end_str  = end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
        except Exception:
            continue

        url = (
            f"{OPENF1}/location"
            f"?session_key={session_key}"
            f"&driver_number={drv_num}"
            f"&date>={lap_start}"
            f"&date<={end_str}"
        )
        locs = _get_url(url)
        if len(locs) < 80:
            continue

        pts = _pts_from_locs(locs)
        if len(pts) < MIN_PTS:
            continue

        # Trim a tiny margin at each end to remove pit-lane artefacts
        trim = max(2, len(pts) // 40)
        return pts[trim:-trim]

    return None


# ── main ─────────────────────────────────────────────────────────────────────

def build_all(year: int = 2024):
    layouts = {}
    if OUT.exists():
        try:
            with open(OUT, encoding="utf-8") as f:
                layouts = json.load(f)
        except Exception:
            pass

    sessions = _get("sessions", year=year, session_name="Qualifying")
    if not sessions:
        print("ERROR: OpenF1 returned no qualifying sessions - check network.")
        return

    # Fetch meetings to get proper "Bahrain Grand Prix" style names
    meetings = _get("meetings", year=year)
    mkey_to_name = {m["meeting_key"]: m["meeting_name"] for m in meetings}

    sessions.sort(key=lambda s: s.get("session_key", 0))
    print(f"Found {len(sessions)} qualifying sessions for {year}\n")

    for sess in sessions:
        skey  = sess["session_key"]
        gp    = mkey_to_name.get(sess.get("meeting_key", 0), f"session_{skey}")
        slug  = _slug(gp)

        if slug in layouts:
            print(f"  skip  {gp}  ({len(layouts[slug])} pts already)")
            continue

        print(f"  {gp}  [key={skey}] ... ", end="", flush=True)
        pts = build_track_for_session(skey)

        if pts:
            layouts[slug] = pts
            print(f"{len(pts)} pts  OK")
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(layouts, f, separators=(",", ":"))
        else:
            print("SKIP - no usable data")

        time.sleep(0.4)

    print(f"\nDone. {len(layouts)} tracks saved to {OUT}")


if __name__ == "__main__":
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    build_all(year)
