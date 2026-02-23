import json
import sys
import math
import os
import base64
import pandas as pd
from io import BytesIO

try:
    import vroom
except ImportError:
    vroom = None

def parse_time_to_seconds(t_val):
    if pd.isna(t_val) or t_val == "": return 0
    if hasattr(t_val, 'hour'): return (t_val.hour * 60 + t_val.minute) * 60
    s = str(t_val).strip()
    try:
        if " " in s: s = s.split(" ")[-1]
        parts = s.split(":")
        return (int(parts[0]) * 60 + int(parts[1])) * 60
    except: return 0

def format_time_min(seconds):
    minutes = seconds // 60
    h = int(minutes // 60) % 24
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"

def solve_vroom(input_data, matrix_edge_list, file_bytes):
    """
    Called as a function from solver.py (via vroom_solver.py bridge)
    Logic kept intact from vroom_vioorig.py but integrated with matrix_edge_list.
    """
    if vroom is None:
        raise ImportError("vroom module not found. Please ensure you are running in the correct environment.")

    # Original Configuration
    W1_COST = 0.7
    W2_TIME = 0.3

    xls = pd.ExcelFile(BytesIO(file_bytes))
    df_empl = pd.read_excel(xls, 'employees')
    df_vehi = pd.read_excel(xls, 'vehicles')
    df_meta = pd.read_excel(xls, 'metadata')
    
    priority_delays = {} 
    for _, row in df_meta.iterrows():
        key = str(row['key'])
        if key.startswith('priority_') and key.endswith('_max_delay_min'):
            try:
                p_level = int(key.split('_')[1])
                priority_delays[p_level] = int(float(row['value']) * 60)
            except: pass
    
    problem_instance = vroom.Input(amount_size=1)
    
    # ── Original indexing logic ──────────────────────────────────────────────
    # Based on lat, lon as in vroom_vioorig.py
    # But we map them to our matrix-compatible IDs
    all_locations = []
    loc_id_to_idx = {}

    def get_loc_idx(loc_id):
        if loc_id not in all_locations:
            all_locations.append(loc_id)
        return all_locations.index(loc_id)

    # 1. Vehicles
    for _, v in df_vehi.iterrows():
        get_loc_idx(str(v['vehicle_id']).strip())
    # 2. Employees (Pickup & Dropoff)
    # Note: original uses lat/lon. In our matrix, dropoff is usually "office".
    # We'll use the IDs as locations.
    for _, e in df_empl.iterrows():
        get_loc_idx(str(e['employee_id']).strip())
        # Drop location for all employees is "office"
        get_loc_idx("office")

    num_locs = len(all_locations)

    # Map matrix_edge_list to a lookup dict
    dist_map = {} 
    for edge in matrix_edge_list:
        dist_map[edge['id']] = (edge['distance_meters'], edge['duration_seconds'])

    def get_matrix_data(from_id, to_id):
        if from_id == to_id: return 0.0, 0.0
        key = f"{from_id}_{to_id}"
        if key in dist_map:
            return dist_map[key]
        return 10000.0, 1800.0 # Fallback

    veh_int_to_str = {}
    veh_info = {}

    for i, v in df_vehi.iterrows():
        v_int_id = i + 1
        v_id_str = str(v['vehicle_id']).strip()
        veh_int_to_str[v_int_id] = v_id_str
        
        capacity = int(v['capacity'])
        cost_per_km = float(v['cost_per_km'])
        profile_name = f"veh_{v_id_str}"
        
        # Original logic: duration and cost matrix are per vehicle (profile)
        # dur = ceil(dist / speed)
        # cost = 100 * (W1 * cost_per_km * dist + W2 * (dist/speed/60))
        # With matrix: 
        # dist is meters -> km
        # dur is seconds -> minutes
        
        dur_matrix = [[0] * num_locs for _ in range(num_locs)]
        cost_matrix = [[0] * num_locs for _ in range(num_locs)]
        
        for r in range(num_locs):
            for c in range(num_locs):
                d_m, t_s = get_matrix_data(all_locations[r], all_locations[c])
                dist_km = d_m / 1000.0
                dur_min = t_s / 60.0
                
                dur_matrix[r][c] = int(t_s)
                # Matches vroom_vioorig.py formula
                cost_val = int(100 * (W1_COST * cost_per_km * dist_km + W2_TIME * dur_min))
                cost_matrix[r][c] = cost_val
        
        problem_instance.set_durations_matrix(profile=profile_name, matrix_input=dur_matrix)
        problem_instance.set_costs_matrix(profile=profile_name, matrix_input=cost_matrix)

        veh_info[v_id_str] = {
            "capacity": capacity,
            "category": str(v.get('category', 'standard')),
            "speed": float(v.get('avg_speed_kmph', 30.0))
        }

        problem_instance.add_vehicle([vroom.Vehicle(
            id=v_int_id, profile=profile_name,
            start=get_loc_idx(v_id_str),
            capacity=vroom.Amount([capacity]),
            time_window=[parse_time_to_seconds(v.get('available_from', '00:00')), 86400]
        )])
    
    job_int_to_str = {}
    for i, e in df_empl.iterrows():
        j_int_id = i + 1
        eid_str = str(e['employee_id']).strip()
        job_int_to_str[j_int_id] = eid_str
        
        p_idx = get_loc_idx(eid_str)
        d_idx = get_loc_idx("office")
        
        t_pickup_start = int(parse_time_to_seconds(e['earliest_pickup']))
        t_drop_latest = int(parse_time_to_seconds(e['latest_drop']))
        
        p_level = int(e['priority'])
        buffer_s = priority_delays.get(p_level, 0)
        t_drop_latest_relaxed = t_drop_latest + buffer_s
        
        problem_instance.add_shipment(
            pickup=vroom.ShipmentStep(id=j_int_id*10, location=p_idx, time_windows=[[t_pickup_start, 86400]]),
            delivery=vroom.ShipmentStep(id=j_int_id*10+1, location=d_idx, time_windows=[[0, t_drop_latest_relaxed]]),
            amount=vroom.Amount([1]),
            priority=100
        )

    sol = problem_instance.solve(exploration_level=5, nb_threads=4)

    # Format output to match lns_algo
    output_vehicles = []
    total_cost_all = 0.0

    if not sol.routes.empty:
        for v_id, group in sol.routes.groupby('vehicle_id'):
            v_str = veh_int_to_str.get(v_id)
            info = veh_info.get(v_str, {})
            
            group = group.sort_values('arrival')
            
            raw_seq = []
            for _, row in group.iterrows():
                loc_idx = int(row.get('location_index', row.get('location', 0)))
                loc_id = all_locations[loc_idx]
                
                step_data = {
                    "location": loc_id,
                    "arrival_time": int(row['arrival']),
                    "departure_time": int(row.get('departure', row['arrival']))
                }
                
                if row['type'] == 'pickup':
                    eid = job_int_to_str.get(int(row['id']) // 10)
                    step_data["location"] = eid
                elif row['type'] == 'delivery':
                    step_data["location"] = "office"
                elif row['type'] == 'start':
                    step_data["location"] = v_str
                elif row['type'] == 'end':
                    step_data["location"] = v_str
                
                raw_seq.append(step_data)

            # Merge consecutive identical locations
            merged = []
            for s in raw_seq:
                if not merged or s["location"] != merged[-1]["location"]:
                    merged.append(s)
                else:
                    merged[-1]["departure_time"] = s["departure_time"]

            formatted_sequence = []
            route_links = []
            curr_loc = None
            
            for i, s in enumerate(merged):
                fs = {
                    "step": i,
                    "location": s["location"],
                    "arrival_time": format_time_min(s["arrival_time"]),
                    "departure_time": format_time_min(s["departure_time"])
                }
                formatted_sequence.append(fs)
                
                if curr_loc is not None:
                    route_links.append(f"{curr_loc}_{s['location']}")
                curr_loc = s["location"]

            v_total_cost = 0.0
            v_total_time = 0.0
            if len(group) > 0:
                v_total_time = (group['arrival'].iloc[-1] - group['arrival'].iloc[0]) / 60.0

            output_vehicles.append({
                "vehicle_id": v_str,
                "vehicle_type": info.get("category", "standard"),
                "capacity": info.get("capacity", 0),
                "avg_speed_kmph": info.get("speed", 30.0),
                "total_cost": round(v_total_cost, 2),
                "total_time_minutes": round(v_total_time, 2),
                "total_steps": len(formatted_sequence),
                "routes": route_links,
                "route_sequence": formatted_sequence
            })

    unassigned = []
    if sol.unassigned:
        for job in sol.unassigned:
            eid = job_int_to_str.get(int(job._id) // 10)
            if eid and eid not in unassigned:
                unassigned.append(eid)

    if hasattr(sol, 'summary') and hasattr(sol.summary, 'cost'):
        total_cost_all = sol.summary.cost / 100.0

    return {
        "vehicles": output_vehicles,
        "unassigned": sorted(unassigned),
        "summary": {
            "total_cost_all_vehicles": round(total_cost_all, 2)
        }
    }

if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read())
        file_bytes = base64.b64decode(payload["file_b64"])
        matrix_edge_list = payload.get("matrix_edge_list", [])
        input_data = payload.get("input_data", {})
        
        result = solve_vroom(input_data, matrix_edge_list, file_bytes)
        print(json.dumps(result))
    except Exception as exc:
        import traceback
        print(json.dumps({"error": str(exc), "traceback": traceback.format_exc()}), file=sys.stderr)
        sys.exit(1)
