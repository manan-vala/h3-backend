import vroom
import pandas as pd
import numpy as np
import json
import os
import math
from io import BytesIO
from .lns_utils import load_data_from_bytes, DistanceMatrix, time_to_minutes

W1 = 0.7
W2 = 0.3

def solve_vroom(input_data, matrix_edge_list, file_bytes):
    employees_raw, vehicles_raw = load_data_from_bytes(file_bytes)
    dist_matrix = DistanceMatrix(matrix_edge_list)
    
    excel_file = BytesIO(file_bytes)
    meta_df = pd.read_excel(excel_file, sheet_name='metadata')
    meta = dict(zip(meta_df['key'], meta_df['value']))
    
    delays = {i: float(meta.get(f'priority_{i}_max_delay_min', 0)) for i in range(1,6)}

    problem = vroom.Input()
    
    # Define locations
    loc_ids = ["office"] + list(vehicles_raw.keys()) + list(employees_raw.keys())
    id_to_idx = {id: i for i, id in enumerate(loc_ids)}
    
    num_locs = len(loc_ids)
    
    # Build matrices for VROOM (integer matrices)
    # We'll use multiple profiles if speeds differ, but OSRM table already gives durations.
    # Actually VROOM prefers one matrix per profile if we have different vehicles.
    # But here we have durations from OSRM which already accounts for speed? 
    # No, OSRM table usually assumes a standard speed.
    # Let's use the provided durations and distances.
    
    dur_matrix = np.zeros((num_locs, num_locs), dtype=np.uint32)
    dist_matrix_m = np.zeros((num_locs, num_locs), dtype=np.uint32)
    
    for item in matrix_edge_list:
        from_id, to_id = item['id'].split('_')
        if from_id in id_to_idx and to_id in id_to_idx:
            i, j = id_to_idx[from_id], id_to_idx[to_id]
            dur_matrix[i, j] = int(item['duration_seconds'])
            dist_matrix_m[i, j] = int(item['distance_meters'])

    # Add vehicles
    for i, (vid, veh) in enumerate(vehicles_raw.items()):
        profile_name = f"profile_{vid}"
        
        # VROOM needs integer costs. Cost = W1 * dist + W2 * duration
        # We'll scale to maintain precision.
        cost_matrix = (W1 * dist_matrix_m + W2 * dur_matrix).astype(np.uint32)
        
        problem.set_durations_matrix(profile=profile_name, matrix_input=dur_matrix)
        problem.set_costs_matrix(profile=profile_name, matrix_input=cost_matrix)
        
        v = vroom.Vehicle(
            i, profile=profile_name,
            start=id_to_idx[vid],
            # end=id_to_idx["office"], # Vehicles don't HAVE to end at office in VROOM unless forced
            capacity=vroom.Amount([veh.capacity]),
            time_window=vroom.TimeWindow(int(veh.available_time * 60), 86400)
        )
        problem.add_vehicle(v)

    # Add shipments (pickups and deliveries)
    for i, (eid, emp) in enumerate(employees_raw.items()):
        t_start = int(emp.earliest_pickup * 60)
        t_end = int((emp.latest_drop + delays[emp.priority]) * 60)
        
        problem.add_shipment(
            pickup=vroom.ShipmentStep(i, location=id_to_idx[eid], time_windows=[vroom.TimeWindow(t_start, 86400)]),
            delivery=vroom.ShipmentStep(i, location=id_to_idx["office"], time_windows=[vroom.TimeWindow(0, t_end)]),
            amount=vroom.Amount([1]),
            priority=100 # High priority
        )

    solution = problem.solve(exploration_level=5, nb_threads=4)
    sol_dict = solution.to_dict()
    
    # Format to algo_output.json
    output_vehicles = []
    total_cost_all = 0
    
    def fmt_time(seconds):
        tot_min = seconds / 60.0
        h = int(tot_min // 60) % 24
        m = int(tot_min % 60)
        return f"{h:02d}:{m:02d}"

    for route in sol_dict.get('routes', []):
        v_idx = route['vehicle']
        vid = list(vehicles_raw.keys())[v_idx]
        veh = vehicles_raw[vid]
        
        steps = route.get('steps', [])
        
        # Build raw sequence
        raw_seq = []
        for s in steps:
            loc_id = loc_ids[s['location']]
            raw_seq.append({
                "location": loc_id,
                "arrival": s['arrival'],
                "departure": s['arrival'] + s.get('waiting', 0)
            })
            
        # Merge consecutive identical locations
        merged_seq = []
        for s in raw_seq:
            if not merged_seq or s['location'] != merged_seq[-1]['location']:
                merged_seq.append(s)
            else:
                merged_seq[-1]['departure'] = s['departure']
                
        formatted_seq = []
        for i, s in enumerate(merged_seq):
            fs = {
                "step": i,
                "location": s['location'],
                "arrival_time": fmt_time(s['arrival'])
            }
            if i < len(merged_seq) - 1:
                fs["departure_time"] = fmt_time(s['departure'])
            formatted_seq.append(fs)
            
        links = []
        for j in range(len(formatted_seq) - 1):
            links.append(f"{formatted_seq[j]['location']}_{formatted_seq[j+1]['location']}")
            
        # Recalculate cost using our dist_matrix
        total_dist_km = 0
        for j in range(len(formatted_seq) - 1):
            d, _ = dist_matrix.get_dist_dur(formatted_seq[j]['location'], formatted_seq[j+1]['location'])
            total_dist_km += d
            
        total_cost = total_dist_km * veh.cost_per_km
        total_time_min = (steps[-1]['arrival'] - steps[0]['arrival']) / 60.0
        
        output_vehicles.append({
            "vehicle_id": vid,
            "vehicle_type": veh.category,
            "capacity": veh.capacity,
            "avg_speed_kmph": veh.speed,
            "total_cost": round(total_cost, 2),
            "total_time_minutes": round(total_time_min, 2),
            "total_steps": len(formatted_seq),
            "routes": links,
            "route_sequence": formatted_seq
        })
        total_cost_all += total_cost

    return {
        "vehicles": output_vehicles,
        "summary": {
            "total_cost_all_vehicles": round(total_cost_all, 2)
        }
    }
