import math
from datetime import datetime, timedelta

def parse_time(time_str):
    """Parses HH:MM string to a datetime object."""
    return datetime.strptime(time_str, "%H:%M")

def format_time(dt_obj):
    """Formats datetime object back to HH:MM string."""
    return dt_obj.strftime("%H:%M")

def get_edge_data(id1, id2, distance_map):
    """
    Retrieves distance (m) and duration (s) between two IDs.
    Checks both A_B and B_A keys.
    """
    key1 = f"{id1}_{id2}"
    key2 = f"{id2}_{id1}"
    
    if key1 in distance_map:
        return distance_map[key1]['distance_meters'], distance_map[key1]['duration_seconds']
    elif key2 in distance_map:
        return distance_map[key2]['distance_meters'], distance_map[key2]['duration_seconds']
    else:
        if id1 == id2: return 0, 0
        return float('inf'), float('inf')

def solve_vrp(input_data, distance_data_list):
    """
    Main Solver Function.
    Args:
        input_data (dict): The original payload (employees, vehicles, metadata).
        distance_data_list (list): The flat list of edge costs from logic.py.
    """
    employees = input_data['employees']
    vehicles = input_data['vehicles']
    
    # 1. Create Lookup Map
    # distance_data_list comes from logic.py which returns [ {id, distance_meters, ...}, ... ]
    dist_map = {item['id']: item for item in distance_data_list}
    
    # 2. Initial Assignment (Clustering)
    vehicle_assignments = {v['vehicle_id']: [] for v in vehicles}
    
    for emp in employees:
        emp_id = emp['employee_id']
        best_vehicle = None
        min_dist = float('inf')
        
        for veh in vehicles:
            veh_id = veh['vehicle_id']
            dist, _ = get_edge_data(veh_id, emp_id, dist_map)
            if dist < min_dist:
                min_dist = dist
                best_vehicle = veh_id
        
        if best_vehicle:
            vehicle_assignments[best_vehicle].append(emp)

    # 3. Routing Simulation
    output_vehicles = []
    total_cost_all = 0
    
    for veh in vehicles:
        veh_id = veh['vehicle_id']
        assigned_emps = vehicle_assignments[veh_id]
        
        if not assigned_emps:
            continue

        current_loc = veh_id 
        current_time = parse_time(veh['available_from'])
        
        total_dist_m = 0
        total_duration_s = 0
        
        route_links = [] 
        steps = []
        
        # Initial Step
        steps.append({
            "step": 0,
            "location": veh_id,
            "arrival_time": format_time(current_time),
            "departure_time": format_time(current_time)
        })
        
        step_count = 0
        
        while assigned_emps:
            # --- Pickup Phase ---
            current_load = 0
            capacity = veh['capacity']
            
            while current_load < capacity and assigned_emps:
                nearest_emp = None
                nearest_dist = float('inf')
                nearest_dur = float('inf')
                
                for emp in assigned_emps:
                    d, t = get_edge_data(current_loc, emp['employee_id'], dist_map)
                    if d < nearest_dist:
                        nearest_dist = d
                        nearest_dur = t
                        nearest_emp = emp
                
                target_id = nearest_emp['employee_id']
                
                total_dist_m += nearest_dist
                total_duration_s += nearest_dur
                current_time += timedelta(seconds=nearest_dur)
                
                # Create the Route Tag (e.g. V01_E01)
                route_links.append(f"{current_loc}_{target_id}")
                
                step_count += 1
                steps.append({
                    "step": step_count,
                    "location": target_id,
                    "arrival_time": format_time(current_time),
                    "departure_time": format_time(current_time)
                })
                
                current_loc = target_id
                assigned_emps.remove(nearest_emp)
                current_load += 1
            
            # --- Drop Phase (Return to Office) ---
            dist_to_office, dur_to_office = get_edge_data(current_loc, "office", dist_map)
            
            total_dist_m += dist_to_office
            total_duration_s += dur_to_office
            current_time += timedelta(seconds=dur_to_office)
            
            route_links.append(f"{current_loc}_office")
            
            step_count += 1
            steps.append({
                "step": step_count,
                "location": "office",
                "arrival_time": format_time(current_time)
            })
            
            current_loc = "office"

        # Costs
        dist_km = total_dist_m / 1000.0
        cost = dist_km * veh['cost_per_km']
        total_time_min = total_duration_s / 60.0
        
        total_cost_all += cost

        output_vehicles.append({
            "vehicle_id": veh['vehicle_id'],
            "vehicle_type": veh.get('category', 'standard'),
            "capacity": veh['capacity'],
            "avg_speed_kmph": veh['avg_speed_kmph'],
            "total_cost": round(cost, 2),
            "total_time_minutes": round(total_time_min, 2),
            "total_steps": step_count + 1,
            "routes": route_links,         # List of "A_B" tags
            "route_sequence": steps        # Metadata for markers
        })

    return {
        "vehicles": output_vehicles,
        "summary": {
            "total_cost_all_vehicles": round(total_cost_all, 2)
        }
    }