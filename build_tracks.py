#!/usr/bin/env python3
"""
Build track_layouts.json using the OpenF1 REST API.

Format: {slug: {"track": [[x,y],...], "pit": [[x,y],...] or null}}
  - "track": clean racing line from fastest qualifying lap (no pit-lane artefacts)
  - "pit":   pit-lane path taken during a real pit stop in the race

The OpenF1 /location endpoint returns X/Y coordinates in the same
coordinate system as FastF1 car-position data, so the layouts are
guaranteed to align with driver dots in the simulator.

Run once (resumable – already-built tracks are skipped):
    python build_tracks.py
Or for a specific year:
    python build_tracks.py 2023
Force-rebuild pit lanes for already-built circuits:
    python build_tracks.py 2024 --pit-only
"""
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

OPENF1  = "https://api.openf1.org/v1"
OUT     = Path(__file__).parent / "track_layouts.json"
MIN_PTS = 120   # reject track layout if fewer unique points than this
MIN_PIT = 10    # reject pit lane if fewer points than this


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


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


# ── track outline builder ────────────────────────────────────────────────────

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
    Try up to the first 8 drivers in the qualifying session.
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
            start_dt = _parse_dt(lap_start)
            end_dt   = start_dt + timedelta(seconds=lap_dur + 4)
            end_str  = _fmt_dt(end_dt)
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


# ── pit lane builder ──────────────────────────────────────────────────────────

def build_pit_for_session(session_key: int, track_pts: list | None = None) -> list | None:
    """
    Extract a single clean pit-lane path from a RACE session.

    Strategy:
    1. Find any driver who pitted (has an is_pit_out_lap=True lap).
    2. Fetch ALL location data for that full pit-out lap.
    3. Filter to keep only points that are far from the known track outline
       (>25 m from every track point) → those are in the pit lane.
    4. Return the spatially-deduped pit-lane path.

    Falls back to the middle portion of the lap if track_pts is unavailable.
    """
    drivers = _get("drivers", session_key=session_key)
    if not drivers:
        return None

    for drv_info in drivers[:20]:   # try many drivers – not all pit early
        drv_num = drv_info["driver_number"]
        laps = _get("laps", session_key=session_key, driver_number=drv_num)
        if not laps:
            continue

        laps.sort(key=lambda l: l.get("lap_number", 0))

        pit_out_lap = next((l for l in laps if l.get("is_pit_out_lap")), None)
        if not pit_out_lap:
            continue

        date_start = pit_out_lap.get("date_start")
        lap_dur    = pit_out_lap.get("lap_duration")
        if not date_start or not lap_dur:
            continue

        try:
            start_dt  = _parse_dt(date_start)
            end_dt    = start_dt + timedelta(seconds=float(lap_dur))
            start_str = _fmt_dt(start_dt)
            end_str   = _fmt_dt(end_dt)
        except Exception:
            continue

        url = (
            f"{OPENF1}/location"
            f"?session_key={session_key}"
            f"&driver_number={drv_num}"
            f"&date>={start_str}"
            f"&date<={end_str}"
        )
        locs = _get_url(url)
        if len(locs) < 20:
            continue

        all_pts = [
            [round(float(d["x"]), 1), round(float(d["y"]), 1)]
            for d in locs
            if d.get("x") is not None and d.get("y") is not None
        ]
        if not all_pts:
            continue

        if track_pts and len(track_pts) >= 3:
            # Keep only points that are off the racing line → pit lane
            # (pit lanes are typically 20–40 m to the side of the main straight)
            THRESHOLD = 25.0
            pit_pts = [p for p in all_pts
                       if min(_dist(p, t) for t in track_pts) > THRESHOLD]
        else:
            # No track reference – take the first half of the lap (pit entry/stop
            # are usually within the first ~50 % of the pit-out lap)
            pit_pts = all_pts[:len(all_pts) // 2]

        # Spatial dedup at 3 m resolution
        result: list = []
        last = None
        for p in pit_pts:
            if last is None or _dist(p, last) >= 3:
                result.append(p)
                last = p

        if len(result) >= MIN_PIT:
            return result

    return None


# ── main ─────────────────────────────────────────────────────────────────────

def build_all(year: int = 2024, pit_only: bool = False):
    # Load existing layouts; migrate old list-format to new dict-format
    layouts: dict = {}
    if OUT.exists():
        try:
            with open(OUT, encoding="utf-8") as f:
                raw = json.load(f)
            for slug, val in raw.items():
                if isinstance(val, list):
                    # Migrate: old format was just the track points list
                    layouts[slug] = {"track": val, "pit": None}
                else:
                    layouts[slug] = val
        except Exception:
            pass

    qual_sessions = _get("sessions", year=year, session_name="Qualifying")
    race_sessions = _get("sessions", year=year, session_name="Race")
    if not qual_sessions:
        print("ERROR: OpenF1 returned no qualifying sessions - check network.")
        return

    # Fetch meetings to get proper "Bahrain Grand Prix" style names
    meetings       = _get("meetings", year=year)
    mkey_to_name   = {m["meeting_key"]: m["meeting_name"] for m in meetings}
    mkey_to_race   = {s["meeting_key"]: s["session_key"]  for s in race_sessions}

    qual_sessions.sort(key=lambda s: s.get("session_key", 0))
    print(f"Found {len(qual_sessions)} qualifying sessions for {year}\n")

    for sess in qual_sessions:
        qkey  = sess["session_key"]
        mkey  = sess.get("meeting_key", 0)
        gp    = mkey_to_name.get(mkey, f"session_{qkey}")
        slug  = _slug(gp)
        rkey  = mkey_to_race.get(mkey)

        entry = layouts.get(slug, {})

        # Decide what needs building
        need_track = not pit_only and not entry.get("track")
        need_pit   = entry.get("pit") is None   # always try to fill missing pit

        if not need_track and not need_pit:
            n_track = len(entry.get("track") or [])
            n_pit   = len(entry.get("pit") or [])
            print(f"  skip  {gp}  (track={n_track} pts, pit={n_pit} pts)")
            continue

        parts = []
        if need_track:
            print(f"  {gp}  [qual={qkey}] building track ... ", end="", flush=True)
            pts = build_track_for_session(qkey)
            if pts:
                entry["track"] = pts
                parts.append(f"track={len(pts)}")
            else:
                parts.append("track=FAIL")
            time.sleep(0.4)

        if need_pit and rkey:
            print(f"  {gp}  [race={rkey}] building pit lane ... ", end="", flush=True)
            pit = build_pit_for_session(rkey, track_pts=entry.get("track"))
            if pit:
                entry["pit"] = pit
                parts.append(f"pit={len(pit)}")
            else:
                entry["pit"] = None
                parts.append("pit=none")
            time.sleep(0.4)
        elif need_pit and not rkey:
            parts.append("pit=no-race-session")

        print(", ".join(parts) + "  OK")
        layouts[slug] = entry
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(layouts, f, separators=(",", ":"))

    print(f"\nDone. {len(layouts)} tracks in {OUT}")


if __name__ == "__main__":
    args     = sys.argv[1:]
    pit_only = "--pit-only" in args
    year_args = [a for a in args if a.isdigit()]
    year     = int(year_args[0]) if year_args else 2024
    build_all(year, pit_only=pit_only)
