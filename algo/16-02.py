import pandas as pd
import numpy as np
import math
import time
import random
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Set
from datetime import datetime, time as dt_time
from pathlib import Path
import json
from collections import defaultdict
import copy
from io import BytesIO
from .lns_utils import load_data_from_bytes, DistanceMatrix, time_to_minutes

# ==========================================
# CONFIGURATION - ADJUST THESE
# ==========================================

@dataclass
class Config:
    ALNS_ITERATIONS: int = 1000
    ALNS_TIME_LIMIT: int = 20
    DESTROY_RATE_MIN: float = 0.05
    DESTROY_RATE_MAX: float = 0.40
    DESTROY_RATE_ADAPTIVE: bool = True

    TEMPERATURE_START: float = 100.0
    TEMPERATURE_END: float = 0.01

    SCORE_GLOBAL_BEST: float = 6.0
    SCORE_BETTER: float = 3.0
    SCORE_EQUAL: float = 1.0
    SCORE_WORSE: float = 0.0
    WEIGHT_UPDATE_INTERVAL: int = 100
    WEIGHT_DECAY: float = 0.8

    EARLY_TERMINATION_ITERATIONS: int = 500
    MIN_ACCEPTANCE_RATE: float = 0.02

    # Local search settings
    USE_LOCAL_SEARCH: bool = True
    LS_ITERATIONS: int = 10
    LS_PROBABILITY: float = 0.15

    # Neighborhood reduction
    K_NEAREST_VEHICLES: int = 5
    USE_NEIGHBORHOOD_FILTER: bool = True

    # SOFT CONSTRAINT PENALTIES - ADJUST THESE
    VEHICLE_PREF_PENALTY: float = 500.0
    SHARING_PREF_PENALTY: float = 200.0
    VEHICLE_TYPE_PENALTY: float = 300.0
    UNASSIGNED_PENALTY: float = 1000000.0

config = Config()

# ==========================================
# DISTANCE PROVIDER
# ==========================================

class DistanceProvider:
    _matrix: DistanceMatrix = None

    @classmethod
    def set_matrix(cls, matrix: DistanceMatrix):
        cls._matrix = matrix

    @classmethod
    def get_dist(cls, from_id: str, to_id: str) -> float:
        d, _ = cls._matrix.get_dist_dur(from_id, to_id)
        return d

    @classmethod
    def get_dur(cls, from_id: str, to_id: str) -> float:
        _, dur = cls._matrix.get_dist_dur(from_id, to_id)
        return dur

# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass 
class Request:
    id: str
    priority: int
    pickup_id: str
    drop_id: str
    earliest_pickup: float
    latest_drop: float
    max_delay: float
    vehicle_pref: str
    max_share: int

    @property
    def latest_drop_hard(self) -> float:
        """Latest drop time including max delay - HARD CONSTRAINT"""
        return self.latest_drop + self.max_delay

    @property
    def time_window_width(self) -> float:
        return self.latest_drop_hard - self.earliest_pickup

@dataclass
class Vehicle:
    id: str
    capacity: int
    cost_per_km: float
    speed_kmph: float
    start_id: str
    available_from: float
    category: str
    fuel_type: str
    vehicle_type: str

    @property
    def is_premium(self) -> bool:
        return self.category == 'premium'

@dataclass
class RouteLeg:
    request_id: str
    is_pickup: bool
    arrival_time: float
    departure_time: float
    cumulative_load: int
    location_id: str

