"""
vroom_bridge.py  –  Standalone VROOM solver for Python 3.9/3.10
================================================================
This file is designed to run in a SEPARATE Python 3.9 or 3.10 virtual
environment where pyvroom installs and works correctly.

It reads a JSON payload from STDIN and writes results to STDOUT.
The main 3.11/3.12 code calls this via subprocess.

Standalone env setup (one-time):
    py -3.10 -m venv vroom_env
    vroom_env\\Scripts\\pip install pyvroom pandas openpyxl

Then set VROOM_PYTHON in vroom_solver.py to:
    "vroom_env\\Scripts\\python.exe"
"""

import sys
import json
import math
import base64
from io import BytesIO

try:
    import vroom_matrix_patch
    import vroom
    import pandas as pd
except ImportError as e:
    print(json.dumps({"error": f"Missing dependency: {e}"}), file=sys.stderr)
    sys.exit(1)


# ─── Constants ───────────────────────────────────────────────────────────────

# Road factor: real road distance is ~30% longer than straight-line haversine.
# Must match the same factor used in lns_utils.py so that feasibility scoring
# (get_feasibility_score) evaluates all solvers on a consistent distance basis.
ROAD_FACTOR = 1.3


# ─── Helpers ─────────────────────────────────────────────────────────────────

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_time_to_seconds(t_val) -> int:
    """
    Convert a variety of time representations to integer seconds since midnight.

    Handles:
      - datetime.time / datetime.datetime objects
      - Strings in "HH:MM" or "HH:MM:SS" format, optionally prefixed with a date
      - Excel time fractions (float in [0.0, 1.0) where 0.375 == 09:00)
      - NaN / empty → returns 0
    """
    if pd.isna(t_val) or t_val == "":
        return 0
    if hasattr(t_val, 'hour'):
        return (t_val.hour * 60 + t_val.minute) * 60
    # Excel stores times as a fraction of a 24-hour day (0.0–<1.0)
    if isinstance(t_val, float) and 0.0 <= t_val < 1.0:
        return round(t_val * 86400)
    s = str(t_val).strip()
    try:
        # Handle "YYYY-MM-DD HH:MM:SS" — take only the time part
        if " " in s:
            s = s.split(" ")[-1]
        parts = s.split(":")
        return (int(parts[0]) * 60 + int(parts[1])) * 60
    except Exception:
        return 0


# ─── Core solve ──────────────────────────────────────────────────────────────

