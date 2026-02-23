"""
make_test_excel.py  –  Converts algo/templts/payload_dict.json into a
                        proper test.xlsx with employees, vehicles, metadata sheets.
Run once:  python make_test_excel.py
"""
import json, os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
payload_path = os.path.join(HERE, "algo", "templts", "payload_dict.json")
out_path = os.path.join(HERE, "algo", "templts", "test_input.xlsx")

with open(payload_path) as f:
    data = json.load(f)

emp_df = pd.DataFrame(data["employees"])
veh_df = pd.DataFrame(data["vehicles"])

# Add required columns that load_data_from_bytes expects
# employees need: employee_id, pickup_lat, pickup_lng, drop_lat, drop_lng,
#                 priority, earliest_pickup, latest_drop, vehicle_preference, sharing_preference
# vehicles need:  vehicle_id, capacity, avg_speed_kmph, cost_per_km, category,
#                 current_lat, current_lng, available_from

# Rename to match lns_utils.py expectations
if "vehicle_type" not in veh_df.columns:
    veh_df["vehicle_type"] = "4W"
if "fuel_type" not in veh_df.columns:
    veh_df["fuel_type"] = "petrol"

# Metadata sheet
meta_rows = [
    {"key": "objective_cost_weight",      "value": 0.6},
    {"key": "objective_time_weight",      "value": 0.4},
    {"key": "priority_1_max_delay_min",   "value": 15},
    {"key": "priority_2_max_delay_min",   "value": 20},
    {"key": "priority_3_max_delay_min",   "value": 30},
    {"key": "priority_4_max_delay_min",   "value": 45},
    {"key": "priority_5_max_delay_min",   "value": 60},
]
meta_df = pd.DataFrame(meta_rows)

with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
    emp_df.to_excel(writer, sheet_name="employees", index=False)
    veh_df.to_excel(writer, sheet_name="vehicles",  index=False)
    meta_df.to_excel(writer, sheet_name="metadata", index=False)

print(f"Written: {out_path}")
print(f"  employees : {len(emp_df)} rows")
print(f"  vehicles  : {len(veh_df)} rows")
print(f"  metadata  : {len(meta_df)} rows")