class OptimizedRoute:
    def __init__(self, vehicle: Vehicle, requests_map: Dict[str, Request] = None):
        self.vehicle = vehicle
        self.legs: List[RouteLeg] = []
        self.request_set: Set[str] = set()
        self.requests_map = requests_map
        self._total_distance: float = 0.0
        self._total_time: float = 0.0
        self._max_load: int = 0

    def copy(self) -> 'OptimizedRoute':
        r = OptimizedRoute(self.vehicle, self.requests_map)
        r.legs = [RouteLeg(leg.request_id, leg.is_pickup, leg.arrival_time,
                          leg.departure_time, leg.cumulative_load, leg.location_id) for leg in self.legs]
        r.request_set = self.request_set.copy()
        r._total_distance = self._total_distance
        r._total_time = self._total_time
        r._max_load = self._max_load
        return r

    def set_requests_map(self, requests_map: Dict[str, Request]):
        self.requests_map = requests_map

    @property
    def total_distance(self) -> float:
        return self._total_distance

    @property
    def total_duration_hours(self) -> float:
        return self._total_time

    @property
    def start_time(self) -> float:
        return self.legs[0].arrival_time if self.legs else self.vehicle.available_from

    def get_max_load(self) -> int:
        return self._max_load

    def update_incremental_stats(self):
        """Update distance, time, and max load from legs"""
        if not self.legs:
            self._total_distance = 0.0
            self._total_time = 0.0
            self._max_load = 0
            return

        total_dist = 0.0
        prev_loc_id = self.vehicle.start_id
        max_load = 0

        for leg in self.legs:
            curr_loc_id = leg.location_id
            total_dist += DistanceProvider.get_dist(prev_loc_id, curr_loc_id)
            prev_loc_id = curr_loc_id
            max_load = max(max_load, leg.cumulative_load)

        self._total_distance = total_dist
        self._total_time = (self.legs[-1].departure_time - self.vehicle.available_from) * 24 if self.legs else 0.0
        self._max_load = max_load

    def get_pickup_groups(self) -> List[List[str]]:
        if not self.legs:
            return []

        pickup_groups = []
        current_group = []
        active_pickups = set()

        for leg in self.legs:
            if leg.is_pickup:
                current_group.append(leg.request_id)
                active_pickups.add(leg.request_id)
            else:
                if leg.request_id in active_pickups:
                    active_pickups.remove(leg.request_id)
                if not active_pickups and current_group:
                    pickup_groups.append(current_group)
                    current_group = []

        if current_group:
            pickup_groups.append(current_group)

        return pickup_groups

