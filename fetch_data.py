#!/usr/bin/env python3
"""
F1 Race Data Fetcher
Usage:  python fetch_data.py [year] [grand_prix] [session]
Example: python fetch_data.py 2024 Bahrain R
         python fetch_data.py 2023 Monaco Q
"""
import sys
import json
import math
from pathlib import Path

import fastf1
import pandas as pd
import numpy as np


def hex_color(team_color):
    """Normalise team color to #RRGGBB, fall back to white."""
    if not team_color or (isinstance(team_color, float) and math.isnan(team_color)):
        return "#ffffff"
    s = str(team_color).strip()
    return s if s.startswith("#") else f"#{s}"


def safe_seconds(td):
    """Convert timedelta / NaT to float seconds, or None."""
    try:
        import pandas as _pd
        if td is None or td is _pd.NaT:
            return None
        v = float(td.total_seconds())
        return None if math.isnan(v) else v
    except Exception:
        return None


def safe_str(val, default="UNKNOWN"):
    """Return a clean uppercase string, treating NaN/None/empty as default."""
    import pandas as _pd
    try:
        if val is None or val is _pd.NaT:
            return default
        if isinstance(val, float) and math.isnan(val):
            return default
        s = str(val).strip().upper()
        return s if s and s != 'NAN' and s != 'NONE' else default
    except Exception:
        return default


