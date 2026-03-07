from typing import Dict, List, Tuple
from .lns_utils import Employee, Vehicle, DistanceMatrix

class RouteSimulator:
    def __init__(self, employees: Dict[str, Employee], vehicles: Dict[str, Vehicle], 
                 dist_matrix: DistanceMatrix, allow_violations: bool = False):
        self.employees = employees
        self.vehicles = vehicles
        self.dist_matrix = dist_matrix
        self.allow_violations = allow_violations
        
        # Penalties (Adjusted to prefer unassigned over hard violations)
        self.PENALTY_CAPACITY = 5000000.0  
        self.PENALTY_TIME_WINDOW = 50000.0  
        self.PENALTY_PRIORITY = 2000000.0    
        self.PENALTY_HARD_UNASSIGNED = 20000.0 # Cheaper to drop than to violate safety/time

    def simulate_vehicle(self, veh_id: str, groups: List[List[str]]) -> Tuple[bool, Dict]:
        """
        Simulate a vehicle route. 
        """
        vehicle = self.vehicles[veh_id]
        curr_loc = veh_id
        curr_time = vehicle.available_time
        
        total_distance = 0.0
        total_time = 0.0
        
        # Soft penalties
        sharing_penalty = 0.0
        vehicle_penalty = 0.0
        
        # Violation penalties
        violation_penalty = 0.0
        hard_violation_count = 0
        
        route_details = {}
        route_sequence = []
        
        # Add initial vehicle position
        route_sequence.append({
            "step": 0,
            "location": veh_id,
            "arrival_time": curr_time,
            "departure_time": curr_time
        })
        step_count = 1

        for group in groups:
            if not group:
                continue
                
            # --- Check Capacity ---
            if len(group) > vehicle.capacity:
                if not self.allow_violations:
                    return False, {'error': f'Capacity exceeded'}
                else:
                    over = len(group) - vehicle.capacity
                    violation_penalty += over * self.PENALTY_CAPACITY
                    hard_violation_count += 1
            
            group_pickups = {}
            
            # --- Pickups ---
            for emp_id in group:
                emp = self.employees[emp_id]
                dist, travel_time = self.dist_matrix.get_dist_dur(curr_loc, emp_id, speed_kmph=vehicle.speed)
                arrival_time = curr_time + travel_time
                pickup_time = max(arrival_time, emp.earliest_pickup)
                wait_time = pickup_time - arrival_time
                
                total_distance += dist
                total_time += travel_time + wait_time
                
                curr_time = pickup_time
                curr_loc = emp_id
                group_pickups[emp_id] = {'pickup_time': pickup_time, 'wait_time': wait_time}
                
                route_sequence.append({
                    "step": step_count,
                    "location": emp_id,
                    "arrival_time": arrival_time,
                    "departure_time": pickup_time
                })
                step_count += 1
            
            # --- Drop-off ---
            # Assumption: All employees in a group are dropped at "office"
            office_id = "office"
            dist_to_office, travel_time_to_office = self.dist_matrix.get_dist_dur(curr_loc, office_id, speed_kmph=vehicle.speed)
            drop_time = curr_time + travel_time_to_office
            
            total_distance += dist_to_office
            total_time += travel_time_to_office
            
            # --- Check Deadlines ---
            for emp_id in group:
                emp = self.employees[emp_id]
                delay_actual = max(0, drop_time - emp.latest_drop)
                
                if delay_actual > 0:
                    violation_penalty += delay_actual * self.PENALTY_TIME_WINDOW
                
                if delay_actual > emp.max_delay:
                     if not self.allow_violations:
                         return False, {'error': f'Priority Breach'}
                     else:
                         violation_penalty += self.PENALTY_PRIORITY
                         hard_violation_count += 1

            curr_time = drop_time
            curr_loc = office_id
            
            route_sequence.append({
                "step": step_count,
                "location": office_id,
                "arrival_time": drop_time
            })
            step_count += 1

            # --- Soft Constraints ---
            group_size = len(group)
            for emp_id in group:
                emp = self.employees[emp_id]
                if emp.sharing_preference == 'single' and group_size > 1:
                    sharing_penalty += 1.0
                elif emp.sharing_preference == 'double' and group_size > 2:
                    sharing_penalty += 1.0
                elif emp.sharing_preference == 'triple' and group_size > 3:
                    sharing_penalty += 1.0
                
                if emp.vehicle_preference != 'any' and emp.vehicle_preference != vehicle.category:
                    vehicle_penalty += 1.0
                
                route_details[emp_id] = {
                    'pickup_time': group_pickups[emp_id]['pickup_time'],
                    'drop_time': drop_time
                }
        
        raw_cost = total_distance * vehicle.cost_per_km
        
        return True, {
            'total_cost': raw_cost,
            'total_time': total_time,
            'total_distance': total_distance,
            'sharing_penalty': sharing_penalty,
            'vehicle_penalty': vehicle_penalty,
            'violation_penalty': violation_penalty,
            'hard_violation_count': hard_violation_count,
            'route_details': route_details,
            'route_sequence': route_sequence
        }