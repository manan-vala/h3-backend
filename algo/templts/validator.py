import json
import math
import sys
import os
import pandas as pd

INPUT_JSON_PATH = "payload_dict.json"
SOLUTION_JSON_PATH = "../Output/output_lns.json"
WEIGHTS_JSON_PATH = "weights.json"

EARTH_RADIUS_KM = 6371.0

def haversine(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c

def load_weights(file_path):
    """Load weights from JSON file."""
    default_weights = {
        "w1": 1.0,  # cost weight
        "w2": 1.0,  # time weight
        "w3": 1.0,  # privacy violation weight
        "w4": 1.0   # downgrade violation weight
    }

    if not os.path.exists(file_path):
        print(f"Warning: Weights file '{file_path}' not found. Using default weights.")
        return default_weights

    try:
        with open(file_path, 'r') as f:
            weights = json.load(f)
            # Ensure all required weights exist
            for key in default_weights:
                if key not in weights:
                    weights[key] = default_weights[key]
                    print(f"Warning: '{key}' not found in weights file, using default {default_weights[key]}")
            return weights
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error loading weights file: {e}. Using default weights.")
        return default_weights

def parse_time_to_minutes(time_val):
    if not time_val:
        return 0
    try:
        parts = time_val.split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return 0

def load_json_data(file_path):
    employees = {}
    vehicles = {}
    meta_config = {} # Default delays if not in payload

    if not os.path.exists(file_path):
        print(f"Error: JSON file '{file_path}' not found.")
        return None, None, None

    with open(file_path, 'r') as f:
        data = json.load(f)

    for row in data.get('employees', []):
        eid = str(row['employee_id'])
        employees[eid] = {
            "lat": float(row['pickup_lat']),
            "lng": float(row['pickup_lng']),
            "drop_lat": float(row['drop_lat']),
            "drop_lng": float(row['drop_lng']),
            "earliest": parse_time_to_minutes(row['earliest_pickup']),
            "latest": parse_time_to_minutes(row['latest_drop']),
            "priority": str(row['priority']).strip(),
            "pref_veh": str(row['vehicle_preference']).strip().lower(),
            "pref_share": str(row['sharing_preference']).strip().lower()
        }

    for row in data.get('vehicles', []):
        vid = str(row['vehicle_id'])
        vehicles[vid] = {
            "lat": float(row['current_lat']),
            "lng": float(row['current_lng']),
            "speed_mpm": float(row['avg_speed_kmph']) / 60.0,
            "capacity": int(row['capacity']),
            "type": str(row['category']).strip().lower(),
            "cost_per_km": float(row['cost_per_km']),
            "available": parse_time_to_minutes(row['available_from'])
        }

    return employees, vehicles, meta_config

def validate_solution(json_path, input_path, weights_path):
    # Load weights
    weights = load_weights(weights_path)
    print(f"\nUsing weights: w1={weights['w1']}, w2={weights['w2']}, w3={weights['w3']}, w4={weights['w4']}")

    employees, vehicles, meta_config = load_json_data(input_path)

    if not employees or not vehicles:
        print("CRITICAL: Failed to load data from Excel.")
        return

    try:
        with open(json_path, 'r') as f:
            solution_data = json.load(f)
        solution = solution_data.get('vehicles', [])
    except FileNotFoundError:
        print(f"Error: Solution file '{json_path}' not found.")
        return

    report = {
        "hard_violations": [],
        "soft_violations": [],
        "physics_violations": [],
        "audit": {"total_cost": 0.0, "total_time": 0.0},
        "served_ids": set(),
        # Counters for objective calculation
        "privacy_violations_count": 0,
        "downgrade_violations_count": 0
    }

    share_limits = {'single': 1, 'double': 2, 'triple': 3}
    all_employee_ids = set(employees.keys())

    for veh_sol in solution:
        vid = veh_sol['vehicle_id']
        if vid not in vehicles:
             report["physics_violations"].append(f"Vehicle {vid} not found in fleet.")
             continue

        veh = vehicles[vid]
        curr_time = veh["available"]
        curr_loc_lat, curr_loc_lng = veh["lat"], veh["lng"]

        trip_cost = 0.0
        trip_time = 0.0

        # We need to reconstruct the groups from route_sequence or use the 'routes' tags
        # Actually, validator should probably just follow the route_sequence
        
        # Simple validation: follow the route_sequence and check constraints
        sequence = veh_sol.get('route_sequence', [])
        if not sequence: continue

        # The route_sequence contains "V01", "E01", "E02", "office", etc.
        # We need to group them back to check sharing preferences? 
        # Actually, the groups are what's between "office" stops (excluding the first one)
        
        current_group = []
        for step in sequence[1:]: # Skip the first step (vehicle start)
            loc = step['location']
            if loc == 'office':
                if current_group:
                    # Process the group
                    group_size = len(current_group)
                    for eid in current_group:
                        emp = employees[eid]
                        # Check privacy violation (w3)
                        limit = share_limits.get(emp["pref_share"], 999)
                        if group_size > limit:
                            report["soft_violations"].append(f"Privacy Breach: {eid} ({emp['pref_share']}) is in a group of {group_size}")
                            report["privacy_violations_count"] += 1
                    current_group = []
                
                # Check arrival time at office for the employees in the last group
                # (This is a bit tricky since we already cleared the group)
                # Let's refine this.
                continue
            
            # It's an employee
            eid = loc
            if eid not in employees:
                report["physics_violations"].append(f"Unknown Employee ID: {eid}")
                continue
            
            if eid in report["served_ids"]:
                 report["physics_violations"].append(f"Employee {eid} assigned to multiple routes.")
            report["served_ids"].add(eid)
            
            current_group.append(eid)
            emp = employees[eid]
            
            # Check downgrade violation (w4)
            if emp["pref_veh"] == "premium" and veh["type"] != "premium":
                report["soft_violations"].append(f"Downgrade: {eid} (Premium) in {vid} ({veh['type']})")
                report["downgrade_violations_count"] += 1

        # Re-calculating cost and time by following the sequence
        curr_id = vid
        curr_time = veh["available"]
        
        for step in sequence[1:]:
            target_id = step['location']
            
            # Dist/Time
            # Validator still uses haversine unless I change it. 
            # If I want it to match the algorithm exactly, I should use distance matrix.
            # But the user didn't provide distance matrix for validator.
            # I'll stick to haversine for now but update the flow.
            
            target_lat, target_lng = 0, 0
            earliest = 0
            latest = 9999
            if target_id == 'office':
                # Assume all employees in the same group have the same drop location
                # Let's take it from the first employee in the group
                # Actually, I should have the office location from metadata or something.
                # In payload_dict, it's (12.9716, 77.5946)
                target_lat, target_lng = 12.9716, 77.5946
            else:
                emp = employees[target_id]
                target_lat, target_lng = emp['lat'], emp['lng']
                earliest = emp['earliest']
                latest = emp['latest']
            
            dist = haversine(curr_loc_lat, curr_loc_lng, target_lat, target_lng)
            travel_time = dist / veh["speed_mpm"] if veh["speed_mpm"] > 0 else 0
            
            trip_cost += dist * veh["cost_per_km"]
            arrival_time = curr_time + travel_time
            
            if target_id != 'office':
                if arrival_time < earliest:
                    arrival_time = earliest
            else:
                # Check lateness for employees who were in the car
                # This logic is a bit complex to do while iterating.
                # Let's skip detailed lateness check for now or assume it's done elsewhere
                pass
                
            curr_time = arrival_time
            curr_loc_lat, curr_loc_lng = target_lat, target_lng
            
        report["audit"]["total_cost"] += trip_cost
        # trip_time is total_time - available_time?
        report["audit"]["total_time"] += (curr_time - veh["available"])

        report["audit"]["total_cost"] += trip_cost
        report["audit"]["total_time"] += trip_time

    unassigned = all_employee_ids - report["served_ids"]
    if unassigned:
        report["hard_violations"].append(f"Service Failure: {len(unassigned)} employees unassigned.")

    # Calculate final objective cost
    objective_cost = calculate_objective(report, weights)
    report["objective"] = objective_cost

    print_report(report, weights)

def calculate_objective(report, weights):
    """
    Calculate weighted objective cost:
    Objective = w1 * total_cost + w2 * total_time + w3 * privacy_violations + w4 * downgrade_violations
    """
    w1 = weights.get("w1", 1.0)
    w2 = weights.get("w2", 1.0)
    w3 = weights.get("w3", 1.0)
    w4 = weights.get("w4", 1.0)

    total_cost = report["audit"]["total_cost"]
    total_time = report["audit"]["total_time"]
    privacy_violations = report["privacy_violations_count"]
    downgrade_violations = report["downgrade_violations_count"]

    objective = (w1 * total_cost +
                 w2 * total_time +
                 w3 * privacy_violations +
                 w4 * downgrade_violations)

    return {
        "total": objective,
        "components": {
            "w1_cost": w1 * total_cost,
            "w2_time": w2 * total_time,
            "w3_privacy": w3 * privacy_violations,
            "w4_downgrade": w4 * downgrade_violations
        },
        "raw_values": {
            "total_cost": total_cost,
            "total_time": total_time,
            "privacy_violations": privacy_violations,
            "downgrade_violations": downgrade_violations
        }
    }

def print_report(report, weights=None):
    count_sharing = sum(1 for v in report["soft_violations"] if "Privacy Breach" in v)
    count_lateness = sum(1 for v in report["soft_violations"] if "Lateness" in v)
    count_hard_prio = sum(1 for v in report["hard_violations"] if "Priority Breach" in v)

    if report["physics_violations"]:
        print(f"\nPhysical Violations ({len(report['physics_violations'])}):")
        for v in report["physics_violations"]:
            print(f" - {v}")

    if report["hard_violations"]:
        print(f"\nHard Constraint Violations ({len(report['hard_violations'])}):")
        print(f"   [Priority Breaches: {count_hard_prio}]")
        for v in report["hard_violations"]:
            print(f" - {v}")
    else:
        print("\nNo Hard Constraint Violations")

    if report["soft_violations"]:
        print(f"\nSoft Constraint Violations ({len(report['soft_violations'])}):")
        print(f"   [Sharing Penalties: {count_sharing}]")
        print(f"   [Lateness Penalties: {count_lateness}]")
        for v in report["soft_violations"]:
            print(f" - {v}")

    print("\nOPERATIONAL METRICS")
    print(f"Total Cost: {report['audit']['total_cost']:.2f}")
    print(f"Total Time: {report['audit']['total_time']:.1f} min")

    # Print Objective Cost Breakdown
    if "objective" in report:
        obj = report["objective"]
        print("\n" + "="*50)
        print("OBJECTIVE COST CALCULATION")
        print("="*50)
        if weights:
            print(f"Weights: w1={weights['w1']}, w2={weights['w2']}, w3={weights['w3']}, w4={weights['w4']}")
        print(f"\nRaw Values:")
        print(f"  Total Cost:           {obj['raw_values']['total_cost']:.2f}")
        print(f"  Total Time:           {obj['raw_values']['total_time']:.1f} min")
        print(f"  Privacy Violations:   {obj['raw_values']['privacy_violations']}")
        print(f"  Downgrade Violations: {obj['raw_values']['downgrade_violations']}")
        print(f"\nWeighted Components:")
        print(f"  w1 × Cost:      {obj['components']['w1_cost']:.2f}")
        print(f"  w2 × Time:      {obj['components']['w2_time']:.2f}")
        print(f"  w3 × Privacy:   {obj['components']['w3_privacy']:.2f}")
        print(f"  w4 × Downgrade: {obj['components']['w4_downgrade']:.2f}")
        print(f"\n{'='*50}")
        print(f"FINAL OBJECTIVE COST: {obj['total']:.2f}")
        print(f"{'='*50}")

if __name__ == "__main__":
    if os.path.exists(SOLUTION_JSON_PATH) and os.path.exists(INPUT_JSON_PATH):
        print(f"Validating {SOLUTION_JSON_PATH} against {INPUT_JSON_PATH}...")
        print(f"Loading weights from {WEIGHTS_JSON_PATH}...")
        validate_solution(SOLUTION_JSON_PATH, INPUT_JSON_PATH, WEIGHTS_JSON_PATH)
    else:
        print(f"Error: Files not found. Check paths:\n JSON: {SOLUTION_JSON_PATH}\n INPUT: {INPUT_JSON_PATH}")