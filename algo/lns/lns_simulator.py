from typing import Dict, List, Tuple
from lns_utils import Employee, Vehicle, haversine

class RouteSimulator:
    def __init__(self, employees: Dict[str, Employee], vehicles: Dict[str, Vehicle], 
                 allow_violations: bool = False):
        self.employees = employees
        self.vehicles = vehicles
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
        curr_lat, curr_lon = vehicle.start_lat, vehicle.start_lng
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
                dist = haversine(curr_lat, curr_lon, emp.pickup_lat, emp.pickup_lng)
                travel_time = (dist / vehicle.speed) * 60
                arrival_time = curr_time + travel_time
                pickup_time = max(arrival_time, emp.earliest_pickup)
                wait_time = pickup_time - arrival_time
                
                total_distance += dist
                total_time += travel_time + wait_time
                
                curr_time = pickup_time
                curr_lat, curr_lon = emp.pickup_lat, emp.pickup_lng
                group_pickups[emp_id] = {'pickup_time': pickup_time, 'wait_time': wait_time}
            
            # --- Drop-off ---
            first_emp = self.employees[group[0]]
            hq_lat, hq_lng = first_emp.drop_lat, first_emp.drop_lng
            dist_to_hq = haversine(curr_lat, curr_lon, hq_lat, hq_lng)
            travel_time_to_hq = (dist_to_hq / vehicle.speed) * 60
            drop_time = curr_time + travel_time_to_hq
            
            total_distance += dist_to_hq
            total_time += travel_time_to_hq
            
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
            curr_lat, curr_lon = hq_lat, hq_lng
            
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
            'route_details': route_details
        }