class Solution:
    def __init__(self):
        self.routes: Dict[str, OptimizedRoute] = {}
        self.unassigned: Set[str] = set()
        self._score_cache: Optional[float] = None
        self.iteration_found: int = 0

    def copy(self) -> 'Solution':
        s = Solution()
        s.routes = {vid: r.copy() for vid, r in self.routes.items()}
        s.unassigned = self.unassigned.copy()
        s.iteration_found = self.iteration_found
        return s

    def shallow_copy(self) -> 'Solution':
        """Lightweight copy - routes are shallow copied"""
        s = Solution()
        s.routes = self.routes.copy()
        s.unassigned = self.unassigned.copy()
        s.iteration_found = self.iteration_found
        return s

    def get_all_assigned(self) -> Set[str]:
        assigned = set()
        for route in self.routes.values():
            assigned.update(route.request_set)
        return assigned

    def validate_solution(self, request_map: Dict[str, Request]) -> Tuple[bool, List[str]]:
        errors = []

        employee_assignments = {}
        for vid, route in self.routes.items():
            for req_id in route.request_set:
                if req_id in employee_assignments:
                    errors.append(f"MULTI_ROUTE: {req_id}")
                else:
                    employee_assignments[req_id] = vid

        for req_id in self.unassigned:
            if req_id in employee_assignments:
                errors.append(f"DUAL_STATE: {req_id}")

        for vid, route in self.routes.items():
            if route.request_set:
                valid, msg = self._validate_route(route, request_map)
                if not valid:
                    errors.append(f"ROUTE[{vid}]: {msg}")

        return len(errors) == 0, errors

    def _validate_route(self, route: OptimizedRoute, requests_map: Dict[str, Request]) -> Tuple[bool, str]:
        if not route.legs:
            return True, "OK"

        # HARD CONSTRAINT: Capacity
        max_load = route.get_max_load()
        if max_load > route.vehicle.capacity:
            return False, f"CAPACITY: {max_load}/{route.vehicle.capacity}"

        pickup_times = {}
        drop_times = {}

        for leg in route.legs:
            req = requests_map[leg.request_id]

            if leg.is_pickup:
                # HARD CONSTRAINT: Pickup after earliest available
                if leg.departure_time < req.earliest_pickup - 1e-9:
                    return False, f"EARLY_PICKUP: {leg.request_id}"
                pickup_times[leg.request_id] = leg.departure_time
            else:
                # HARD CONSTRAINT: Drop before latest + max_delay
                if leg.departure_time > req.latest_drop_hard + 1e-9:
                    return False, f"LATE_DROP: {leg.request_id} (dep={leg.departure_time:.4f} > hard={req.latest_drop_hard:.4f})"
                drop_times[leg.request_id] = leg.arrival_time

        # HARD CONSTRAINT: Pickup before drop
        for req_id in route.request_set:
            if req_id in pickup_times and req_id in drop_times:
                if pickup_times[req_id] > drop_times[req_id] + 1e-9:
                    return False, f"PICKUP_AFTER_DROP: {req_id}"

        # Verify load consistency
        curr_load = 0
        for leg in route.legs:
            if leg.is_pickup:
                curr_load += 1
            else:
                curr_load -= 1
            if curr_load != leg.cumulative_load:
                return False, f"LOAD_MISMATCH"
            if curr_load < 0:
                return False, f"NEGATIVE_LOAD"

        return True, "OK"

    def get_objective(self, request_map: Dict[str, Request], Wc: float, Wt: float) -> float:
        if self._score_cache is not None:
            return self._score_cache

        total_cost = 0.0
        total_time = 0.0

        for route in self.routes.values():
            if route.requests_map is None:
                route.set_requests_map(request_map)

            c, t = self._evaluate_route(route, request_map)
            total_cost += c
            total_time += t

        penalty = len(self.unassigned) * config.UNASSIGNED_PENALTY
        score = Wc * total_cost + Wt * total_time + penalty
        self._score_cache = score
        return score

    def _evaluate_route(self, route: OptimizedRoute, request_map: Dict[str, Request]) -> Tuple[float, float]:
        if not route.legs:
            return 0.0, 0.0

        # Use cached distance - no recomputation needed
        op_cost = route.total_distance * route.vehicle.cost_per_km
        time_component = route.total_duration_hours * 60

        violations = self._count_violations(route, request_map)
        soft_penalty = (violations['vehicle_pref'] * config.VEHICLE_PREF_PENALTY + 
                       violations['sharing_pref'] * config.SHARING_PREF_PENALTY +
                       violations['vehicle_type'] * config.VEHICLE_TYPE_PENALTY)

        return op_cost + soft_penalty, time_component

    def _count_violations(self, route: OptimizedRoute, request_map: Dict[str, Request]) -> Dict:
        violations = {'vehicle_pref': 0, 'sharing_pref': 0, 'vehicle_type': 0}

        if not route.legs:
            return violations

        active_requests = {}
        for leg in route.legs:
            req = request_map[leg.request_id]

            if leg.is_pickup:
                # SOFT CONSTRAINT: Vehicle preference (premium/normal)
                if req.vehicle_pref == 'premium' and not route.vehicle.is_premium:
                    violations['vehicle_pref'] += 1
                
                # SOFT CONSTRAINT: Vehicle type matching (single/double/triple)
                vehicle_type = route.vehicle.vehicle_type.lower()
                if req.max_share == 1 and vehicle_type not in ['2w', '4w']:
                    violations['vehicle_type'] += 1
                elif req.max_share == 2 and vehicle_type == 'van':
                    violations['vehicle_type'] += 1
                    
                active_requests[leg.request_id] = req
            else:
                if leg.request_id in active_requests:
                    del active_requests[leg.request_id]

            # SOFT CONSTRAINT: Sharing preference violation
            if active_requests:
                min_share = min(r.max_share for r in active_requests.values())
                if leg.cumulative_load > min_share:
                    violations['sharing_pref'] += leg.cumulative_load - min_share

        return violations

    def get_served_count(self) -> int:
        return len(self.get_all_assigned())

    def invalidate_cache(self):
        self._score_cache = None

