import json
import os
import sys
from lns_utils import load_data, DistanceMatrix

def minutes_to_time(m):
    h = int(m // 60) % 24
    minutes = int(m % 60)
    return f"{h:02d}:{minutes:02d}"

def convert_output(excel_path, matrix_path, output_lns_path):
    employees, vehicles = load_data(excel_path)
    dist_matrix = DistanceMatrix(matrix_path)
    
    if not os.path.exists(output_lns_path):
        print(f"Error: {output_lns_path} not found.")
        return

    with open(output_lns_path, 'r') as f:
        lns_output = json.load(f)

    output_vehicles = []
    total_cost_all = 0.0

    for veh_id, groups in lns_output.items():
        if veh_id not in vehicles:
            continue
        
        vehicle = vehicles[veh_id]
        curr_id = veh_id
        curr_time = vehicle.available_time
        
        total_dist = 0.0
        total_time_dur = 0.0
        
        routes_list = []
        route_sequence = []
        
        # Initial step
        route_sequence.append({
            "step": 0,
            "location": veh_id,
            "arrival_time": minutes_to_time(curr_time),
            "departure_time": minutes_to_time(curr_time)
        })
        
        step_count = 1
        
        for group in groups:
            if not group: continue
            
            # Pickups
            for emp_id in group:
                emp = employees[emp_id]
                dist, duration = dist_matrix.get_data(curr_id, emp_id)
                if dist is None: dist, duration = 0.0, 0.0
                
                arrival_time = curr_time + duration
                departure_time = max(arrival_time, emp.earliest_pickup)
                
                routes_list.append(f"{curr_id}_{emp_id}")
                route_sequence.append({
                    "step": step_count,
                    "location": emp_id,
                    "arrival_time": minutes_to_time(arrival_time),
                    "departure_time": minutes_to_time(departure_time)
                })
                
                total_dist += dist
                total_time_dur += duration + (departure_time - arrival_time)
                curr_time = departure_time
                curr_id = emp_id
                step_count += 1
            
            # Drop-off to office
            dist_to_office, dur_to_office = dist_matrix.get_data(curr_id, "office")
            if dist_to_office is None: dist_to_office, dur_to_office = 0.0, 0.0
            
            arrival_at_office = curr_time + dur_to_office
            routes_list.append(f"{curr_id}_office")
            route_sequence.append({
                "step": step_count,
                "location": "office",
                "arrival_time": minutes_to_time(arrival_at_office)
            })
            
            total_dist += dist_to_office
            total_time_dur += dur_to_office
            curr_time = arrival_at_office
            curr_id = "office"
            step_count += 1

        cost = total_dist * vehicle.cost_per_km
        total_cost_all += cost
        
        output_vehicles.append({
            "vehicle_id": vehicle.id,
            "vehicle_type": vehicle.category,
            "capacity": vehicle.capacity,
            "avg_speed_kmph": vehicle.speed,
            "total_cost": round(cost, 2),
            "total_time_minutes": round(total_time_dur, 2),
            "total_steps": step_count,
            "routes": routes_list,
            "route_sequence": route_sequence
        })

    final_output = {
        "vehicles": output_vehicles,
        "summary": {
            "total_cost_all_vehicles": round(total_cost_all, 2)
        }
    }

    dir_path = os.path.dirname(output_lns_path)
    new_output_path = os.path.join(dir_path, "new_format_output_lns.json")
    
    with open(new_output_path, 'w') as f:
        json.dump(final_output, f, indent=2)
    
    print(f"Converted output saved to {new_output_path}")

if __name__ == "__main__":
    # Default paths for quick execution if needed
    EXCEL_PATH = "templts/TestCase_TC04.xlsx"
    MATRIX_PATH = "templts/matrix_edge_list.json"
    OUTPUT_LNS_PATH = "Output/output_lns.json"
    
    if len(sys.argv) > 1: EXCEL_PATH = sys.argv[1]
    if len(sys.argv) > 2: MATRIX_PATH = sys.argv[2]
    if len(sys.argv) > 3: OUTPUT_LNS_PATH = sys.argv[3]
    
    convert_output(EXCEL_PATH, MATRIX_PATH, OUTPUT_LNS_PATH)
