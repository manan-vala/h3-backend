"""
debug_alns.py  –  Diagnose why ALNS returns 0 served employees.
Run from h3-backend/:  python debug_alns.py
"""
import sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "algo"))

import importlib.util, types

# ── Patch 16-02.py so relative imports work ──────────────────────────────────
spec = importlib.util.spec_from_file_location(
    "alns_solver_16_02",
    os.path.join(HERE, "algo", "16-02.py")
)
alns_mod = importlib.util.module_from_spec(spec)
alns_mod.__package__ = "algo"
sys.modules["alns_solver_16_02"] = sys.modules.get("algo.alns_solver_16_02", alns_mod)
spec.loader.exec_module(alns_mod)

from algo.lns_utils import DistanceMatrix, time_to_minutes
from io import BytesIO
import pandas as pd

with open(os.path.join(HERE, "algo", "templts", "matrix_edge_list.json")) as f:
    matrix_edge_list = json.load(f)
with open(os.path.join(HERE, "algo", "templts", "payload_dict.json")) as f:
    input_data = json.load(f)
with open(os.path.join(HERE, "algo", "templts", "test_input.xlsx"), "rb") as f:
    file_bytes = f.read()

dm = DistanceMatrix(matrix_edge_list)
alns_mod.DistanceProvider.set_matrix(dm)

excel_file = BytesIO(file_bytes)
emp_df  = pd.read_excel(excel_file, sheet_name="employees")
veh_df  = pd.read_excel(excel_file, sheet_name="vehicles")
meta_df = pd.read_excel(excel_file, sheet_name="metadata")
meta    = dict(zip(meta_df["key"], meta_df["value"]))

print("=== meta ===")
print(meta)

delays = {i: float(meta.get(f"priority_{i}_max_delay_min", 0)) / (24*60) for i in range(1, 6)}
print("\n=== delays (fractional-day) ===", delays)

Wc = float(meta.get("objective_cost_weight", 0.6))
Wt = float(meta.get("objective_time_weight", 0.4))

requests = {}
for _, row in emp_df.iterrows():
    rid = str(row["employee_id"])
    ep  = time_to_minutes(row["earliest_pickup"]) / 1440.0
    ld  = time_to_minutes(row["latest_drop"])     / 1440.0
    md  = delays[int(row["priority"])]
    requests[rid] = alns_mod.Request(
        id=rid, priority=int(row["priority"]),
        pickup_id=rid, drop_id="office",
        earliest_pickup=ep, latest_drop=ld, max_delay=md,
        vehicle_pref=str(row["vehicle_preference"]).lower(),
        max_share={"single":1,"double":2,"triple":3}.get(
            str(row["sharing_preference"]).lower(), 999)
    )

vehicles = {}
for _, row in veh_df.iterrows():
    vid = str(row["vehicle_id"])
    vehicles[vid] = alns_mod.Vehicle(
        id=vid, capacity=int(row["capacity"]),
        cost_per_km=float(row["cost_per_km"]),
        speed_kmph=float(row["avg_speed_kmph"]),
        start_id=vid,
        available_from=time_to_minutes(row["available_from"]) / 1440.0,
        category=str(row["category"]).lower(),
        fuel_type=str(row["fuel_type"]),
        vehicle_type=str(row["vehicle_type"])
    )

print("\n=== Requests (first 3) ===")
for k, r in list(requests.items())[:3]:
    print(f"  {k}: ep={r.earliest_pickup*1440:.1f}min, ld={r.latest_drop*1440:.1f}min, "
          f"max_delay={r.max_delay*1440:.1f}min, latest_drop_hard={r.latest_drop_hard*1440:.1f}min")

print("\n=== Vehicles (first 3) ===")
for k, v in list(vehicles.items())[:3]:
    print(f"  {k}: avail={v.available_from*1440:.1f}min, cap={v.capacity}")

# Test a single insertion: V01 picks up E01, drops at office
print("\n=== Single insertion test: V01 picks up E01 ===")
v = vehicles["V01"]
r = requests["E01"]
route = alns_mod.OptimizedRoute(v, requests)

solver_data = {
    "requests": requests, "requests_list": list(requests.values()),
    "vehicles": vehicles, "vehicles_list": list(vehicles.values()),
    "Wc": Wc, "Wt": Wt
}
solver = alns_mod.EnhancedALNSSolver(solver_data)

# Try inserting E01 into empty V01 route at positions (0, 1)
result = solver._insert_request(route, r, 0, 1)
if result is None:
    print("  _insert_request returned None! Checking why...")
    # Manual trace
    curr_time   = v.available_from
    prev_loc_id = v.start_id  # "V01"
    
    # Pickup at i=0
    d_km, dur_min = dm.get_dist_dur(prev_loc_id, r.pickup_id)
    arrival_pu = curr_time + dur_min / 60.0 / 24.0
    departure_pu = max(arrival_pu, r.earliest_pickup)
    load_after_pu = 1
    print(f"  V01->E01: dur={dur_min:.2f}min, arrival={arrival_pu*1440:.1f}min, "
          f"earliest={r.earliest_pickup*1440:.1f}min, departure={departure_pu*1440:.1f}min")
    print(f"  latest_drop_hard = {r.latest_drop_hard*1440:.1f}min")
    
    # Drop at i=1
    d_km2, dur_min2 = dm.get_dist_dur(r.pickup_id, "office")
    arrival_dr = departure_pu + dur_min2 / 60.0 / 24.0
    print(f"  E01->office: dur={dur_min2:.2f}min, arrival={arrival_dr*1440:.1f}min")
    print(f"  Drop feasible? (arrival <= latest_drop_hard): {arrival_dr <= r.latest_drop_hard + 1e-9}")
    print(f"  Cap check: load=1 <= cap={v.capacity}? {1 <= v.capacity}")
else:
    print(f"  SUCCESS: {len(result.legs)} legs, request_set={result.request_set}")
    valid, msg = solver._validate_route(result)
    print(f"  Validate: {valid} | {msg}")

print("\n=== Running full ALNS (5s time limit for debug) ===")
alns_mod.config.ALNS_TIME_LIMIT = 5
solver2 = alns_mod.EnhancedALNSSolver(solver_data)
best = solver2.solve(time_limit=5)
served = best.get_served_count()
unassigned = len(best.unassigned)
print(f"  Served: {served}, Unassigned: {unassigned}")
for vid, route in best.routes.items():
    if route.legs:
        print(f"  {vid}: {len(route.legs)} legs, passengers={route.request_set}")