def fetch_race_data(
    year: int = 2024,
    grand_prix: str = "Bahrain",
    session_type: str = "R",
    output_path: str = "race_data.json",
    progress_cb=None,
):
    if progress_cb is None:
        progress_cb = print

    ff1_cache = Path(output_path).parent / "cache"
    ff1_cache.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(ff1_cache))

    progress_cb(f"Loading {year} {grand_prix} {session_type} …")
    session = fastf1.get_session(year, grand_prix, session_type)
    session.load(telemetry=True, weather=False, messages=False)

    driver_data: dict = {}
    track_points: list = []

    # ── Load pre-built track layout if available ──────────────────────────────
    # build_tracks.py pre-generates layouts for all 24 circuits keyed by
    # slug(EventName), avoiding broken telemetry-derived outlines entirely.
    def _slug(s: str) -> str:
        return s.lower().replace(" ", "_").replace("/", "_").replace("'", "")

    layouts_file = Path(output_path).parent / "track_layouts.json"
    if layouts_file.exists():
        try:
            with open(layouts_file, encoding="utf-8") as _lf:
                _layouts = json.load(_lf)
            _key = _slug(grand_prix)
            if _key in _layouts:
                track_points = _layouts[_key]
                progress_cb(f"  Track layout: {len(track_points)} pts (pre-built)")
        except Exception:
            pass
    # ─────────────────────────────────────────────────────────────────────────

    for driver_num in session.drivers:
        try:
            drv = session.get_driver(driver_num)
            laps = session.laps.pick_driver(driver_num)
            if laps.empty:
                continue

            # Combined telemetry across all laps
            tel = laps.get_telemetry()
            if tel.empty or "X" not in tel.columns:
                continue

            tel = tel.dropna(subset=["X", "Y", "SessionTime"])
            if tel.empty:
                continue

            # Build track outline from the single fastest clean lap's raw GPS trace.
            # One lap sorted by Distance gives the actual circuit shape.
            # Averaging across multiple laps distorts the track because cars take
            # different lines — the averaged X,Y at each distance bin is off-circuit.
            if not track_points:
                try:
                    clean = laps.dropna(subset=["LapTime"])
                    if not clean.empty:
                        min_lt = clean["LapTime"].min()
                        # Use laps within 107% of fastest to exclude SC/formation laps
                        clean = clean[clean["LapTime"] <= min_lt * 1.07]
                    # Pick the fastest lap
                    ref_lap = (
                        clean.loc[clean["LapTime"].idxmin()]
                        if not clean.empty else laps.iloc[1]
                    )
                    lap_tel = ref_lap.get_telemetry()
                    lap_tel = lap_tel.dropna(subset=["X", "Y", "Distance"])
                    lap_tel = lap_tel.sort_values("Distance").reset_index(drop=True)
                    if not lap_tel.empty:
                        # Downsample to ~800 pts max; keep every point on short laps
                        step = max(1, len(lap_tel) // 800)
                        track_points = [
                            [round(float(r.X), 1), round(float(r.Y), 1)]
                            for _, r in lap_tel.iloc[::step].iterrows()
                        ]
                        # Trim first/last ~2% to avoid start/finish GPS scatter
                        trim = max(1, len(track_points) // 50)
                        track_points = track_points[trim:-trim]
                except Exception:
                    pass

            # Downsample to ~2500 points per driver
            step = max(1, len(tel) // 2500)
            tel_s = tel.iloc[::step]

            # ── Lap start times ────────────────────────────────────────────
            lap_starts = []
            for _, lap in laps.iterrows():
                t = safe_seconds(lap.get("LapStartTime"))
                if t is not None:
                    lap_starts.append(round(t, 2))

            # ── Tyre compounds (one entry per lap where compound is known) ─
            tyres = []
            for _, lap in laps.iterrows():
                t = safe_seconds(lap.get("LapStartTime"))
                if t is None:
                    continue
                compound  = safe_str(lap.get("Compound"), "UNKNOWN")
                raw_life  = lap.get("TyreLife")
                try:
                    tyre_life = int(float(raw_life)) if raw_life is not None else 0
                except (ValueError, TypeError):
                    tyre_life = 0
                tyres.append([round(t, 2), compound, tyre_life])

            # ── Pit stops ──────────────────────────────────────────────────
            # FastF1 layout: PitInTime on the inlap, PitOutTime on the outlap.
            # Build a lookup of session-time → PitOutTime from out-laps.
            pits = []
            lap_list = list(laps.iterrows())
            for idx, (_, lap) in enumerate(lap_list):
                pi = safe_seconds(lap.get("PitInTime"))
                if pi is None:
                    continue
                # PitOutTime lives on the NEXT lap row (the out-lap)
                po = None
                if idx + 1 < len(lap_list):
                    po = safe_seconds(lap_list[idx + 1][1].get("PitOutTime"))
                if po is None:
                    po = pi + 28
                pits.append([round(pi, 2), round(po, 2)])

            # ── Telemetry positions ────────────────────────────────────────
            positions = []
            for _, row in tel_s.iterrows():
                try:
                    positions.append([
                        round(row["SessionTime"].total_seconds(), 2),
                        round(float(row["X"]), 1),
                        round(float(row["Y"]), 1),
                        round(float(row.get("Speed", 0) or 0), 1),
                        round(float(row.get("Distance", 0) or 0), 1),
                    ])
                except Exception:
                    pass

            if not positions:
                continue

            name = drv.get("Abbreviation", str(driver_num))
            driver_data[driver_num] = {
                "name":      name,
                "team":      drv.get("TeamName", ""),
                "color":     hex_color(drv.get("TeamColor")),
                "laps":      sorted(lap_starts),
                "tyres":     tyres,
                "pits":      pits,
                "positions": positions,
            }
            progress_cb(f"  {name}: {len(positions)} pts, {len(pits)} pit(s)")

        except Exception as exc:
            progress_cb(f"  Skipping {driver_num}: {exc}")

    if not driver_data:
        progress_cb("No driver data found — nothing exported.")
        return

    all_t = [p[0] for d in driver_data.values() for p in d["positions"]]

    # ── Track status (Safety Car / VSC / Yellow / Red) ────────────────────
    track_status_list = []
    try:
        ts_data = session.track_status
        if ts_data is not None and not ts_data.empty:
            for _, row in ts_data.iterrows():
                t = safe_seconds(row.get("Time"))
                if t is None:
                    continue
                status = str(row.get("Status", "")).strip()
                msg    = str(row.get("Message", "")).strip()
                track_status_list.append([round(t, 2), status, msg])
    except Exception:
        pass

    # ── Total laps for the session ────────────────────────────────────────
    total_laps = 0
    try:
        all_laps = session.laps
        for num in session.drivers:
            laps_drv = all_laps.pick_driver(num)
            n = int(laps_drv["LapNumber"].max()) if not laps_drv.empty else 0
            if n > total_laps:
                total_laps = n
    except Exception:
        pass

    output = {
        "grand_prix":    grand_prix,
        "year":          year,
        "session":       session_type,
        "track":         track_points,
        "drivers":       driver_data,
        "t_start":       round(min(all_t), 2),
        "t_end":         round(max(all_t), 2),
        "track_status":  track_status_list,
        "total_laps":    total_laps,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        json.dump(output, fh, separators=(",", ":"))

    size_mb = Path(output_path).stat().st_size / 1e6
    progress_cb(f"\nExported {len(driver_data)} drivers → {Path(output_path).name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    _year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    _gp   = sys.argv[2]     if len(sys.argv) > 2 else "Bahrain"
    _sess = sys.argv[3]     if len(sys.argv) > 3 else "R"
    fetch_race_data(_year, _gp, _sess)
