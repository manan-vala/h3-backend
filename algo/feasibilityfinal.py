import json
import math
import os
import pandas as pd
from .lns_utils import load_data_from_bytes, DistanceMatrix, time_to_minutes

def get_feasibility_score(file_bytes, matrix_edge_list, solution_json):
    """
    Evaluates a solution and returns metrics.
    solution_json is the output in the format of algo_output.json
    """
    employees_raw, vehicles_raw = load_data_from_bytes(file_bytes)
    dist_matrix = DistanceMatrix(matrix_edge_list)
    
    # We need metadata for weights and delays
    from io import BytesIO
    excel_file = BytesIO(file_bytes)
    meta_df = pd.read_excel(excel_file, sheet_name='metadata')
    meta = dict(zip(meta_df['key'], meta_df['value']))
    
    Wc = float(meta.get('objective_cost_weight', 0.6))
    Wt = float(meta.get('objective_time_weight', 0.4))
    
    delays = {}
    for i in range(1, 6):
        key = f'priority_{i}_max_delay_min'
        delays[str(i)] = float(meta.get(key, 0))

    served_ids = set()
    hard_count = 0 
    soft_count = 0
    total_cost = 0.0
    total_time_min = 0.0
    share_limits = {'single': 1, 'double': 2, 'triple': 3}

    vehicles_sol = solution_json.get('vehicles', [])
    
    for v_sol in vehicles_sol:
        vid = v_sol['vehicle_id']
        if vid not in vehicles_raw: continue
        veh = vehicles_raw[vid]
        
        # Sequence
        sequence = v_sol.get('route_sequence', [])
        if not sequence: continue
        
        curr_time = veh.available_time
        curr_loc = vid
        
        # We need to track who is currently in the vehicle to check capacity and sharing
        current_passengers = []
        
        for step in sequence[1:]: # Skip start
            target_loc = step['location']
            
            dist_km, travel_time_min = dist_matrix.get_dist_dur(curr_loc, target_loc)
            
            total_cost += dist_km * veh.cost_per_km
            arrival_time = curr_time + travel_time_min
            
            if target_loc == 'office':
                # Drop off everyone
                for eid in current_passengers:
                    emp = employees_raw[eid]
                    delay = max(0, arrival_time - emp.latest_drop)
                    max_allowed = delays.get(str(emp.priority), 0.0)
                    if delay > max_allowed:
                        # Hard violation if we want to follow the "strictly 0" rule for everything
                        # But user says "strictly 0 hard constraint violations"
                        # Usually priority breach is hard.
                        hard_count += 1
                    elif delay > 0:
                        soft_count += 1
                current_passengers = []
            else:
                # Pickup
                eid = target_loc
                if eid not in employees_raw:
                    # Should not happen
                    continue
                
                emp = employees_raw[eid]
                pickup_time = max(arrival_time, emp.earliest_pickup)
                
                if eid in served_ids:
                    hard_count += 1 # Duplicate
                served_ids.add(eid)
                
                current_passengers.append(eid)
                
                # Check capacity
                if len(current_passengers) > veh.capacity:
                    hard_count += 1
                
                # Check soft constraints
                if emp.vehicle_preference == "premium" and veh.category != "premium":
                    soft_count += 1
                
                # Update current time to departure time
                arrival_time = pickup_time # Start from when we actually pick up
            
            curr_time = arrival_time
            curr_loc = target_loc
            
            # Check sharing preference for everyone currently in the car
            group_size = len(current_passengers)
            for pid in current_passengers:
                p_emp = employees_raw[pid]
                limit = share_limits.get(p_emp.sharing_preference, 999)
                if group_size > limit:
                    soft_count += 1

    objective = (Wc * total_cost) + (Wt * total_time_min)

    return {
        "served_count": len(served_ids),
        "hard_violations": hard_count,
        "soft_violations": soft_count,
        "objective": objective,
        "total_cost": total_cost,
        "total_time_min": total_time_min
    }