class EnhancedALNSSolver:
    def __init__(self, data: Dict):
        self.data = data
        self.requests = data['requests']
        self.requests_list = data['requests_list']
        self.vehicles = data['vehicles']
        self.vehicles_list = data['vehicles_list']
        self.Wc = data['Wc']
        self.Wt = data['Wt']

        # Precompute vehicle-request distances
        self._vehicle_request_distances = self._compute_vehicle_request_distances()
        
        # Precompute feasible vehicles per request
        self.request_vehicle_map = self._precompute_feasible_vehicles()

        self.best_solution = None
        self.current_solution = None
        self.global_best_solution = None

        self.destroy_ops = [
            self.destroy_random, self.destroy_worst_cost, self.destroy_shaw,
            self.destroy_sharing_violation, self.destroy_time_window,
            self.destroy_route, self.destroy_zone,
        ]
        self.n_destroy = len(self.destroy_ops)
        self.destroy_weights = np.ones(self.n_destroy)
        self.destroy_scores = np.zeros(self.n_destroy)
        self.destroy_usage = np.zeros(self.n_destroy)

        self.current_destroy_min = config.DESTROY_RATE_MIN
        self.current_destroy_max = config.DESTROY_RATE_MAX
        self.stagnation_count = 0

        self.historical_costs = defaultdict(float)
        self.request_cost_updates = defaultdict(int)

        self.temperature = config.TEMPERATURE_START

        self.stats = {'iterations': 0, 'improvements': 0, 'acceptances': 0, 
                     'rejections': 0, 'ls_improvements': 0}

    def _compute_vehicle_request_distances(self):
        distances = {}
        for vid, vehicle in self.vehicles.items():
            distances[vid] = {}
            for rid, req in self.requests.items():
                dist = DistanceProvider.get_dist(vehicle.start_id, req.pickup_id)
                distances[vid][rid] = dist
        return distances

    def _precompute_feasible_vehicles(self) -> Dict[str, List[str]]:
        """Precompute nearest vehicles for each request"""
        request_vehicle_map = {}
        for rid in self.requests:
            vehicle_distances = [(vid, self._vehicle_request_distances[vid][rid]) 
                                for vid in self.vehicles.keys()]
            vehicle_distances.sort(key=lambda x: x[1])
            request_vehicle_map[rid] = [vid for vid, _ in vehicle_distances[:config.K_NEAREST_VEHICLES]]
        return request_vehicle_map

    def _calculate_insertion_delta(self, route: OptimizedRoute, req: Request, p_idx: int, d_idx: int) -> float:
        legs = route.legs
        vehicle = route.vehicle
        total_delta = 0.0

        # Simple approach for distance delta using DistanceProvider
        # Pickup insertion
        prev_id = legs[p_idx-1].location_id if p_idx > 0 else vehicle.start_id
        next_id = legs[p_idx].location_id if p_idx < len(legs) else None
        
        total_delta += DistanceProvider.get_dist(prev_id, req.pickup_id)
        if next_id:
            total_delta += DistanceProvider.get_dist(req.pickup_id, next_id) - DistanceProvider.get_dist(prev_id, next_id)
        
        # Drop insertion (more complex due to sequence shift, but let's approximate or just rebuild)
        # For simplicity in this adaptation, we use the _insert_request full check for delta
        return total_delta * vehicle.cost_per_km

    def _quick_feasibility_check(self, route: OptimizedRoute, req: Request, p_idx: int, d_idx: int) -> bool:
        # For this adaptation, let's just rely on the full validation in _insert_request
        return True

    def _validate_route(self, route: OptimizedRoute):
        if not route.legs: return True, "OK"
        if route.get_max_load() > route.vehicle.capacity: return False, "CAPACITY"
        
        pickup_times = {}
        drop_times = {}
        for leg in route.legs:
            req = self.requests[leg.request_id]
            if leg.is_pickup:
                if leg.departure_time < req.earliest_pickup - 1e-9: return False, "EARLY_PICKUP"
                pickup_times[leg.request_id] = leg.departure_time
            else:
                if leg.departure_time > req.latest_drop_hard + 1e-9: return False, "LATE_DROP"
                drop_times[leg.request_id] = leg.arrival_time
        
        for rid in route.request_set:
            if rid in pickup_times and rid in drop_times:
                if pickup_times[rid] > drop_times[rid] + 1e-9: return False, "PICKUP_BEFORE_DROP"
        return True, "OK"

    def _insert_request(self, route, req, pickup_idx, drop_idx):
        new_legs = []
        curr_time = route.vehicle.available_from
        curr_load = 0
        original_legs = route.legs[:]
        n_orig = len(original_legs)
        orig_i = 0
        prev_loc_id = route.vehicle.start_id

        for i in range(n_orig + 2):
            target_id = None
            is_new_pickup = (i == pickup_idx)
            is_new_drop = (i == drop_idx)
            
            if is_new_pickup:
                target_id = req.pickup_id
                rid = req.id
                is_pickup = True
            elif is_new_drop:
                target_id = req.drop_id
                rid = req.id
                is_pickup = False
            elif orig_i < n_orig:
                leg = original_legs[orig_i]
                target_id = leg.location_id
                rid = leg.request_id
                is_pickup = leg.is_pickup
                orig_i += 1
            else: continue

            dist = DistanceProvider.get_dist(prev_loc_id, target_id)
            dur_min = DistanceProvider.get_dur(prev_loc_id, target_id)
            arrival = curr_time + (dur_min / 60.0 / 24.0)
            
            if is_pickup:
                departure = max(arrival, self.requests[rid].earliest_pickup)
                curr_load += 1
            else:
                departure = arrival
                curr_load -= 1
            
            if curr_load > route.vehicle.capacity or curr_load < 0: return None
            if not is_pickup and arrival > self.requests[rid].latest_drop_hard + 1e-9: return None
            
            new_legs.append(RouteLeg(rid, is_pickup, arrival, departure, curr_load, target_id))
            curr_time = departure
            prev_loc_id = target_id

        route.legs = new_legs
        route.request_set.add(req.id)
        route.update_incremental_stats()
        return route

    def _find_best_insertion(self, route, req):
        n = len(route.legs)
        best_score = float('inf')
        best_positions = None

        for p_idx in range(n + 1):
            for d_idx in range(p_idx + 1, n + 2):
                trial_route = self._insert_request(route.copy(), req, p_idx, d_idx)
                if trial_route:
                    valid, _ = self._validate_route(trial_route)
                    if valid:
                        cost, _ = self._evaluate_route(trial_route, self.requests)
                        if cost < best_score:
                            best_score = cost
                            best_positions = (p_idx, d_idx)
        return (*best_positions, best_score) if best_positions else None

    def _evaluate_route(self, route: OptimizedRoute, request_map: Dict[str, Request]) -> Tuple[float, float]:
        if not route.legs: return 0.0, 0.0
        op_cost = route.total_distance * route.vehicle.cost_per_km
        time_min = route.total_duration_hours * 60
        violations = self._count_route_violations(route)
        penalty = (violations['vehicle_pref'] * config.VEHICLE_PREF_PENALTY + 
                  violations['sharing_pref'] * config.SHARING_PREF_PENALTY +
                  violations['vehicle_type'] * config.VEHICLE_TYPE_PENALTY)
        return op_cost + penalty, time_min

    def _count_route_violations(self, route):
        violations = {'vehicle_pref': 0, 'sharing_pref': 0, 'vehicle_type': 0}
        active_requests = {}
        for leg in route.legs:
            req = self.requests[leg.request_id]
            if leg.is_pickup:
                if req.vehicle_pref == 'premium' and not route.vehicle.is_premium: violations['vehicle_pref'] += 1
                vehicle_type = route.vehicle.vehicle_type.lower()
                if req.max_share == 1 and vehicle_type not in ['2w', '4w']: violations['vehicle_type'] += 1
                elif req.max_share == 2 and vehicle_type == 'van': violations['vehicle_type'] += 1
                active_requests[leg.request_id] = req
            else:
                if leg.request_id in active_requests: del active_requests[leg.request_id]
            if active_requests:
                min_share = min(r.max_share for r in active_requests.values())
                if leg.cumulative_load > min_share: violations['sharing_pref'] += leg.cumulative_load - min_share
        return violations

    def _rebuild_route(self, vehicle, legs):
        route = OptimizedRoute(vehicle, self.requests)
        curr_time = vehicle.available_from
        curr_load = 0
        new_legs = []
        prev_loc_id = vehicle.start_id
        for leg_spec in legs:
            req = self.requests[leg_spec.request_id]
            target_id = req.pickup_id if leg_spec.is_pickup else req.drop_id
            dur_min = DistanceProvider.get_dur(prev_loc_id, target_id)
            arrival = curr_time + (dur_min / 60.0 / 24.0)
            if leg_spec.is_pickup:
                departure = max(arrival, req.earliest_pickup)
                curr_load += 1
            else:
                departure = arrival
                curr_load -= 1
            if curr_load > vehicle.capacity or curr_load < 0: return None
            new_legs.append(RouteLeg(req.id, leg_spec.is_pickup, arrival, departure, curr_load, target_id))
            curr_time = departure
            prev_loc_id = target_id
        route.legs = new_legs
        route.request_set = set(l.request_id for l in new_legs)
        route.update_incremental_stats()
        return route

    # Local Search
    def local_search(self, solution):
        return solution # Simplified LS for speed

    # Destroy Operators
    def _remove_requests(self, solution, req_ids):
        for rid in req_ids:
            for vid, route in solution.routes.items():
                if rid in route.request_set:
                    new_legs = [l for l in route.legs if l.request_id != rid]
                    rebuilt = self._rebuild_route(route.vehicle, new_legs)
                    if rebuilt: solution.routes[vid] = rebuilt
                    else: 
                        route.legs = []
                        route.request_set = set()
                        route.update_incremental_stats()
                    break
            solution.unassigned.add(rid)

    def destroy_random(self, sol, n):
        new_sol = sol.shallow_copy()
        assigned = list(new_sol.get_all_assigned())
        to_rem = random.sample(assigned, min(n, len(assigned))) if assigned else []
        self._remove_requests(new_sol, to_rem)
        return new_sol

    def destroy_worst_cost(self, sol, n): return self.destroy_random(sol, n)
    def destroy_shaw(self, sol, n): return self.destroy_random(sol, n)
    def destroy_sharing_violation(self, sol, n): return self.destroy_random(sol, n)
    def destroy_time_window(self, sol, n): return self.destroy_random(sol, n)
    def destroy_route(self, sol, n): return self.destroy_random(sol, n)
    def destroy_zone(self, sol, n): return self.destroy_random(sol, n)

    # Repair
    def repair_regret(self, sol, noise=0.0):
        new_sol = sol.shallow_copy()
        pending = list(new_sol.unassigned)
        new_sol.unassigned = set()
        random.shuffle(pending)
        for rid in pending:
            req = self.requests[rid]
            best_v, best_p, best_d, min_cost = None, None, None, float('inf')
            for vid in self.request_vehicle_map[rid]:
                res = self._find_best_insertion(new_sol.routes[vid], req)
                if res:
                    p, d, cost = res
                    if cost < min_cost: min_cost, best_v, best_p, best_d = cost, vid, p, d
            if best_v: self._insert_request(new_sol.routes[best_v], req, best_p, best_d)
            else: new_sol.unassigned.add(rid)
        new_sol.invalidate_cache()
        return new_sol

    def solve(self, time_limit=20):
        sol = Solution()
        for v in self.vehicles.values(): sol.routes[v.id] = OptimizedRoute(v, self.requests)
        self.current_solution = self.repair_regret(sol)
        self.global_best_solution = self.current_solution.copy()
        start = time.time()
        iter = 0
        while time.time() - start < time_limit:
            iter += 1
            n_rem = max(2, int(len(self.requests) * random.uniform(0.1, 0.3)))
            temp = self.destroy_random(self.current_solution, n_rem)
            temp = self.repair_regret(temp)
            if temp.get_objective(self.requests, self.Wc, self.Wt) < self.current_solution.get_objective(self.requests, self.Wc, self.Wt):
                self.current_solution = temp
                if temp.get_objective(self.requests, self.Wc, self.Wt) < self.global_best_solution.get_objective(self.requests, self.Wc, self.Wt):
                    self.global_best_solution = temp.copy()
        return self.global_best_solution

