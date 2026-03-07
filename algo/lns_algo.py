import json
import random
import time
import math
import os
from collections import defaultdict
from .lns_utils import load_data_from_bytes, Employee, Vehicle, DistanceMatrix
from .lns_simulator import RouteSimulator
from .lns_local_search import LocalSearch

W1 = 1 # Cost penalty
W2 = 1 # Time penalty
W3 = 2000 # Sharing penalty
W4 = 2000 # Vehicle Premium penalty
W5 = 20000 # Unassigned penalty
W6 = 10000000 # Hard Constraint violation penalty

class LNSOptimizer:
    def __init__(self, file_bytes, matrix_edge_list, initial_sol=None):
        self.employees, self.vehicles = load_data_from_bytes(file_bytes)
        self.dist_matrix = DistanceMatrix(matrix_edge_list)
        self.simulator = RouteSimulator(self.employees, self.vehicles, self.dist_matrix, allow_violations=True)
        
        # Default Weights - penalized heavily to prevent "soft" violations
        self.weights_dict = {'w1': W1, 'w2': W2, 'w3': W3, 'w4': W4}
        
        self.w_cost = self.weights_dict['w1']
        self.w_time = self.weights_dict['w2']
        self.w_sharing = self.weights_dict['w3']
        self.w_vehicle = self.weights_dict['w4']
        
        self.local_search = LocalSearch(self.employees, self.vehicles, self.weights_dict, self.simulator)
        self.current_routes = initial_sol if initial_sol else {}
        self.best_routes = self._copy_routes(self.current_routes)
        self.best_score = float('inf')
        
        _, score, _ = self.evaluate(self.current_routes)
        self.best_score = score
        # print(f"Initial Score: {score:.2f}")

    def _copy_routes(self, routes):
        """Manual shallow copy — safe because leaf values are immutable strings."""
        return {k: [list(g) for g in v] for k, v in routes.items()}

    def _copy_groups(self, groups):
        """Copy a single vehicle's groups list."""
        return [list(g) for g in groups]

    def evaluate(self, routes):
        total_cost = 0.0
        total_penalty = 0.0
        served_count = 0
        total_hard_violations = 0
        metrics_summary = {'cost': 0, 'time': 0, 'sharing': 0, 'vehicle': 0, 'violation': 0}
        
        for veh_id, groups in routes.items():
            if not groups: continue
            feasible, m = self.simulator.simulate_vehicle(veh_id, groups)
            served_count += sum(len(g) for g in groups)
            total_hard_violations += m.get('hard_violation_count', 0)
            
            cost_comp = (self.w_cost * m['total_cost'] + 
                         self.w_time * m['total_time'] + 
                         self.w_sharing * m['sharing_penalty'] + 
                         self.w_vehicle * m['vehicle_penalty'])
            
            total_cost += cost_comp
            total_penalty += m['violation_penalty']
            
            metrics_summary['cost'] += m['total_cost']
            metrics_summary['time'] += m['total_time']
            metrics_summary['sharing'] += m['sharing_penalty']
            metrics_summary['vehicle'] += m['vehicle_penalty']
            metrics_summary['violation'] += m['violation_penalty']

        all_emps = set(self.employees.keys())
        assigned = set()
        for groups in routes.values():
            for g in groups: assigned.update(g)
        
        unassigned_count = len(all_emps) - len(assigned)
        final_score = total_cost + total_penalty + unassigned_count * W5
        final_score += total_hard_violations * W6
        metrics_summary['hard_vio_count'] = total_hard_violations
        
        return served_count, final_score, metrics_summary

    # ========================================================================
    # DESTRUCTION OPERATORS (RUIN)
    # ========================================================================
    
    def destroy_random(self, routes, num_remove):
        new_routes = self._copy_routes(routes)
        assigned = []
        for v, groups in new_routes.items():
            for g_i, group in enumerate(groups):
                for p_i, eid in enumerate(group): assigned.append(eid)
        if not assigned: return new_routes, []
        removed = random.sample(assigned, min(num_remove, len(assigned)))
        return self._rebuild_without(new_routes, set(removed)), removed

    def destroy_spatial(self, routes, num_remove):
        new_routes = self._copy_routes(routes)
        assigned = []
        for v, groups in new_routes.items():
            for g in groups: assigned.extend(g)
        if not assigned: return new_routes, []
        seed_id = random.choice(assigned)
        dists = []
        for eid in assigned:
            # Use the distance matrix (haversine-based) for geographically correct clustering.
            # Squared lat/lng differences are NOT proportional to real distance —
            # 1° longitude at ~20°N latitude is ~103 km but 1° latitude is ~111 km.
            dist_km, _ = self.dist_matrix.get_dist_dur(seed_id, eid)
            dists.append((dist_km, eid))
        dists.sort(key=lambda x: x[0])
        removed = [x[1] for x in dists[:num_remove]]
        return self._rebuild_without(new_routes, set(removed)), removed

    def destroy_worst(self, routes, num_remove):
        new_routes = self._copy_routes(routes)
        assigned = []
        for v, groups in new_routes.items():
            for g in groups:
                for eid in g:
                    # Use distance to office as a proxy for "worst" if we want, 
                    # or just the length of its segment.
                    dist, _ = self.dist_matrix.get_dist_dur(eid, "office")
                    assigned.append((dist, eid))
        if not assigned: return new_routes, []
        assigned.sort(key=lambda x: x[0], reverse=True)
        removed = []
        for _ in range(num_remove):
            if not assigned: break
            idx = int(random.random()**3 * len(assigned))
            removed.append(assigned.pop(idx)[1])
        return self._rebuild_without(new_routes, set(removed)), removed

    def destroy_route(self, routes, num_remove):
        new_routes = self._copy_routes(routes)
        removed = []
        active = [v for v, gs in new_routes.items() if gs]
        if not active: return new_routes, []
        vehs = random.sample(active, max(1, int(len(active) * 0.2)))
        for v in vehs:
            for g in new_routes[v]: removed.extend(g)
            del new_routes[v]
        return new_routes, removed

    def _rebuild_without(self, routes, ids_to_remove):
        cleaned = {}
        for v, groups in routes.items():
            new_gs = [[e for e in g if e not in ids_to_remove] for g in groups]
            new_gs = [g for g in new_gs if g]
            if new_gs: cleaned[v] = new_gs
        return cleaned

    # ========================================================================
    # REPAIR OPERATORS (RECREATE)
    # ========================================================================

    def repair_greedy(self, routes, unassigned_ids):
        current_routes = self._copy_routes(routes)
        random.shuffle(unassigned_ids)
        for eid in unassigned_ids:
            best_delta, best_move = float('inf'), None

            # Pre-compute baseline cost for each vehicle so we measure MARGINAL insertion cost.
            # Without this, vehicles with lower absolute route cost look cheaper even if
            # adding eid to them costs more than adding to a vehicle with a higher baseline.
            baseline_costs = {}
            for vid, groups in current_routes.items():
                _, m = self.simulator.simulate_vehicle(vid, groups)
                baseline_costs[vid] = (self.w_cost*m['total_cost'] + self.w_time*m['total_time'] +
                                       self.w_sharing*m['sharing_penalty'] + self.w_vehicle*m['vehicle_penalty'] +
                                       m['violation_penalty'])

            # Insert into an existing group
            for vid, groups in current_routes.items():
                for g_idx, group in enumerate(groups):
                    for pos in range(len(group) + 1):
                        temp = self._copy_groups(groups)
                        temp[g_idx].insert(pos, eid)
                        _, m = self.simulator.simulate_vehicle(vid, temp)
                        total = (self.w_cost*m['total_cost'] + self.w_time*m['total_time'] +
                                 self.w_sharing*m['sharing_penalty'] + self.w_vehicle*m['vehicle_penalty'] +
                                 m['violation_penalty'])
                        delta = total - baseline_costs[vid]
                        if delta < best_delta:
                            best_delta, best_move = delta, (vid, g_idx, pos, False)

            # Add as a new solo group on any vehicle (baseline is 0 for empty vehicles)
            for vid in self.vehicles:
                if vid in current_routes:
                    continue  # already evaluated above with existing groups
                temp = [[eid]]
                _, m = self.simulator.simulate_vehicle(vid, temp)
                delta = (self.w_cost*m['total_cost'] + self.w_time*m['total_time'] +
                         self.w_sharing*m['sharing_penalty'] + self.w_vehicle*m['vehicle_penalty'] +
                         m['violation_penalty'])
                if delta < best_delta:
                    best_delta, best_move = delta, (vid, 0, 0, True)

            # Only assign if marginal cost is less than leaving the employee unassigned
            if best_move and best_delta < W5:
                v, g, p, is_new = best_move
                if v not in current_routes: current_routes[v] = []
                if is_new: current_routes[v].append([eid])
                else: current_routes[v][g].insert(p, eid)
        return current_routes

    def repair_regret(self, routes, unassigned_ids, k=2):
        current_routes = self._copy_routes(routes)
        unassigned = list(unassigned_ids)
        while unassigned:
            regrets = []
            for eid in unassigned:
                costs = []

                # Pre-compute baseline for marginal cost calculation
                baseline_costs = {}
                for vid, groups in current_routes.items():
                    _, m = self.simulator.simulate_vehicle(vid, groups)
                    baseline_costs[vid] = (self.w_cost*m['total_cost'] + self.w_time*m['total_time'] +
                                           self.w_sharing*m['sharing_penalty'] + self.w_vehicle*m['vehicle_penalty'] +
                                           m['violation_penalty'])

                for vid, groups in current_routes.items():
                    for g_idx, group in enumerate(groups):
                        for pos in range(len(group) + 1):
                            temp = self._copy_groups(groups)
                            temp[g_idx].insert(pos, eid)
                            _, m = self.simulator.simulate_vehicle(vid, temp)
                            total = (self.w_cost*m['total_cost'] + self.w_time*m['total_time'] +
                                     self.w_sharing*m['sharing_penalty'] + self.w_vehicle*m['vehicle_penalty'] +
                                     m['violation_penalty'])
                            delta = total - baseline_costs[vid]
                            costs.append((delta, vid, g_idx, pos, False))

                # New solo group on unoccupied vehicles
                for vid in self.vehicles:
                    if vid in current_routes:
                        continue
                    temp = [[eid]]
                    _, m = self.simulator.simulate_vehicle(vid, temp)
                    delta = (self.w_cost*m['total_cost'] + self.w_time*m['total_time'] +
                             self.w_sharing*m['sharing_penalty'] + self.w_vehicle*m['vehicle_penalty'] +
                             m['violation_penalty'])
                    costs.append((delta, vid, 0, 0, True))

                costs.sort(key=lambda x: x[0])
                # Only consider insertions cheaper than leaving the employee unassigned
                valid = [c for c in costs if c[0] < W5]
                if not valid: regrets.append((-1, float('inf'), eid, None))
                else:
                    best_c = valid[0][0]
                    rv = sum(valid[i][0] - best_c for i in range(1, min(k, len(valid))))
                    regrets.append((rv, best_c, eid, valid[0]))
            if not regrets: break
            regrets.sort(key=lambda x: x[0], reverse=True)
            _, _, eid, move = regrets[0]
            if move:
                c, v, g, p, is_new = move
                if v not in current_routes: current_routes[v] = []
                if is_new: current_routes[v].append([eid])
                else: current_routes[v][g].insert(p, eid)
            unassigned.remove(eid)
        return current_routes

    def optimize(self, max_iterations=100):
        # print(f"Starting Enhanced LNS optimization for {max_iterations} iterations...")
        num_employees = len(self.employees)
        
        # Initial Repair for unassigned
        all_assigned = set()
        for groups in self.current_routes.values():
            for group in groups: all_assigned.update(group)
        initial_unassigned = list(set(self.employees.keys()) - all_assigned)
        if initial_unassigned:
            # print(f"Repairing initial solution with {len(initial_unassigned)} unassigned...")
            self.current_routes = self.repair_regret(self.current_routes, initial_unassigned, k=2)
            _, score, _ = self.evaluate(self.current_routes)
            self.best_score, self.best_routes = score, self._copy_routes(self.current_routes)
            # print(f"Repaired Initial Score: {score:.2f}")

        T, cooling = 1000.0, 0.995
        for i in range(max_iterations):
            iter_dest = random.uniform(0.1, 0.6)
            num_remove = max(1, int(num_employees * iter_dest))
            temp_routes = self._copy_routes(self.current_routes)
            
            r_val = random.random()
            if r_val < 0.25:
                temp_routes, removed = self.destroy_random(temp_routes, num_remove)
            elif r_val < 0.50:
                temp_routes, removed = self.destroy_spatial(temp_routes, num_remove)
            elif r_val < 0.75:
                temp_routes, removed = self.destroy_worst(temp_routes, num_remove)
            else:
                temp_routes, removed = self.destroy_route(temp_routes, num_remove)
                
            if random.random() < 0.5:
                temp_routes = self.repair_greedy(temp_routes, removed)
            else:
                temp_routes = self.repair_regret(temp_routes, removed, k=2)
            
            assigned_iter = set()
            for gs in temp_routes.values():
                for g in gs: assigned_iter.update(g)
            temp_routes, _ = self.local_search.run(temp_routes, set(self.employees.keys()) - assigned_iter, max_steps=100)
            
            _, score, metrics = self.evaluate(temp_routes)
            delta = score - self.best_score
            if score < self.best_score:
                # print(f"Iter {i}: NEW BEST! {score:.2f} | Hard: {metrics['hard_vio_count']}")
                self.best_score, self.best_routes, self.current_routes = score, self._copy_routes(temp_routes), temp_routes
            elif random.random() < math.exp(-delta / max(T, 0.1)):
                self.current_routes = temp_routes
            T *= cooling

    def get_formatted_output(self):
        output_vehicles = []
        total_cost_all = 0
        
        def format_time_min(minutes):
            h = int(minutes // 60) % 24
            m = int(minutes % 60)
            return f"{h:02d}:{m:02d}"

        for veh_id, groups in self.best_routes.items():
            if not groups: continue
            feasible, m = self.simulator.simulate_vehicle(veh_id, groups)
            vehicle = self.vehicles[veh_id]
            
            route_links = []
            curr = veh_id
            for group in groups:
                for eid in group:
                    route_links.append(f"{curr}_{eid}")
                    curr = eid
                route_links.append(f"{curr}_office")
                curr = "office"
            
            # Format route sequence from simulator
            formatted_sequence = []
            for step in m['route_sequence']:
                fs = {
                    "step": step['step'],
                    "location": step['location'],
                    "arrival_time": format_time_min(step['arrival_time'])
                }
                if 'departure_time' in step:
                    fs["departure_time"] = format_time_min(step['departure_time'])
                formatted_sequence.append(fs)

            output_vehicles.append({
                "vehicle_id": veh_id,
                "vehicle_type": vehicle.category,
                "capacity": vehicle.capacity,
                "avg_speed_kmph": vehicle.speed,
                "total_cost": round(m['total_cost'], 2),
                "total_time_minutes": round(m['total_time'], 2),
                "total_steps": len(formatted_sequence),
                "routes": route_links,
                "route_sequence": formatted_sequence
            })
            total_cost_all += m['total_cost']

        return {
            "vehicles": output_vehicles,
            "summary": {
                "total_cost_all_vehicles": round(total_cost_all, 2)
            }
        }