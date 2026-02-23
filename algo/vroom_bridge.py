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
import os
import base64
from io import BytesIO

try:
    import vroom
    import pandas as pd
except ImportError as e:
    print(json.dumps({"error": f"Missing dependency: {e}"}), file=sys.stderr)
    sys.exit(1)


# ─── Helpers ────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_time_to_seconds(t_val):
    if pd.isna(t_val) or t_val == "":
        return 0
    if hasattr(t_val, 'hour'):
        return (t_val.hour * 60 + t_val.minute) * 60
    s = str(t_val).strip()
    try:
        if " " in s:
            s = s.split(" ")[-1]
        parts = s.split(":")
        return (int(parts[0]) * 60 + int(parts[1])) * 60
    except:
        return 0


# ─── Core solve ─────────────────────────────────────────────────────────────

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

    xls       = pd.ExcelFile(BytesIO(file_bytes))
    df_empl   = pd.read_excel(xls, "employees")
    df_vehi   = pd.read_excel(xls, "vehicles")
    df_meta   = pd.read_excel(xls, "metadata")

    # Priority delays (seconds)
    priority_delays = {}
    for _, row in df_meta.iterrows():
        key = str(row["key"])
        if key.startswith("priority_") and key.endswith("_max_delay_min"):
            try:
                level = int(key.split("_")[1])
                priority_delays[level] = int(float(row["value"]) * 60)
            except:
                pass

    # Build unified location index
    all_locations = []
    def get_loc_idx(lat, lon):
        loc = (float(lat), float(lon))
        if loc not in all_locations:
            all_locations.append(loc)
        return all_locations.index(loc)

    for _, v in df_vehi.iterrows():
        get_loc_idx(v["current_lat"], v["current_lng"])
    for _, e in df_empl.iterrows():
        get_loc_idx(e["pickup_lat"], e["pickup_lng"])
        get_loc_idx(e["drop_lat"],   e["drop_lng"])

    # Distance matrix (km)
    dist_km = [
        [haversine(l1[0], l1[1], l2[0], l2[1]) for l2 in all_locations]
        for l1 in all_locations
    ]

    problem = vroom.Input(amount_size=1)

    veh_int_to_str = {}
    veh_cap        = {}

    for i, v in df_vehi.iterrows():
        v_int  = i + 1
        v_str  = str(v["vehicle_id"])
        veh_int_to_str[v_int] = v_str
        cap                   = int(v["capacity"])
        veh_cap[v_int]        = cap
        speed_kps             = float(v["avg_speed_kmph"]) / 3600.0
        cpk                   = float(v["cost_per_km"])
        profile               = f"veh_{v_str}"

        dur_mat  = [
            [int(math.ceil(d / speed_kps)) if speed_kps > 0 else 0 for d in row]
            for row in dist_km
        ]
        cost_mat = [
            [int(100 * (W1 * cpk * d +
                        W2 * (d / speed_kps / 60.0 if speed_kps > 0 else 0)))
             for d in row]
            for row in dist_km
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

    job_int_to_str = {}
    for i, e in df_empl.iterrows():
        j_int = i + 1
        job_int_to_str[j_int] = str(e["employee_id"])
        p_idx = get_loc_idx(e["pickup_lat"], e["pickup_lng"])
        d_idx = get_loc_idx(e["drop_lat"],   e["drop_lng"])
        t_pu  = int(parse_time_to_seconds(e["earliest_pickup"]))
        t_dr  = int(parse_time_to_seconds(e["latest_drop"]))
        buf   = priority_delays.get(int(e["priority"]), 0)

        problem.add_shipment(
            pickup=vroom.ShipmentStep(
                id=j_int * 10, location=p_idx,
                time_windows=[[t_pu, 86400]]),
            delivery=vroom.ShipmentStep(
                id=j_int * 10 + 1, location=d_idx,
                time_windows=[[0, t_dr + buf]]),
            amount=vroom.Amount([1]),
            priority=100,
        )

    sol = problem.solve(exploration_level=5, nb_threads=4)

    # ── Format output as route_sequence for solver.py ────────────────────────
    def fmt_seconds(secs):
        h = (secs // 3600) % 24
        m = (secs % 3600) // 60
        return f"{h:02d}:{m:02d}"

    output_vehicles = []
    total_cost      = 0.0

    if not sol.routes.empty:
        for v_id, grp in sol.routes.groupby("vehicle_id"):
            v_str = veh_int_to_str.get(v_id, str(v_id))
            grp   = grp.sort_values("arrival")

            # Build route_sequence
            raw_seq = [{"location": v_str,
                        "arrival_time":   "00:00",
                        "departure_time": "00:00"}]

            for _, row in grp.iterrows():
                if row["type"] == "pickup":
                    eid = job_int_to_str.get(int(row["id"]) // 10, "?")
                    raw_seq.append({
                        "location":       eid,
                        "arrival_time":   fmt_seconds(int(row["arrival"])),
                        "departure_time": fmt_seconds(int(row["departure"])),
                    })
                elif row["type"] == "delivery":
                    raw_seq.append({
                        "location":       "office",
                        "arrival_time":   fmt_seconds(int(row["arrival"])),
                        "departure_time": fmt_seconds(int(row["departure"])),
                    })

            # Merge consecutive identical locations
            merged = []
            for s in raw_seq:
                if not merged or s["location"] != merged[-1]["location"]:
                    merged.append(dict(s))
                else:
                    merged[-1]["departure_time"] = s["departure_time"]

            final_seq = []
            for i, s in enumerate(merged):
                fs = {"step": i, "location": s["location"],
                      "arrival_time": s["arrival_time"]}
                if i < len(merged) - 1:
                    fs["departure_time"] = s["departure_time"]
                final_seq.append(fs)

            links = [f"{final_seq[i]['location']}_{final_seq[i+1]['location']}"
                     for i in range(len(final_seq) - 1)]

            # Estimate cost (W1 * km * cost_per_km) — approximate
            output_vehicles.append({
                "vehicle_id":        v_str,
                "vehicle_type":      "standard",
                "capacity":          veh_cap.get(v_id, 0),
                "avg_speed_kmph":    30.0,
                "total_cost":        0.0,   # caller can re-score
                "total_time_minutes": 0.0,
                "total_steps":       len(final_seq),
                "routes":            links,
                "route_sequence":    final_seq,
            })

    # Unassigned
    unassigned = set()
    if sol.unassigned:
        for job in sol.unassigned:
            eid = job_int_to_str.get(int(job._id) // 10)
            if eid:
                unassigned.add(eid)

    return {
        "vehicles": output_vehicles,
        "unassigned": sorted(unassigned),
        "summary": {"total_cost_all_vehicles": total_cost},
    }


# ─── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read())
        result  = solve(payload)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)