def solve(payload: dict) -> dict:
    """
    payload keys:
        file_b64   : base64-encoded Excel bytes
        W1_COST    : float (default 0.7)
        W2_TIME    : float (default 0.3)
    """
    file_bytes = base64.b64decode(payload["file_b64"])
    W1 = float(payload.get("W1_COST", 0.7))
    W2 = float(payload.get("W2_TIME", 0.3))

    xls      = pd.ExcelFile(BytesIO(file_bytes), engine='openpyxl')
    df_empl  = pd.read_excel(xls, "employees")
    df_vehi  = pd.read_excel(xls, "vehicles")
    df_meta  = pd.read_excel(xls, "metadata")

    # Ensure DataFrames have a clean 0-based RangeIndex so that iloc[v_id-1]
    # always corresponds correctly to the vehicle registered with id=i+1.
    df_vehi  = df_vehi.reset_index(drop=True)
    df_empl  = df_empl.reset_index(drop=True)

    # Priority delays (seconds)
    priority_delays: dict[int, int] = {}
    for _, row in df_meta.iterrows():
        key = str(row["key"])
        if key.startswith("priority_") and key.endswith("_max_delay_min"):
            try:
                level = int(key.split("_")[1])
                priority_delays[level] = int(float(row["value"]) * 60)
            except Exception:
                pass

    # ── Build unified location index (O(1) lookups via dict) ─────────────────
    all_locations: list[tuple[float, float]] = []
    _loc_index: dict[tuple[float, float], int] = {}

    def get_loc_idx(lat, lon) -> int:
        loc = (float(lat), float(lon))
        if loc not in _loc_index:
            _loc_index[loc] = len(all_locations)
            all_locations.append(loc)
        return _loc_index[loc]

    # Also build a mapping from string IDs (vehicle_id, employee_id, "office")
    # to location indices so we can look up OSRM matrix entries.
    _id_to_loc_idx: dict[str, int] = {}

    for _, v in df_vehi.iterrows():
        idx = get_loc_idx(v["current_lat"], v["current_lng"])
        _id_to_loc_idx[str(v["vehicle_id"])] = idx
    for _, e in df_empl.iterrows():
        idx_pickup = get_loc_idx(e["pickup_lat"], e["pickup_lng"])
        idx_drop   = get_loc_idx(e["drop_lat"],   e["drop_lng"])
        _id_to_loc_idx[str(e["employee_id"])] = idx_pickup
        # "office" — all employees share the same drop location; first write wins,
        # subsequent writes are the same index (same coords).
        _id_to_loc_idx["office"] = idx_drop

    # ── Build OSRM lookup: (loc_idx_from, loc_idx_to) → (km, seconds) ───────
    matrix_edge_list = payload.get("matrix_edge_list") or []
    _osrm_lookup: dict[tuple[int, int], tuple[float, float]] = {}

    for entry in matrix_edge_list:
        eid = entry.get("id", "")
        parts = eid.split("_")
        # Parse "{from}_{to}" — must match logic.py's naming convention.
        # Handle "office" as a special keyword that can appear as prefix or suffix.
        from_id = to_id = None
        if len(parts) == 2:
            from_id, to_id = parts[0], parts[1]
        elif "office" in eid:
            if eid.startswith("office_"):
                from_id, to_id = "office", eid.replace("office_", "", 1)
            elif eid.endswith("_office"):
                from_id, to_id = eid.rsplit("_office", 1)[0], "office"
        if from_id is None:
            continue
        idx_from = _id_to_loc_idx.get(from_id)
        idx_to   = _id_to_loc_idx.get(to_id)
        if idx_from is not None and idx_to is not None:
            km  = entry["distance_meters"] / 1000.0
            sec = entry["duration_seconds"]
            _osrm_lookup[(idx_from, idx_to)] = (km, sec)

    osrm_hit = len(_osrm_lookup)
    n_locs   = len(all_locations)

    # ── Build NxN distance (km) and duration (seconds) matrices ──────────────
    # Use OSRM data when available, fall back to haversine × ROAD_FACTOR.
    dist_km  = [[0.0] * n_locs for _ in range(n_locs)]
    dur_sec  = [[0.0] * n_locs for _ in range(n_locs)]

    OSRM_REFERENCE_SPEED_KMPH = 30.0  # assumed speed behind OSRM durations

    for i in range(n_locs):
        for j in range(n_locs):
            if i == j:
                continue
            osrm = _osrm_lookup.get((i, j))
            if osrm is not None:
                dist_km[i][j] = osrm[0]
                dur_sec[i][j] = osrm[1]
            else:
                # Haversine fallback with road factor
                hav_km = haversine(all_locations[i][0], all_locations[i][1],
                                   all_locations[j][0], all_locations[j][1]) * ROAD_FACTOR
                dist_km[i][j] = hav_km
                dur_sec[i][j] = (hav_km / OSRM_REFERENCE_SPEED_KMPH) * 3600.0

    _pct = (osrm_hit / max(n_locs * (n_locs - 1), 1)) * 100
    print(f"[VROOM] Matrix: {n_locs} locations, {osrm_hit} OSRM pairs "
          f"({_pct:.0f}% coverage), rest haversine fallback", file=sys.stderr)

    problem = vroom.Input(amount_size=1)

    veh_int_to_str: dict[int, str] = {}
    veh_cap: dict[int, int] = {}

    for i, v in df_vehi.iterrows():
        v_int    = i + 1                          # 1-based VROOM vehicle id
        v_str    = str(v["vehicle_id"])
        veh_int_to_str[v_int] = v_str
        cap      = int(v["capacity"])
        veh_cap[v_int] = cap
        speed_kmph = float(v["avg_speed_kmph"])
        cpk        = float(v["cost_per_km"])
        profile    = f"veh_{v_str}"

        # Scale OSRM durations by vehicle speed ratio.
        # OSRM durations reflect real road speed limits (~30-50 km/h urban).
        # If this vehicle is faster/slower, scale proportionally.
        speed_ratio = OSRM_REFERENCE_SPEED_KMPH / speed_kmph if speed_kmph > 0 else 1.0

        dur_mat = [
            [max(0, int(math.ceil(dur_sec[r][c] * speed_ratio)))
             for c in range(n_locs)]
            for r in range(n_locs)
        ]
        cost_mat = [
            [int(100 * (W1 * cpk * dist_km[r][c] +
                        W2 * (dur_sec[r][c] * speed_ratio / 60.0)))
             for c in range(n_locs)]
            for r in range(n_locs)
        ]

        problem.set_durations_matrix(profile=profile, matrix_input=dur_mat)
        problem.set_costs_matrix(profile=profile, matrix_input=cost_mat)

        avail_s = parse_time_to_seconds(v.get("available_from", "00:00"))
        problem.add_vehicle([vroom.Vehicle(
            id=v_int,
            profile=profile,
            start=get_loc_idx(v["current_lat"], v["current_lng"]),
            capacity=vroom.Amount([cap]),
            time_window=[avail_s, 86400],
        )])

    # Maps from VROOM integer job id back to employee string id and location indices.
    # Stored upfront so that output formatting doesn't need to re-scan df_empl.
    job_int_to_str: dict[int, str] = {}
    job_int_to_pickup_idx: dict[int, int] = {}
    job_int_to_drop_idx: dict[int, int] = {}

    for i, e in df_empl.iterrows():
        j_int = i + 1                             # 1-based VROOM job id
        job_int_to_str[j_int]        = str(e["employee_id"])
        job_int_to_pickup_idx[j_int] = get_loc_idx(e["pickup_lat"], e["pickup_lng"])
        job_int_to_drop_idx[j_int]   = get_loc_idx(e["drop_lat"],   e["drop_lng"])

        t_pu = int(parse_time_to_seconds(e["earliest_pickup"]))
        t_dr = int(parse_time_to_seconds(e["latest_drop"]))
        buf  = priority_delays.get(int(e["priority"]), 0)

        problem.add_shipment(
            pickup=vroom.ShipmentStep(
                id=j_int * 10, location=job_int_to_pickup_idx[j_int],
                time_windows=[[t_pu, 86400]]),
            delivery=vroom.ShipmentStep(
                id=j_int * 10 + 1, location=job_int_to_drop_idx[j_int],
                time_windows=[[0, t_dr + buf]]),
            amount=vroom.Amount([1]),
            priority=100,
        )

    sol = problem.solve(exploration_level=5, nb_threads=2)

    # ── Format output as route_sequence for solver.py ─────────────────────────
    def fmt_seconds(secs: int) -> str:
        h = (secs // 3600) % 24
        m = (secs % 3600) // 60
        return f"{h:02d}:{m:02d}"

    output_vehicles = []
    total_cost      = 0.0
    total_time_sec  = 0.0

    if not sol.routes.empty:
        for v_id, grp in sol.routes.groupby("vehicle_id"):
            v_str  = veh_int_to_str.get(v_id, str(v_id))
            v_row  = df_vehi.iloc[v_id - 1]          # safe: df_vehi reset_index above
            v_cpk  = float(v_row["cost_per_km"])
            v_cat  = str(v_row.get("category", "standard")).lower()

            grp = grp.sort_values("arrival")

            # ── Build raw_seq with loc_idx at every step ──────────────────────
            # raw_seq is the authoritative source for distance calculation.
            # It preserves every physical stop (including multiple "office" drops
            # at different coordinates) without merging anything.
            start_idx = get_loc_idx(v_row["current_lat"], v_row["current_lng"])
            avail_s   = parse_time_to_seconds(v_row.get("available_from", "00:00"))
            raw_seq = [{
                "location":       v_str,
                "arrival_time":   fmt_seconds(avail_s),
                "departure_time": fmt_seconds(avail_s),
                "loc_idx":        start_idx,
            }]

            for _, row in grp.iterrows():
                row_type = row.get("type", "")
                if row_type not in ("pickup", "delivery"):
                    continue   # skip 'start' / 'end' meta-rows

                arr = int(row["arrival"])
                dep = int(row.get("departure", arr))
                job_id = int(row["id"]) // 10       # decode: j*10 → j, j*10+1 → j

                if row_type == "pickup":
                    raw_seq.append({
                        "location":       job_int_to_str.get(job_id, "?"),
                        "arrival_time":   fmt_seconds(arr),
                        "departure_time": fmt_seconds(dep),
                        "loc_idx":        job_int_to_pickup_idx.get(job_id),
                    })
                else:  # delivery — use the actual drop location, NOT the vehicle depot
                    raw_seq.append({
                        "location":       "office",
                        "arrival_time":   fmt_seconds(arr),
                        "departure_time": fmt_seconds(dep),
                        "loc_idx":        job_int_to_drop_idx.get(job_id),
                    })

            # ── Merge consecutive stops at the same name AND same location ────
            # Checking loc_idx prevents incorrectly merging two "office" drops
            # that are physically at different coordinates.
            merged = []
            for s in raw_seq:
                same_name = merged and s["location"] == merged[-1]["location"]
                same_loc  = merged and s.get("loc_idx") == merged[-1].get("loc_idx")
                if same_name and same_loc:
                    merged[-1]["departure_time"] = s["departure_time"]
                else:
                    merged.append(dict(s))

            # ── Build final_seq (strip loc_idx, add step numbers) ─────────────
            final_seq = []
            for idx, s in enumerate(merged):
                fs = {
                    "step":         idx,
                    "location":     s["location"],
                    "arrival_time": s["arrival_time"],
                }
                if idx < len(merged) - 1:
                    fs["departure_time"] = s["departure_time"]
                final_seq.append(fs)

            links = [
                f"{final_seq[i]['location']}_{final_seq[i+1]['location']}"
                for i in range(len(final_seq) - 1)
            ]

            # ── Route time (from first pickup to last delivery) ───────────────
            pickup_rows  = grp[grp["type"] == "pickup"]
            delivery_rows = grp[grp["type"] == "delivery"]

            if not pickup_rows.empty and not delivery_rows.empty:
                route_start    = int(pickup_rows.iloc[0]["arrival"])
                last_del       = delivery_rows.iloc[-1]
                route_end      = int(last_del.get("departure", last_del["arrival"]))
                route_time_sec = max(0, route_end - route_start)
            else:
                route_time_sec = 0

            # ── Distance: walk raw_seq pairs using stored loc_idx ─────────────
            route_distance_km = 0.0
            for a, b in zip(raw_seq, raw_seq[1:]):
                ai, bi = a.get("loc_idx"), b.get("loc_idx")
                if ai is not None and bi is not None:
                    route_distance_km += dist_km[ai][bi]

            route_cost = W1 * v_cpk * route_distance_km + W2 * (route_time_sec / 60.0)
            total_cost    += route_cost
            total_time_sec += route_time_sec

            output_vehicles.append({
                "vehicle_id":         v_str,
                "vehicle_type":       v_cat,          # actual category, not hardcoded
                "capacity":           veh_cap.get(v_id, 0),
                "avg_speed_kmph":     float(v_row["avg_speed_kmph"]),
                "total_cost":         round(route_cost, 2),
                "total_time_minutes": round(route_time_sec / 60.0, 2),
                "total_steps":        len(final_seq),
                "routes":             links,
                "route_sequence":     final_seq,
            })

    # ── Unassigned employees ──────────────────────────────────────────────────
    # VROOM marks both the pickup step (id = j*10) and delivery step (id = j*10+1)
    # as unassigned. Both decode to the same j via // 10, and the set deduplicates.
    unassigned: set[str] = set()
    if sol.unassigned:
        for job in sol.unassigned:
            eid = job_int_to_str.get(int(job._id) // 10)
            if eid:
                unassigned.add(eid)

    return {
        "routes":      output_vehicles,
        "vehicles":    output_vehicles,   # backward compatibility with solver.py
        "unassigned":  sorted(unassigned),
        "summary": {
            "cost":                      round(total_cost, 2),
            "total_cost_all_vehicles":   round(total_cost, 2),
            "time_minutes":              round(total_time_sec / 60.0, 2),
        },
    }


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read())
        result  = solve(payload)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)