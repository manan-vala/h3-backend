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

# ==========================================
# CONFIGURATION
# ==========================================

@dataclass
class Config:
    ALNS_ITERATIONS: int = 10000
    ALNS_TIME_LIMIT: int = 38          # 38 s for ALNS; 45 s hard cap in solver.py
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
    EARLY_TERMINATION_ITERATIONS: int = 300   # tighter than original 2000
    MIN_ACCEPTANCE_RATE: float = 0.02
    USE_LOCAL_SEARCH: bool = False            # off – too slow on 96+ employees
    LS_ITERATIONS: int = 10
    LS_PROBABILITY: float = 0.05
    K_NEAREST_VEHICLES: int = 3              # reduced from 5
    USE_NEIGHBORHOOD_FILTER: bool = True
    VEHICLE_PREF_PENALTY: float = 500.0
    SHARING_PREF_PENALTY: float = 200.0
    VEHICLE_TYPE_PENALTY: float = 300.0
    UNASSIGNED_PENALTY: float = 1000000.0

config = Config()

# ==========================================
# DISTANCE
# ==========================================

def haversine_distance(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

_MATRIX_DATA = {}

def build_matrix(matrix_edge_list):
    global _MATRIX_DATA
    _MATRIX_DATA.clear()
    for item in (matrix_edge_list or []):
        try:
            parts = item['id'].split('_')
            if len(parts) == 2:
                km = item['distance_meters'] / 1000.0
                days = item['duration_seconds'] / 86400.0
                _MATRIX_DATA[(parts[0], parts[1])] = (km, days)
                _MATRIX_DATA[(parts[1], parts[0])] = (km, days)
        except Exception:
            pass

# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass
class Location:
    lat: float
    lng: float
    id: str = ""

    def distance_and_days(self, other, speed_kmph=30.0):
        if self.id and other.id:
            entry = _MATRIX_DATA.get((self.id, other.id))
            if entry is not None:
                return entry
        km = haversine_distance(self.lat, self.lng, other.lat, other.lng)
        # Apply 1.3x road-factor to haversine (straight-line) distance
        # to be conservative when matrix entries are missing. Real road
        # distances are typically 20-40% longer than great-circle distance.
        road_km = km * 1.3
        return road_km, (road_km / speed_kmph / 24.0)

    def distance_to(self, other: 'Location') -> float:
        km, _ = self.distance_and_days(other, 30.0)
        return km

@dataclass
class Request:
    id: str
    priority: int
    pickup_loc: Location
    drop_loc: Location
    earliest_pickup: float
    latest_drop: float
    max_delay: float
    vehicle_pref: str
    max_share: int

    @property
    def latest_drop_hard(self) -> float:
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
    start_loc: Location
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

class OptimizedRoute:
    def __init__(self, vehicle: Vehicle, requests_map=None):
        self.vehicle = vehicle
        self.legs: List[RouteLeg] = []
        self.request_set: Set[str] = set()
        self.requests_map = requests_map
        self._total_distance: float = 0.0
        self._total_time: float = 0.0
        self._max_load: int = 0

    def copy(self) -> 'OptimizedRoute':
        r = OptimizedRoute(self.vehicle, self.requests_map)
        r.legs = [RouteLeg(l.request_id, l.is_pickup, l.arrival_time,
                           l.departure_time, l.cumulative_load) for l in self.legs]
        r.request_set = self.request_set.copy()
        r._total_distance = self._total_distance
        r._total_time = self._total_time
        r._max_load = self._max_load
        return r

    def set_requests_map(self, m): self.requests_map = m

    @property
    def total_distance(self): return self._total_distance
    @property
    def total_duration_hours(self): return self._total_time
    @property
    def start_time(self):
        return self.legs[0].arrival_time if self.legs else self.vehicle.available_from

    def get_max_load(self): return self._max_load
    def invalidate_cache(self): pass

    def update_incremental_stats(self):
        if not self.legs:
            self._total_distance = self._total_time = 0.0
            self._max_load = 0
            return
        total_dist = 0.0
        prev_loc = self.vehicle.start_loc
        max_load = 0
        for leg in self.legs:
            if self.requests_map and leg.request_id in self.requests_map:
                req = self.requests_map[leg.request_id]
                curr_loc = req.pickup_loc if leg.is_pickup else req.drop_loc
            else:
                self._total_distance = self._total_time = 0.0
                self._max_load = 0
                return
            total_dist += prev_loc.distance_to(curr_loc)
            prev_loc = curr_loc
            max_load = max(max_load, leg.cumulative_load)
        self._total_distance = total_dist
        self._total_time = (self.legs[-1].departure_time - self.start_time) * 24 if self.legs else 0.0
        self._max_load = max_load

    def get_pickup_groups(self):
        if not self.legs: return []
        groups, current_group, active = [], [], set()
        for leg in self.legs:
            if leg.is_pickup:
                current_group.append(leg.request_id)
                active.add(leg.request_id)
            else:
                active.discard(leg.request_id)
                if not active and current_group:
                    groups.append(current_group); current_group = []
        if current_group: groups.append(current_group)
        return groups

class Solution:
    def __init__(self):
        self.routes: Dict[str, OptimizedRoute] = {}
        self.unassigned: Set[str] = set()
        self._score_cache = None
        self.iteration_found: int = 0

    def copy(self) -> 'Solution':
        s = Solution()
        s.routes = {vid: r.copy() for vid, r in self.routes.items()}
        s.unassigned = self.unassigned.copy()
        s.iteration_found = self.iteration_found
        return s

    def shallow_copy(self) -> 'Solution':
        """
        Deep-copy routes to prevent destroy operators mutating the original.
        (A true shallow copy leads to subtle corruption bugs.)
        """
        return self.copy()

    def get_all_assigned(self) -> Set[str]:
        assigned = set()
        for route in self.routes.values():
            assigned.update(route.request_set)
        return assigned

    def validate_solution(self, request_map):
        errors = []
        seen = {}
        for vid, route in self.routes.items():
            for req_id in route.request_set:
                if req_id in seen:
                    errors.append(f"MULTI_ROUTE: {req_id}")
                else:
                    seen[req_id] = vid
        for vid, route in self.routes.items():
            if route.request_set:
                valid, msg = self._validate_route_full(route, request_map)
                if not valid:
                    errors.append(f"ROUTE[{vid}]: {msg}")
        return len(errors) == 0, errors

    def _validate_route_full(self, route, requests_map):
        if not route.legs: return True, "OK"
        if route.get_max_load() > route.vehicle.capacity:
            return False, "CAPACITY"
        pt, dt = {}, {}
        for leg in route.legs:
            req = requests_map[leg.request_id]
            if leg.is_pickup:
                if leg.departure_time < req.earliest_pickup - 1e-9:
                    return False, f"EARLY_PICKUP: {leg.request_id}"
                pt[leg.request_id] = leg.departure_time
            else:
                if leg.departure_time > req.latest_drop_hard + 1e-9:
                    return False, f"LATE_DROP: {leg.request_id}"
                dt[leg.request_id] = leg.arrival_time
        for req_id in route.request_set:
            if req_id in pt and req_id in dt:
                if pt[req_id] > dt[req_id] + 1e-9:
                    return False, f"PICKUP_AFTER_DROP: {req_id}"
        curr_load = 0
        for leg in route.legs:
            curr_load += 1 if leg.is_pickup else -1
            if curr_load != leg.cumulative_load or curr_load < 0:
                return False, "LOAD_MISMATCH"
        return True, "OK"

    def get_objective(self, request_map, Wc, Wt):
        if self._score_cache is not None:
            return self._score_cache
        is_valid, _ = self.validate_solution(request_map)
        if not is_valid:
            return float('inf')
        total_cost = total_time = 0.0
        for route in self.routes.values():
            if route.requests_map is None:
                route.set_requests_map(request_map)
            c, t = self._evaluate_route(route, request_map)
            total_cost += c; total_time += t
        penalty = len(self.unassigned) * config.UNASSIGNED_PENALTY
        score = Wc * total_cost + Wt * total_time + penalty
        self._score_cache = score
        return score

    def _evaluate_route(self, route, request_map):
        if not route.legs: return 0.0, 0.0
        op_cost = route.total_distance * route.vehicle.cost_per_km
        time_component = route.total_duration_hours * 60
        v = self._count_violations(route, request_map)
        soft = (v['vehicle_pref'] * config.VEHICLE_PREF_PENALTY +
                v['sharing_pref'] * config.SHARING_PREF_PENALTY +
                v['vehicle_type'] * config.VEHICLE_TYPE_PENALTY)
        return op_cost + soft, time_component

    def _count_violations(self, route, request_map):
        v = {'vehicle_pref': 0, 'sharing_pref': 0, 'vehicle_type': 0}
        active = {}
        for leg in route.legs:
            req = request_map[leg.request_id]
            if leg.is_pickup:
                if req.vehicle_pref == 'premium' and not route.vehicle.is_premium:
                    v['vehicle_pref'] += 1
                vt = route.vehicle.vehicle_type.lower()
                if req.max_share == 1 and vt not in ['2w', '4w']:
                    v['vehicle_type'] += 1
                elif req.max_share == 2 and vt == 'van':
                    v['vehicle_type'] += 1
                active[leg.request_id] = req
            else:
                active.pop(leg.request_id, None)
            if active:
                min_share = min(r.max_share for r in active.values())
                if leg.cumulative_load > min_share:
                    v['sharing_pref'] += leg.cumulative_load - min_share
        return v

    def get_served_count(self):
        return len(self.get_all_assigned())

    def invalidate_cache(self):
        self._score_cache = None

# ==========================================
# SOLVER
# ==========================================

class EnhancedALNSSolver:
    def __init__(self, data):
        self.requests      = data['requests']
        self.requests_list = data['requests_list']
        self.vehicles      = data['vehicles']
        self.vehicles_list = data['vehicles_list']
        self.Wc = data['Wc']
        self.Wt = data['Wt']

        self._vehicle_request_distances = self._compute_vehicle_request_distances()
        self.request_vehicle_map = self._precompute_feasible_vehicles()

        self.destroy_ops = [
            self.destroy_random, self.destroy_worst_cost, self.destroy_shaw,
            self.destroy_sharing_violation, self.destroy_time_window,
            self.destroy_route, self.destroy_zone,
        ]
        self.n_destroy = len(self.destroy_ops)
        self.destroy_weights = np.ones(self.n_destroy)
        self.destroy_scores  = np.zeros(self.n_destroy)
        self.destroy_usage   = np.zeros(self.n_destroy)
        self.current_destroy_min = config.DESTROY_RATE_MIN
        self.current_destroy_max = config.DESTROY_RATE_MAX
        self.stagnation_count    = 0
        self.historical_costs    = defaultdict(float)
        self.temperature = config.TEMPERATURE_START
        self.stats = {'iterations': 0, 'improvements': 0,
                      'acceptances': 0, 'rejections': 0, 'ls_improvements': 0}

    # ── Distance helpers ──────────────────────────────────────────────────────

    def _compute_vehicle_request_distances(self):
        d = {}
        for vid, v in self.vehicles.items():
            d[vid] = {rid: v.start_loc.distance_to(r.pickup_loc)
                      for rid, r in self.requests.items()}
        return d

    def _precompute_feasible_vehicles(self):
        m = {}
        for rid in self.requests:
            ranked = sorted(self.vehicles.keys(),
                            key=lambda vid: self._vehicle_request_distances[vid][rid])
            m[rid] = ranked[:config.K_NEAREST_VEHICLES]
        return m

    # ── Route validation ──────────────────────────────────────────────────────

    def _validate_route(self, route):
        if not route.legs: return True, "OK"
        if route.get_max_load() > route.vehicle.capacity:
            return False, "CAPACITY"
        pt, dt = {}, {}
        for leg in route.legs:
            req = self.requests[leg.request_id]
            if leg.is_pickup:
                if leg.departure_time < req.earliest_pickup - 1e-9:
                    return False, "EARLY_PICKUP"
                pt[leg.request_id] = leg.departure_time
            else:
                if leg.departure_time > req.latest_drop_hard + 1e-9:
                    return False, "LATE_DROP"
                dt[leg.request_id] = leg.arrival_time
        for req_id in route.request_set:
            if req_id in pt and req_id in dt and pt[req_id] > dt[req_id] + 1e-9:
                return False, "PICKUP_AFTER_DROP"
        return True, "OK"

    # ── Insertion helpers ─────────────────────────────────────────────────────

    def _calculate_insertion_delta(self, route, req, p_idx, d_idx):
        """Delta cost from inserting req at (p_idx, d_idx) — O(1)."""
        legs = route.legs
        v    = route.vehicle
        delta = 0.0

        def loc_at(i):
            if i < 0: return v.start_loc
            leg = legs[i]; r = self.requests[leg.request_id]
            return r.pickup_loc if leg.is_pickup else r.drop_loc

        p_loc = req.pickup_loc
        d_loc = req.drop_loc

        # Pickup insertion cost
        prev_p = loc_at(p_idx - 1)
        if p_idx < len(legs):
            next_p = loc_at(p_idx)
            km1, _ = prev_p.distance_and_days(p_loc, v.speed_kmph)
            km2, _ = p_loc.distance_and_days(next_p, v.speed_kmph)
            km3, _ = prev_p.distance_and_days(next_p, v.speed_kmph)
            delta += km1 + km2 - km3
        else:
            km, _ = prev_p.distance_and_days(p_loc, v.speed_kmph)
            delta += km

        # Drop insertion cost (account for pickup being inserted)
        adj = d_idx
        if d_idx == p_idx + 1:
            prev_d = p_loc
        elif d_idx - 1 < len(legs):
            prev_d = loc_at(d_idx - 2) if d_idx >= 2 else v.start_loc
        else:
            prev_d = p_loc

        if d_idx <= len(legs):
            next_d = loc_at(d_idx - 1) if d_idx - 1 < len(legs) else None
            if next_d:
                km1, _ = prev_d.distance_and_days(d_loc, v.speed_kmph)
                km2, _ = d_loc.distance_and_days(next_d, v.speed_kmph)
                km3, _ = prev_d.distance_and_days(next_d, v.speed_kmph)
                delta += km1 + km2 - km3
            else:
                km, _ = prev_d.distance_and_days(d_loc, v.speed_kmph)
                delta += km
        else:
            km, _ = prev_d.distance_and_days(d_loc, v.speed_kmph)
            delta += km

        return delta * v.cost_per_km

    def _quick_feasibility_check(self, route, req, p_idx, d_idx):
        """O(n) feasibility pass without copying the route."""
        legs = route.legs
        v    = route.vehicle
        n_orig    = len(legs)

        if p_idx > 0:
            prev_leg = legs[p_idx - 1]
            curr_time = prev_leg.departure_time
            curr_load = prev_leg.cumulative_load
            lr = self.requests[prev_leg.request_id]
            prev_loc = lr.pickup_loc if prev_leg.is_pickup else lr.drop_loc
            orig_i = p_idx
        else:
            curr_time = v.available_from
            curr_load = 0
            prev_loc = v.start_loc
            orig_i = 0

        for i in range(p_idx, n_orig + 2):
            if i == p_idx:
                _, tt = prev_loc.distance_and_days(req.pickup_loc, v.speed_kmph)
                arrival    = curr_time + tt
                departure  = max(arrival, req.earliest_pickup)
                curr_load += 1
                if curr_load > v.capacity: return False
                curr_time = departure; prev_loc = req.pickup_loc; continue
            if i == d_idx:
                _, tt = prev_loc.distance_and_days(req.drop_loc, v.speed_kmph)
                arrival   = curr_time + tt
                departure = arrival
                if departure > req.latest_drop_hard + 1e-9: return False
                curr_load -= 1
                curr_time = departure; prev_loc = req.drop_loc; continue
            if orig_i < n_orig:
                leg    = legs[orig_i]; orig_i += 1
                lr     = self.requests[leg.request_id]
                c_loc  = lr.pickup_loc if leg.is_pickup else lr.drop_loc
                _, tt  = prev_loc.distance_and_days(c_loc, v.speed_kmph)
                arrival = curr_time + tt
                if leg.is_pickup:
                    departure = max(arrival, lr.earliest_pickup)
                else:
                    departure = arrival
                    if departure > lr.latest_drop_hard + 1e-9: return False
                curr_load += 1 if leg.is_pickup else -1
                if curr_load > v.capacity or curr_load < 0: return False
                curr_time = departure; prev_loc = c_loc

        return True

    def _insert_request(self, route, req, pickup_idx, drop_idx):
        new_legs      = []
        curr_time     = route.vehicle.available_from
        curr_load     = 0
        original_legs = route.legs[:]
        n_orig        = len(original_legs)
        orig_i        = 0

        def prev_loc_from_legs(legs):
            if not legs: return route.vehicle.start_loc
            pl = legs[-1]; pr = self.requests[pl.request_id]
            return pr.pickup_loc if pl.is_pickup else pr.drop_loc

        for i in range(n_orig + 2):
            if i == pickup_idx:
                prev_loc = prev_loc_from_legs(new_legs)
                _, tt    = prev_loc.distance_and_days(req.pickup_loc, route.vehicle.speed_kmph)
                arrival  = curr_time + tt
                departure = max(arrival, req.earliest_pickup)
                curr_load += 1
                if curr_load > route.vehicle.capacity: return None
                new_legs.append(RouteLeg(req.id, True, arrival, departure, curr_load))
                curr_time = departure; continue

            if i == drop_idx:
                if not new_legs: return None
                prev_loc  = prev_loc_from_legs(new_legs)
                _, tt     = prev_loc.distance_and_days(req.drop_loc, route.vehicle.speed_kmph)
                arrival   = curr_time + tt
                departure = arrival
                if departure > req.latest_drop_hard + 1e-9: return None
                curr_load -= 1
                new_legs.append(RouteLeg(req.id, False, arrival, departure, curr_load))
                curr_time = departure; continue

            if orig_i < n_orig:
                leg = original_legs[orig_i]; orig_i += 1
                prev_loc = prev_loc_from_legs(new_legs)
                lr       = self.requests[leg.request_id]
                curr_loc = lr.pickup_loc if leg.is_pickup else lr.drop_loc
                _, tt    = prev_loc.distance_and_days(curr_loc, route.vehicle.speed_kmph)
                arrival  = curr_time + tt
                if leg.is_pickup:
                    departure = max(arrival, lr.earliest_pickup)
                else:
                    departure = arrival
                    if departure > lr.latest_drop_hard + 1e-9: return None
                curr_load += 1 if leg.is_pickup else -1
                if curr_load > route.vehicle.capacity or curr_load < 0: return None
                new_legs.append(RouteLeg(leg.request_id, leg.is_pickup, arrival, departure, curr_load))
                curr_time = departure

        route.legs = new_legs
        route.request_set.add(req.id)
        route.update_incremental_stats()
        return route

    def _find_best_insertion(self, route, req):
        """
        Delta-evaluation insertion search.
        Uses _quick_feasibility_check (O(n)) to prune before doing a full copy.
        Falls back to a simple single-position search for empty routes.
        """
        if not route.legs:
            trial = self._insert_request(route.copy(), req, 0, 1)
            if trial:
                valid, _ = self._validate_route(trial)
                if valid:
                    return (0, 1, 0.0)
            return None

        n = len(route.legs)
        best_score    = float('inf')
        best_positions = None

        for p_idx in range(n + 1):
            # Prune: if the leg before p_idx departed past req's drop deadline, stop
            if p_idx > 0:
                if route.legs[p_idx - 1].departure_time > req.latest_drop_hard + 1e-9:
                    break

            for d_idx in range(p_idx + 1, n + 2):
                delta = self._calculate_insertion_delta(route, req, p_idx, d_idx)
                if delta >= best_score * 1.5:
                    continue
                if not self._quick_feasibility_check(route, req, p_idx, d_idx):
                    continue

                trial = self._insert_request(route.copy(), req, p_idx, d_idx)
                if trial:
                    valid, _ = self._validate_route(trial)
                    if valid and delta < best_score:
                        best_score = delta
                        best_positions = (p_idx, d_idx)

        return (*best_positions, best_score) if best_positions else None

    def _rebuild_route(self, vehicle, legs):
        route = OptimizedRoute(vehicle, self.requests)
        route.request_set = {leg.request_id for leg in legs}
        curr_time = vehicle.available_from
        curr_load = 0
        new_legs  = []
        prev_loc  = vehicle.start_loc
        for leg in legs:
            req   = self.requests[leg.request_id]
            c_loc = req.pickup_loc if leg.is_pickup else req.drop_loc
            _, tt = prev_loc.distance_and_days(c_loc, vehicle.speed_kmph)
            arrival = curr_time + tt
            if leg.is_pickup:
                departure = max(arrival, req.earliest_pickup)
                curr_load += 1
            else:
                departure = arrival
                if departure > req.latest_drop_hard + 1e-9: return None
                curr_load -= 1
            if curr_load > vehicle.capacity or curr_load < 0: return None
            new_legs.append(RouteLeg(leg.request_id, leg.is_pickup, arrival, departure, curr_load))
            curr_time = departure; prev_loc = c_loc
        route.legs = new_legs
        route.update_incremental_stats()
        return route

    def _calculate_route_cost(self, route):
        return route.total_distance * route.vehicle.cost_per_km

    # ── Remove helper ─────────────────────────────────────────────────────────

    def _remove_requests(self, solution, req_ids):
        for req_id in req_ids:
            for vid, route in solution.routes.items():
                if req_id in route.request_set:
                    new_legs = [l for l in route.legs if l.request_id != req_id]
                    rebuilt  = self._rebuild_route(route.vehicle, new_legs)
                    if rebuilt:
                        solution.routes[vid] = rebuilt
                    else:
                        # Safe fallback – recalculate loads without touching other routes
                        route.request_set.discard(req_id)
                        route.legs = [l for l in route.legs if l.request_id != req_id]
                        # Recalculate cumulative loads
                        cl = 0
                        for l in route.legs:
                            cl += 1 if l.is_pickup else -1
                            l.cumulative_load = cl
                        route.update_incremental_stats()
                    solution.unassigned.add(req_id)
                    break

    # ── Destroy operators ─────────────────────────────────────────────────────

    def destroy_random(self, solution, num_remove):
        new_sol  = solution.shallow_copy()
        assigned = list(new_sol.get_all_assigned())
        to_remove = random.sample(assigned, min(num_remove, len(assigned)))
        self._remove_requests(new_sol, to_remove)
        return new_sol

    def destroy_worst_cost(self, solution, num_remove):
        new_sol = solution.shallow_copy()
        costs   = sorted([(rid, self.historical_costs.get(rid, 0))
                          for rid in new_sol.get_all_assigned()],
                         key=lambda x: x[1], reverse=True)
        self._remove_requests(new_sol, [x[0] for x in costs[:num_remove]])
        return new_sol

    def destroy_shaw(self, solution, num_remove):
        new_sol  = solution.shallow_copy()
        assigned = list(new_sol.get_all_assigned())
        if not assigned: return new_sol
        seed  = random.choice(assigned)
        sr    = self.requests[seed]
        cands = sorted(
            [(rid, sr.pickup_loc.distance_to(self.requests[rid].pickup_loc) +
              0.1 * abs(sr.earliest_pickup - self.requests[rid].earliest_pickup) * 1440)
             for rid in assigned if rid != seed], key=lambda x: x[1])
        to_remove = [seed] + [r for r, _ in cands[:num_remove - 1]]
        self._remove_requests(new_sol, to_remove)
        return new_sol

    def destroy_sharing_violation(self, solution, num_remove):
        new_sol = solution.shallow_copy()
        cands   = [req_id
                   for vid, route in new_sol.routes.items()
                   for req_id in route.request_set
                   if self.requests[req_id].max_share < route.get_max_load()]
        to_remove = random.sample(cands, min(num_remove, len(cands))) if cands else []
        self._remove_requests(new_sol, to_remove)
        return new_sol

    def destroy_time_window(self, solution, num_remove):
        new_sol = solution.shallow_copy()
        ranked  = sorted([(rid, self.requests[rid].time_window_width)
                          for rid in new_sol.get_all_assigned()], key=lambda x: x[1])
        self._remove_requests(new_sol, [r for r, _ in ranked[:num_remove]])
        return new_sol

    def destroy_route(self, solution, num_remove):
        new_sol = solution.shallow_copy()
        ranked  = sorted(
            [(vid, route.total_distance / max(len(route.request_set), 1),
              list(route.request_set))
             for vid, route in new_sol.routes.items() if route.request_set],
            key=lambda x: x[1], reverse=True)
        to_remove = []
        for _, _, reqs in ranked:
            if len(to_remove) >= num_remove: break
            to_remove.extend(reqs[:num_remove - len(to_remove)])
        self._remove_requests(new_sol, to_remove)
        return new_sol

    def destroy_zone(self, solution, num_remove):
        new_sol  = solution.shallow_copy()
        assigned = list(new_sol.get_all_assigned())
        if not assigned: return new_sol
        seed  = random.choice(assigned)
        sr    = self.requests[seed]
        cands = sorted([(rid, sr.pickup_loc.distance_to(self.requests[rid].pickup_loc))
                        for rid in assigned if rid != seed], key=lambda x: x[1])
        to_remove = [seed] + [r for r, _ in cands[:num_remove - 1]]
        self._remove_requests(new_sol, to_remove)
        return new_sol

    # ── Repair ────────────────────────────────────────────────────────────────

    def repair_regret(self, solution, noise_level=0.0):
        new_sol = solution.shallow_copy()
        pending = list(new_sol.unassigned)
        new_sol.unassigned = set()
        pending.sort(key=lambda rid: (-self.requests[rid].priority,
                                      self.requests[rid].earliest_pickup))

        for _ in range(len(pending) * 3):
            if not pending: break
            regrets = []
            for req_id in pending:
                req         = self.requests[req_id]
                insertions  = []
                candidates  = list(self.request_vehicle_map.get(req_id, []))
                remaining   = [v for v in self.vehicles if v not in candidates]
                for vid in candidates + remaining:
                    if vid not in new_sol.routes: continue
                    res = self._find_best_insertion(new_sol.routes[vid], req)
                    if res:
                        p, d, score = res
                        noisy = score * (1 + random.uniform(-noise_level, noise_level))
                        insertions.append((noisy, vid, p, d))
                if len(insertions) >= 2:
                    insertions.sort(key=lambda x: x[0])
                    regrets.append((insertions[1][0] - insertions[0][0], req_id, insertions[0]))
                elif insertions:
                    regrets.append((float('inf'), req_id, insertions[0]))

            if not regrets: break
            regrets.sort(key=lambda x: x[0], reverse=True)
            inserted = False
            for _, req_id, ins in regrets:
                score, vid, p_idx, d_idx = ins
                new_route = self._insert_request(
                    new_sol.routes[vid].copy(), self.requests[req_id], p_idx, d_idx)
                if new_route:
                    valid, _ = self._validate_route(new_route)
                    if valid:
                        new_sol.routes[vid] = new_route
                        pending.remove(req_id)
                        inserted = True; break
            if not inserted:
                break

        new_sol.unassigned.update(pending)
        new_sol.invalidate_cache()
        return new_sol

    # ── Historical costs ──────────────────────────────────────────────────────

    def _update_historical_costs(self, solution):
        for vid, route in solution.routes.items():
            if not route.legs: continue
            rc = self._calculate_route_cost(route)
            n  = len(route.request_set)
            if n > 0:
                avg = rc / n
                for req_id in route.request_set:
                    old = self.historical_costs.get(req_id, avg)
                    self.historical_costs[req_id] = 0.7 * old + 0.3 * avg

    # ── Local search (optional, off by default) ───────────────────────────────

    def local_search(self, solution):
        if not config.USE_LOCAL_SEARCH: return solution
        best_sol = solution.copy()
        best_obj = best_sol.get_objective(self.requests, self.Wc, self.Wt)
        improved = True; itr = 0
        while improved and itr < config.LS_ITERATIONS:
            improved = False; itr += 1
            for vid in list(best_sol.routes.keys()):
                route = best_sol.routes[vid]
                if len(route.legs) < 4: continue
                new_route = self._two_opt(route)
                if new_route:
                    ts = best_sol.copy()
                    ts.routes[vid] = new_route
                    obj = ts.get_objective(self.requests, self.Wc, self.Wt)
                    if obj < best_obj:
                        best_sol, best_obj = ts, obj
                        improved = True
                        self.stats['ls_improvements'] += 1; break
        return best_sol

    def _two_opt(self, route):
        if len(route.legs) < 4: return None
        best_route, best_cost, improved = route, self._calculate_route_cost(route), False
        n = len(route.legs)
        for i in range(n - 1):
            for j in range(i + 2, n):
                new_legs = route.legs[:i+1] + route.legs[i+1:j+1][::-1] + route.legs[j+1:]
                nr = self._rebuild_route(route.vehicle, new_legs)
                if nr:
                    valid, _ = self._validate_route(nr)
                    if valid:
                        cost = self._calculate_route_cost(nr)
                        if cost < best_cost:
                            best_route, best_cost, improved = nr, cost, True
        return best_route if improved else None

    # ── Initial solution ──────────────────────────────────────────────────────

    def initial_solution(self):
        """
        Fast greedy construction:
          1. Try APPEND-AT-END (O(1)) for each candidate vehicle first.
          2. Only fall back to full _find_best_insertion if append fails.
        This reduces initial construction from O(n²) copies to O(n).
        """
        sol = Solution()
        for v in self.vehicles.values():
            sol.routes[v.id] = OptimizedRoute(v, self.requests)
        sol.unassigned = set(self.requests.keys())

        sorted_reqs = sorted(self.requests_list,
                              key=lambda r: (r.time_window_width, r.priority))

        for req in sorted_reqs:
            candidates = list(self.request_vehicle_map.get(req.id, []))
            remaining  = [v for v in self.vehicles if v not in candidates]
            order      = candidates + remaining
            placed     = False

            # ── Fast path: append at end ──────────────────────────────────────
            for vid in order:
                route = sol.routes[vid]
                n = len(route.legs)
                nr = self._insert_request(route.copy(), req, n, n + 1)
                if nr:
                    valid, _ = self._validate_route(nr)
                    if valid:
                        sol.routes[vid] = nr
                        sol.unassigned.discard(req.id)
                        placed = True; break

            # ── Fallback: full insertion search ───────────────────────────────
            if not placed:
                best_v, best_pos, best_score = None, None, float('inf')
                for vid in order:
                    res = self._find_best_insertion(sol.routes[vid], req)
                    if res:
                        p, d, score = res
                        if score < best_score:
                            best_score, best_v, best_pos = score, vid, (p, d)
                if best_v:
                    nr = self._insert_request(sol.routes[best_v].copy(), req, *best_pos)
                    if nr:
                        valid, _ = self._validate_route(nr)
                        if valid:
                            sol.routes[best_v] = nr
                            sol.unassigned.discard(req.id)

        print(f"[ALNS] Initial: served={sol.get_served_count()}/{len(self.requests_list)}")
        return sol

    # ── Main solve loop ───────────────────────────────────────────────────────

    def solve(self, time_limit=60, result_ref=None, format_fn=None):
        print(f"\n[ALNS] Starting (time_limit={time_limit}s)")
        self.current_solution = self.initial_solution()
        self._update_historical_costs(self.current_solution)
        if config.USE_LOCAL_SEARCH:
            self.current_solution = self.local_search(self.current_solution)
        self.global_best_solution = self.current_solution.copy()
        # ── Publish initial solution immediately so timeout recovery has something
        self._write_result(self.global_best_solution, result_ref, format_fn)

        current_obj    = self.current_solution.get_objective(self.requests, self.Wc, self.Wt)
        global_best_obj = current_obj
        start_time     = time.time()
        iteration = last_improvement = acceptance_count = 0
        max_iterations = config.ALNS_ITERATIONS

        while iteration < max_iterations and time.time() - start_time < time_limit - 1:
            iteration += 1

            if config.DESTROY_RATE_ADAPTIVE:
                if self.stagnation_count > 500:
                    self.current_destroy_min = min(0.3, self.current_destroy_min * 1.1)
                    self.current_destroy_max = min(0.5, self.current_destroy_max * 1.1)
                elif self.stagnation_count < 100:
                    self.current_destroy_min = max(config.DESTROY_RATE_MIN,
                                                   self.current_destroy_min * 0.95)
                    self.current_destroy_max = max(config.DESTROY_RATE_MAX,
                                                   self.current_destroy_max * 0.95)

            probs  = self.destroy_weights / self.destroy_weights.sum()
            op_idx = np.random.choice(self.n_destroy, p=probs)
            num_remove = max(1, int(len(self.requests) *
                                    random.uniform(self.current_destroy_min,
                                                   self.current_destroy_max)))

            temp_sol = self.destroy_ops[op_idx](self.current_solution, num_remove)
            noise    = max(0.0, 0.1 * (1 - iteration / max_iterations))
            temp_sol = self.repair_regret(temp_sol, noise_level=noise)

            if config.USE_LOCAL_SEARCH and random.random() < config.LS_PROBABILITY:
                temp_sol = self.local_search(temp_sol)

            # Lightweight validity gate (skip full validate_solution in loop)
            all_ok = all(self._validate_route(r)[0]
                         for r in temp_sol.routes.values() if r.legs)
            if not all_ok:
                self.stats['rejections'] += 1; continue

            self._update_historical_costs(temp_sol)
            temp_obj = temp_sol.get_objective(self.requests, self.Wc, self.Wt)
            if temp_obj == float('inf'):
                self.stats['rejections'] += 1; continue

            delta    = temp_obj - current_obj
            accepted = False
            score    = config.SCORE_WORSE

            if delta < 0:
                accepted, score = True, config.SCORE_BETTER
            elif random.random() < math.exp(-delta / max(self.temperature, 0.001)):
                accepted, score = True, config.SCORE_EQUAL
                acceptance_count += 1

            if accepted:
                self.current_solution, current_obj = temp_sol, temp_obj
                self.stats['acceptances'] += 1
                self.stagnation_count = max(0, self.stagnation_count - 10)
                if current_obj < global_best_obj:
                    global_best_obj     = current_obj
                    self.global_best_solution = self.current_solution.copy()
                    self.global_best_solution.iteration_found = iteration
                    score               = config.SCORE_GLOBAL_BEST
                    last_improvement    = iteration
                    self.stats['improvements'] += 1
                    self.stagnation_count = 0
                    # ── Publish improved result for timeout recovery
                    self._write_result(self.global_best_solution, result_ref, format_fn)
            else:
                self.stats['rejections'] += 1
                self.stagnation_count += 1

            self.destroy_scores[op_idx] += score
            self.destroy_usage[op_idx]  += 1

            if iteration % config.WEIGHT_UPDATE_INTERVAL == 0:
                for i in range(self.n_destroy):
                    if self.destroy_usage[i] > 0:
                        self.destroy_weights[i] = max(
                            0.1, config.WEIGHT_DECAY * self.destroy_weights[i] +
                            (1 - config.WEIGHT_DECAY) *
                            self.destroy_scores[i] / self.destroy_usage[i])
                        self.destroy_scores[i] = self.destroy_usage[i] = 0
                print(f"[ALNS] iter={iteration:04d} best={global_best_obj:.2f} "
                      f"served={self.global_best_solution.get_served_count()}"
                      f"/{len(self.requests_list)}")

            self.temperature = (config.TEMPERATURE_START *
                                (config.TEMPERATURE_END / config.TEMPERATURE_START) **
                                (iteration / max_iterations))

            if iteration - last_improvement > config.EARLY_TERMINATION_ITERATIONS:
                print(f"[ALNS] Early stop: no improvement for "
                      f"{config.EARLY_TERMINATION_ITERATIONS} iters")
                break
            if iteration % 1000 == 0 and iteration > 0:
                if acceptance_count / 1000 < config.MIN_ACCEPTANCE_RATE:
                    print("[ALNS] Early stop: low acceptance rate")
                    break
                acceptance_count = 0

        elapsed = time.time() - start_time
        served  = self.global_best_solution.get_served_count()
        print(f"[ALNS] Done ({iteration} iters, {elapsed:.1f}s): "
              f"served={served}/{len(self.requests_list)}, obj={global_best_obj:.2f}")
        return self.global_best_solution

    def _write_result(self, solution, result_ref, format_fn):
        """Thread-safe best-so-far result update for timeout recovery."""
        if result_ref is not None and format_fn is not None:
            try:
                result_ref[0] = format_fn(solution)
            except Exception:
                pass  # never crash the solve loop

# ==========================================
# TIME HELPERS
# ==========================================

def time_to_fraction(value):
    if pd.isna(value): return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        # Values > 1 are minutes (e.g. 480 = 08:00); values ≤ 1 are already fractions
        return v / 1440.0 if v > 1.0 else v
    if isinstance(value, dt_time):
        return (value.hour + value.minute / 60 + value.second / 3600) / 24
    if isinstance(value, datetime):
        return (value.hour + value.minute / 60 + value.second / 3600) / 24
    if isinstance(value, str):
        try:
            return float(value) / 1440.0
        except ValueError:
            for fmt in ["%H:%M:%S", "%H:%M"]:
                try:
                    dt = datetime.strptime(value.strip(), fmt)
                    return (dt.hour + dt.minute / 60) / 24
                except:
                    continue
    return 0.0

# ==========================================
# BRIDGE: solve_alns (called by solver.py)
# ==========================================

def solve_alns(input_data, matrix_edge_list, file_bytes, _result_ref=None):
    build_matrix(matrix_edge_list)

    excel_file   = BytesIO(file_bytes)
    employees_df = pd.read_excel(excel_file, sheet_name='employees', engine='openpyxl')
    vehicles_df  = pd.read_excel(excel_file, sheet_name='vehicles', engine='openpyxl')
    meta_df      = pd.read_excel(excel_file, sheet_name='metadata', engine='openpyxl')
    meta   = dict(zip(meta_df['key'], meta_df['value']))
    Wc     = float(meta.get('objective_cost_weight', 0.6))
    Wt     = float(meta.get('objective_time_weight', 0.4))
    delays = {i: float(meta.get(f'priority_{i}_max_delay_min', 0)) / (24 * 60)
              for i in range(1, 6)}

    sharing_map = {'single': 1, 'double': 2, 'triple': 3, 'any': 999}
    requests_list = []
    for _, row in employees_df.iterrows():
        eid = str(row['employee_id']).strip()
        try:
            plat = float(row['pickup_lat']); plng = float(row['pickup_lng'])
            dlat = float(row['drop_lat']);   dlng = float(row['drop_lng'])
            pri  = int(float(row['priority']))
        except:
            continue
        ep   = time_to_fraction(row['earliest_pickup'])
        ld   = time_to_fraction(row['latest_drop'])
        pref = str(row.get('sharing_preference', 'any')).lower().strip()
        vpref = str(row.get('vehicle_preference', 'any')).lower().strip()
        if vpref in ['nan', '']: vpref = 'any'
        requests_list.append(Request(
            id=eid, priority=pri,
            pickup_loc=Location(plat, plng, eid), drop_loc=Location(dlat, dlng, "office"),
            earliest_pickup=ep, latest_drop=ld, max_delay=delays.get(pri, 0),
            vehicle_pref=vpref, max_share=sharing_map.get(pref, 999)
        ))

    vehicles_list = []
    for _, row in vehicles_df.iterrows():
        vid = str(row['vehicle_id']).strip()
        try:
            lat = float(row['current_lat']); lng = float(row['current_lng'])
            cap = int(float(row['capacity']))
            cpk = float(row['cost_per_km']); spd = float(row['avg_speed_kmph'])
        except:
            continue
        if cpk <= 0: cpk = 10.0
        avail = time_to_fraction(row['available_from'])
        vehicles_list.append(Vehicle(
            id=vid, capacity=cap, cost_per_km=cpk, speed_kmph=spd,
            start_loc=Location(lat, lng, vid), available_from=avail,
            category=str(row.get('category', 'standard')).lower().strip(),
            fuel_type=str(row.get('fuel_type', 'petrol')),
            vehicle_type=str(row.get('vehicle_type', '4W')).strip()
        ))

    solver_data = {
        'requests':      {r.id: r for r in requests_list},
        'requests_list': requests_list,
        'vehicles':      {v.id: v for v in vehicles_list},
        'vehicles_list': vehicles_list,
        'Wc': Wc, 'Wt': Wt,
    }
    solver   = EnhancedALNSSolver(solver_data)

    def fmt_time(frac):
        m = frac * 1440.0
        return f"{int(m//60)%24:02d}:{int(m%60):02d}"

    def _format_solution(sol):
        """Convert a Solution object to the route_sequence output dict."""
        out_vehicles = []
        total_cost   = 0.0
        for vid, route in sol.routes.items():
            if not route.legs: continue
            raw_seq = [{"location": vid,
                        "arrival_time":   route.vehicle.available_from,
                        "departure_time": route.vehicle.available_from}]
            for leg in route.legs:
                raw_seq.append({"location": leg.request_id if leg.is_pickup else "office",
                                 "arrival_time":   leg.arrival_time,
                                 "departure_time": leg.departure_time})
            merged = []
            for s in raw_seq:
                if not merged or s['location'] != merged[-1]['location']:
                    merged.append(s)
                else:
                    merged[-1]['departure_time'] = s['departure_time']
            final_seq = []
            for i, s in enumerate(merged):
                fs = {"step": i, "location": s['location'],
                      "arrival_time": fmt_time(s['arrival_time'])}
                if i < len(merged) - 1:
                    fs["departure_time"] = fmt_time(s['departure_time'])
                final_seq.append(fs)
            links = [f"{final_seq[i]['location']}_{final_seq[i+1]['location']}"
                     for i in range(len(final_seq) - 1)]
            rc = route.total_distance * route.vehicle.cost_per_km
            total_cost += rc
            out_vehicles.append({
                "vehicle_id": vid, "vehicle_type": route.vehicle.category,
                "capacity": route.vehicle.capacity,
                "avg_speed_kmph": route.vehicle.speed_kmph,
                "total_cost": round(rc, 2),
                "total_time_minutes": round(route.total_duration_hours * 60, 2),
                "total_steps": len(final_seq), "routes": links,
                "route_sequence": final_seq,
            })
        return {"vehicles": out_vehicles,
                "summary": {"total_cost_all_vehicles": round(total_cost, 2)}}

    best_sol = solver.solve(
        time_limit=config.ALNS_TIME_LIMIT,
        result_ref=_result_ref,
        format_fn=_format_solution,
    )
    return _format_solution(best_sol)

# ==========================================
# STANDALONE MAIN
# ==========================================

def main():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
        file_path = filedialog.askopenfilename(
            title="Select Excel File", filetypes=[("Excel files", "*.xlsx *.xls")])
        root.destroy()
    except Exception:
        file_path = input("Enter Excel file path: ").strip()
    if not file_path:
        print("No file selected."); return
    with open(file_path, 'rb') as f:
        file_bytes = f.read()
    result = solve_alns({}, [], file_bytes)
    out = Path(file_path).parent / f"{Path(file_path).stem}_result.json"
    with open(out, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n✅ Saved to {out}")
    print(f"Vehicles used: {len(result['vehicles'])}")
    print(f"Total cost: {result['summary']['total_cost_all_vehicles']}")

if __name__ == "__main__":
    main()