def solve_alns(input_data, matrix_edge_list, file_bytes):
    DistanceProvider.set_matrix(DistanceMatrix(matrix_edge_list))
    
    excel_file = BytesIO(file_bytes)
    employees_df = pd.read_excel(excel_file, sheet_name='employees')
    vehicles_df = pd.read_excel(excel_file, sheet_name='vehicles')
    meta_df = pd.read_excel(excel_file, sheet_name='metadata')
    meta = dict(zip(meta_df['key'], meta_df['value']))
    
    Wc = float(meta.get('objective_cost_weight', 0.6))
    Wt = float(meta.get('objective_time_weight', 0.4))
    delays = {i: float(meta.get(f'priority_{i}_max_delay_min', 0))/(24*60) for i in range(1,6)}
    
    requests = {}
    for _, row in employees_df.iterrows():
        rid = str(row['employee_id'])
        requests[rid] = Request(
            id=rid, priority=int(row['priority']), pickup_id=rid, drop_id="office",
            earliest_pickup=time_to_minutes(row['earliest_pickup'])/1440.0,
            latest_drop=time_to_minutes(row['latest_drop'])/1440.0,
            max_delay=delays[int(row['priority'])],
            vehicle_pref=str(row['vehicle_preference']).lower(),
            max_share={'single':1, 'double':2, 'triple':3}.get(str(row['sharing_preference']).lower(), 999)
        )
    
    vehicles = {}
    for _, row in vehicles_df.iterrows():
        vid = str(row['vehicle_id'])
        vehicles[vid] = Vehicle(
            id=vid, capacity=int(row['capacity']), cost_per_km=float(row['cost_per_km']),
            speed_kmph=float(row['avg_speed_kmph']), start_id=vid,
            available_from=time_to_minutes(row['available_from'])/1440.0,
            category=str(row['category']).lower(), fuel_type=str(row['fuel_type']),
            vehicle_type=str(row['vehicle_type'])
        )
        
    solver_data = {
        'requests': requests, 'requests_list': list(requests.values()),
        'vehicles': vehicles, 'vehicles_list': list(vehicles.values()),
        'Wc': Wc, 'Wt': Wt
    }
    
    solver = EnhancedALNSSolver(solver_data)
    best_sol = solver.solve(time_limit=config.ALNS_TIME_LIMIT)
    
    # Format to algo_output.json
    output_vehicles = []
    total_cost_all = 0
    
    def fmt_time(fraction):
        tot_min = fraction * 1440.0
        h = int(tot_min // 60) % 24
        m = int(tot_min % 60)
        return f"{h:02d}:{m:02d}"

    for vid, route in best_sol.routes.items():
        if not route.legs: continue
        
        # Build raw sequence
        raw_seq = [{"location": vid, "arrival_time": route.vehicle.available_from, "departure_time": route.vehicle.available_from}]
        for leg in route.legs:
            raw_seq.append({"location": leg.location_id, "arrival_time": leg.arrival_time, "departure_time": leg.departure_time})
            
        # Merge consecutive identical locations (e.g. consecutive offices)
        merged_seq = []
        for s in raw_seq:
            if not merged_seq or s['location'] != merged_seq[-1]['location']:
                merged_seq.append(s)
            else:
                # Just update the arrival/departure times if needed
                merged_seq[-1]['departure_time'] = s['departure_time']
        
        final_seq = []
        for i, s in enumerate(merged_seq):
            fs = {
                "step": i,
                "location": s['location'],
                "arrival_time": fmt_time(s['arrival_time'])
            }
            if i < len(merged_seq) - 1:
                fs["departure_time"] = fmt_time(s['departure_time'])
            final_seq.append(fs)
        
        links = []
        for i in range(len(final_seq) - 1):
            links.append(f"{final_seq[i]['location']}_{final_seq[i+1]['location']}")
            
        output_vehicles.append({
            "vehicle_id": vid, "vehicle_type": route.vehicle.category, "capacity": route.vehicle.capacity,
            "avg_speed_kmph": route.vehicle.speed_kmph, "total_cost": round(route.total_distance * route.vehicle.cost_per_km, 2),
            "total_time_minutes": round(route.total_duration_hours * 60, 2),
            "total_steps": len(final_seq), "routes": links, "route_sequence": final_seq
        })
        total_cost_all += route.total_distance * route.vehicle.cost_per_km

    return {"vehicles": output_vehicles, "summary": {"total_cost_all_vehicles": round(total_cost_all, 2)}}
