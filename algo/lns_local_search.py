import copy
import random
import time
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional, Callable
from dataclasses import dataclass

from .lns_utils import Employee, Vehicle
from .lns_simulator import RouteSimulator

@dataclass
class Move:
    """Represents a local search move with its evaluation metrics"""
    move_type: str
    delta_score: float
    priority_delta: int
    employees_involved: List[str]
    vehicles_involved: List[str]
    apply_func: Callable
    description: str = ""

class LocalSearch:
    def __init__(self, employees: Dict[str, Employee], vehicles: Dict[str, Vehicle], 
                 weights: Dict[str, float], simulator: RouteSimulator):
        self.employees = employees
        self.vehicles = vehicles
        
        # Consistent weights with LNS
        self.w_cost = weights.get('w1', 1.0)
        self.w_time = weights.get('w2', 1.0)
        self.w_sharing = weights.get('w3', 2000.0)
        self.w_vehicle = weights.get('w4', 2000.0)
        
        self.simulator = simulator
        
        # Caches
        self._score_cache = {}
        self._feasibility_cache = {} 

    def clear_cache(self):
        self._score_cache.clear()
        self._feasibility_cache.clear()

    def invalidate_vehicles(self, vehicle_ids: List[str]):
        """Remove cache entries only for the given vehicle IDs."""
        veh_set = set(vehicle_ids)
        keys_to_remove = [k for k in self._score_cache if k.split(':')[0] in veh_set]
        for k in keys_to_remove:
            del self._score_cache[k]

    def get_cache_key(self, veh_id: str, groups: List[List[str]]) -> str:
        return veh_id + ':' + '|'.join(','.join(g) for g in groups)

    def calculate_objective(self, routes: Dict[str, List[List[str]]]) -> Tuple[int, float]:
        """Return (served_count, weighted_score)"""
        total_cost = 0.0
        total_time = 0.0
        total_sharing_pen = 0.0
        total_veh_pen = 0.0
        total_violation_pen = 0.0
        served_count = 0
        
        for veh_id, groups in routes.items():
            if not groups:
                continue
            
            cache_key = self.get_cache_key(veh_id, groups)
            if cache_key in self._score_cache:
                cached = self._score_cache[cache_key]
                served_count += cached['served']
                total_cost += cached['cost']
                total_time += cached['time']
                total_sharing_pen += cached['sharing']
                total_veh_pen += cached['veh']
                total_violation_pen += cached['violation']
                continue
            
            feasible, metrics = self.simulator.simulate_vehicle(veh_id, groups)
            
            if not feasible:
                return -1, float('inf')
            
            served = sum(len(g) for g in groups)
            served_count += served
            total_cost += metrics['total_cost']
            total_time += metrics['total_time']
            total_sharing_pen += metrics['sharing_penalty']
            total_veh_pen += metrics['vehicle_penalty']
            total_violation_pen += metrics['violation_penalty']
            
            self._score_cache[cache_key] = {
                'served': served,
                'cost': metrics['total_cost'],
                'time': metrics['total_time'],
                'sharing': metrics['sharing_penalty'],
                'veh': metrics['vehicle_penalty'],
                'violation': metrics['violation_penalty']
            }
        
        score = (self.w_cost * total_cost + 
                self.w_time * total_time + 
                self.w_sharing * total_sharing_pen + 
                self.w_vehicle * total_veh_pen +
                total_violation_pen)
        
        return served_count, score

    def copy_routes(self, routes: Dict) -> Dict:
        return {k: [list(g) for g in v] for k, v in routes.items()}

    def rebuild_location_map(self, routes: Dict) -> Dict:
        emp_loc = {}
        for v_id, groups in routes.items():
            for g_idx, group in enumerate(groups):
                for p_idx, e_id in enumerate(group):
                    emp_loc[e_id] = (v_id, g_idx, p_idx)
        return emp_loc

    # ============================================================================
    # MOVES
    # ============================================================================
    
    def apply_insert(self, routes, emp_location, unassigned, emp_id, veh_id, group_idx, pos):
        new_routes = self.copy_routes(routes)
        new_unassigned = set(unassigned)
        
        if emp_id in emp_location:
            old_veh, old_g, old_p = emp_location[emp_id]
            new_routes[old_veh][old_g].pop(old_p)
            if not new_routes[old_veh][old_g]:
                new_routes[old_veh].pop(old_g)
        
        if veh_id not in new_routes:
            new_routes[veh_id] = []
        
        veh_groups = new_routes[veh_id]
        if group_idx > len(veh_groups):
            group_idx = len(veh_groups)
        
        if group_idx == len(veh_groups):
            veh_groups.append([emp_id])
        else:
            actual_pos = min(pos, len(veh_groups[group_idx]))
            veh_groups[group_idx].insert(actual_pos, emp_id)
            
        new_emp_loc = self.rebuild_location_map(new_routes)
        if emp_id in new_unassigned:
            new_unassigned.remove(emp_id)
            
        return new_routes, new_emp_loc, new_unassigned

    def apply_swap(self, routes, emp_location, unassigned, emp_i, emp_j):
        new_routes = self.copy_routes(routes)
        new_unassigned = set(unassigned)
        
        loc_i = emp_location.get(emp_i)
        loc_j = emp_location.get(emp_j)
        
        if not loc_i or not loc_j: return None, None, None
        
        veh_i, g_i, p_i = loc_i
        veh_j, g_j, p_j = loc_j
        
        if veh_i == veh_j and g_i == g_j:
            new_routes[veh_i][g_i][p_i] = emp_j
            new_routes[veh_i][g_i][p_j] = emp_i
        else:
            new_routes[veh_i][g_i][p_i] = emp_j
            new_routes[veh_j][g_j][p_j] = emp_i
            
        new_emp_loc = self.rebuild_location_map(new_routes)
        return new_routes, new_emp_loc, new_unassigned

    def apply_2opt(self, routes, emp_location, unassigned, veh_id, group_idx, i, j):
        if veh_id not in routes or group_idx >= len(routes[veh_id]):
            return None, None, None
        
        group = routes[veh_id][group_idx]
        if i >= j or i < 0 or j >= len(group):
            return None, None, None
            
        new_routes = self.copy_routes(routes)
        new_routes[veh_id][group_idx] = group[:i] + group[i:j+1][::-1] + group[j+1:]
        
        new_emp_loc = self.rebuild_location_map(new_routes)
        return new_routes, new_emp_loc, set(unassigned)

    def apply_relocate(self, routes, emp_location, unassigned, emp_id, target_veh, target_g, target_p):
        return self.apply_insert(routes, emp_location, unassigned, emp_id, target_veh, target_g, target_p)

    def apply_oropt(self, routes, emp_location, unassigned, veh_id, group_idx, start, end, insert_pos):
        if veh_id not in routes or group_idx >= len(routes[veh_id]):
            return None, None, None
        
        group = routes[veh_id][group_idx]
        n = len(group)
        
        if start < 0 or end > n or start >= end or insert_pos < 0:
            return None, None, None
        
        # If insertion is inside the segment, invalid
        if start <= insert_pos < end:
            return None, None, None
            
        new_routes = self.copy_routes(routes)
        segment = group[start:end]
        remaining = group[:start] + group[end:]
        
        # Adjust insert position if it was after the removed segment
        if insert_pos > start:
            insert_pos -= (end - start)
            
        if insert_pos > len(remaining):
             insert_pos = len(remaining)
             
        new_group = remaining[:insert_pos] + segment + remaining[insert_pos:]
        new_routes[veh_id][group_idx] = new_group
        
        new_emp_loc = self.rebuild_location_map(new_routes)
        return new_routes, new_emp_loc, set(unassigned)

    def apply_exchange_groups(self, routes, emp_location, unassigned, veh_a, g_a, veh_b, g_b):
        if veh_a not in routes or veh_b not in routes: return None, None, None
        if g_a >= len(routes[veh_a]) or g_b >= len(routes[veh_b]): return None, None, None
        
        new_routes = self.copy_routes(routes)
        
        group_a = new_routes[veh_a][g_a]
        group_b = new_routes[veh_b][g_b]
        
        new_routes[veh_a][g_a] = group_b
        new_routes[veh_b][g_b] = group_a
        
        new_emp_loc = self.rebuild_location_map(new_routes)
        return new_routes, new_emp_loc, set(unassigned)

    def apply_merge_groups(self, routes, emp_location, unassigned, veh_id, g_i, g_j):
        if veh_id not in routes: return None, None, None
        groups = routes[veh_id]
        if g_i >= len(groups) or g_j >= len(groups) or g_i == g_j: return None, None, None
        
        new_routes = self.copy_routes(routes)
        # Merge j into i
        merged = new_routes[veh_id][g_i] + new_routes[veh_id][g_j]
        new_routes[veh_id][g_i] = merged
        
        # Remove j (pop carefully if indices shift? usually pop highest index first)
        high = max(g_i, g_j)
        low = min(g_i, g_j)
        
        # We merged into low, so pop high
        # Wait, simple list pop shifts indices.
        # If we assign merged to 'low', we pop 'high'.
        new_routes[veh_id][low] = merged
        new_routes[veh_id].pop(high)
        
        new_emp_loc = self.rebuild_location_map(new_routes)
        return new_routes, new_emp_loc, set(unassigned)

    def apply_split_group(self, routes, emp_location, unassigned, veh_id, g_idx, split_pos):
        if veh_id not in routes or g_idx >= len(routes[veh_id]): return None, None, None
        group = routes[veh_id][g_idx]
        if split_pos <= 0 or split_pos >= len(group): return None, None, None
        
        new_routes = self.copy_routes(routes)
        part1 = group[:split_pos]
        part2 = group[split_pos:]
        
        new_routes[veh_id][g_idx] = part1
        new_routes[veh_id].insert(g_idx + 1, part2)
        
        new_emp_loc = self.rebuild_location_map(new_routes)
        return new_routes, new_emp_loc, set(unassigned)

    # ============================================================================
    # EXPLORATION
    # ============================================================================
    
    def explore_relocate_moves(self, routes, emp_location, unassigned, current_served, current_score):
        moves = []
        assigned_emps = list(emp_location.keys())
        if len(assigned_emps) > 40:
            assigned_emps = random.sample(assigned_emps, 40)
            
        for emp_id in assigned_emps:
            current_veh, current_g, _ = emp_location[emp_id]
            target_vehs = list(self.vehicles.keys())
            if len(target_vehs) > 8:
                target_vehs = random.sample(target_vehs, 8)
                if current_veh not in target_vehs: target_vehs.append(current_veh)
                
            for target_veh_id in target_vehs:
                target_groups = routes.get(target_veh_id, [])
                for g_idx, group in enumerate(target_groups):
                    if target_veh_id == current_veh and g_idx == current_g: continue
                    new_routes, _, _ = self.apply_relocate(routes, emp_location, unassigned, emp_id, target_veh_id, g_idx, len(group))
                    served, score = self.calculate_objective(new_routes)
                    if served >= current_served and score < current_score - 0.001:
                        def make_apply(eid, vid, gid, pos):
                            return lambda r, el, ua: self.apply_relocate(r, el, ua, eid, vid, gid, pos)
                        moves.append(Move('relocate', score - current_score, served - current_served, [emp_id], [current_veh, target_veh_id], make_apply(emp_id, target_veh_id, g_idx, len(group))))
        return moves

    def explore_swap_moves(self, routes, emp_location, unassigned, current_served, current_score):
        moves = []
        emp_list = list(emp_location.keys())
        for _ in range(80):
            if len(emp_list) < 2: break
            e1, e2 = random.sample(emp_list, 2)
            new_routes, _, _ = self.apply_swap(routes, emp_location, unassigned, e1, e2)
            if not new_routes: continue
            served, score = self.calculate_objective(new_routes)
            if served >= current_served and score < current_score - 0.001:
                 veh_e1 = emp_location[e1][0]
                 veh_e2 = emp_location[e2][0]
                 involved = list({veh_e1, veh_e2})
                 def make_apply(x, y):
                     return lambda r, el, ua: self.apply_swap(r, el, ua, x, y)
                 moves.append(Move('swap', score - current_score, served - current_served, [e1, e2], involved, make_apply(e1, e2)))
        return moves

    def explore_2opt_moves(self, routes, emp_location, unassigned, current_served, current_score):
        moves = []
        for veh_id, groups in routes.items():
            for g_idx, group in enumerate(groups):
                if len(group) < 3: continue
                # Limit checks per group
                for _ in range(5): 
                     if len(group) < 3: break
                     i = random.randint(0, len(group)-3)
                     j = random.randint(i+2, len(group)-1)
                     new_routes, _, _ = self.apply_2opt(routes, emp_location, unassigned, veh_id, g_idx, i, j)
                     served, score = self.calculate_objective(new_routes)
                     if served >= current_served and score < current_score - 0.001:
                         def make_apply(v, g, s, e):
                             return lambda r, el, ua: self.apply_2opt(r, el, ua, v, g, s, e)
                         moves.append(Move('2opt', score - current_score, 0, group[i:j+1], [veh_id], make_apply(veh_id, g_idx, i, j)))
        return moves

    def explore_oropt_moves(self, routes, emp_location, unassigned, current_served, current_score):
        moves = []
        
        # Pick random vehicles/groups
        vehs = list(routes.keys())
        if not vehs: return []
        
        sampled_vehs = random.sample(vehs, min(len(vehs), 10))
        
        for veh_id in sampled_vehs:
            groups = routes[veh_id]
            for g_idx, group in enumerate(groups):
                if len(group) < 4: continue
                
                # Try a few segments
                for _ in range(5):
                    seg_len = random.randint(1, 3)
                    if len(group) - seg_len < 1: continue
                    start = random.randint(0, len(group) - seg_len)
                    end = start + seg_len
                    
                    # Try a few insert positions
                    for _ in range(3):
                         # Insert into remaining
                         rem_len = len(group) - seg_len
                         insert_pos = random.randint(0, rem_len)
                         
                         new_routes, _, _ = self.apply_oropt(routes, emp_location, unassigned, veh_id, g_idx, start, end, insert_pos)
                         if not new_routes: continue
                         
                         served, score = self.calculate_objective(new_routes)
                         if served >= current_served and score < current_score - 0.001:
                            def make_apply(v, g, s, e, p):
                                return lambda r, el, ua: self.apply_oropt(r, el, ua, v, g, s, e, p)
                            moves.append(Move('oropt', score - current_score, 0, group[start:end], [veh_id], make_apply(veh_id, g_idx, start, end, insert_pos)))
        return moves

    def explore_exchange_group_moves(self, routes, emp_location, unassigned, current_served, current_score):
        moves = []
        vehs = list(routes.keys())
        if len(vehs) < 2: return []
        
        for _ in range(20):
             va, vb = random.sample(vehs, 2)
             if not routes[va] or not routes[vb]: continue
             ga = random.randint(0, len(routes[va]) - 1)
             gb = random.randint(0, len(routes[vb]) - 1)
             
             new_routes, _, _ = self.apply_exchange_groups(routes, emp_location, unassigned, va, ga, vb, gb)
             if not new_routes: continue
             
             served, score = self.calculate_objective(new_routes)
             if served >= current_served and score < current_score - 0.001:
                def make_apply(v1, g1, v2, g2):
                    return lambda r, el, ua: self.apply_exchange_groups(r, el, ua, v1, g1, v2, g2)
                moves.append(Move('exchange_groups', score - current_score, 0, [], [va, vb], make_apply(va, ga, vb, gb)))
        return moves

    def explore_merge_split_moves(self, routes, emp_location, unassigned, current_served, current_score):
        moves = []
        
        # Merge within vehicle
        vehs = [v for v in routes.keys() if len(routes[v]) >= 2]
        if vehs:
             sampled = random.sample(vehs, min(len(vehs), 5))
             for vid in sampled:
                 groups = routes[vid]
                 g1, g2 = random.sample(range(len(groups)), 2)
                 
                 new_routes, _, _ = self.apply_merge_groups(routes, emp_location, unassigned, vid, g1, g2)
                 if not new_routes: continue
                 
                 served, score = self.calculate_objective(new_routes)
                 if served >= current_served and score < current_score - 0.001:
                    def make_apply(v, ga, gb):
                        return lambda r, el, ua: self.apply_merge_groups(r, el, ua, v, ga, gb)
                    moves.append(Move('merge_groups', score - current_score, 0, [], [vid], make_apply(vid, g1, g2)))

        # Split group
        vehs = [v for v in routes.keys() if any(len(g) >= 2 for g in routes[v])]
        if vehs:
             sampled = random.sample(vehs, min(len(vehs), 5))
             for vid in sampled:
                 groups = routes[vid]
                 candidates = [i for i, g in enumerate(groups) if len(g) >= 2]
                 if not candidates: continue
                 g_idx = random.choice(candidates)
                 group = groups[g_idx]
                 split_pos = random.randint(1, len(group)-1)
                 
                 new_routes, _, _ = self.apply_split_group(routes, emp_location, unassigned, vid, g_idx, split_pos)
                 if not new_routes: continue
                 
                 served, score = self.calculate_objective(new_routes)
                 if served >= current_served and score < current_score - 0.001:
                     def make_apply(v, g, p):
                         return lambda r, el, ua: self.apply_split_group(r, el, ua, v, g, p)
                     moves.append(Move('split_group', score - current_score, 0, [], [vid], make_apply(vid, g_idx, split_pos)))

        return moves

    def select_best_move(self, moves: List[Move], current_served: int) -> Optional[Move]:
        if not moves:
            return None
        
        # Priority: Served count > Score
        serving_moves = [m for m in moves if m.priority_delta > 0]
        if serving_moves:
            return min(serving_moves, key=lambda m: m.delta_score)
        
        improving_moves = [m for m in moves if m.delta_score < -0.001]
        if improving_moves:
            return min(improving_moves, key=lambda m: m.delta_score)
            
        return None

    def run(self, current_routes, current_unassigned, max_steps=50):
        self.clear_cache()
        emp_location = self.rebuild_location_map(current_routes)
        current_served, current_score = self.calculate_objective(current_routes)
        
        for step in range(max_steps):
            all_local_moves = []
            
            # Phase 2: Relocate
            relocate_moves = self.explore_relocate_moves(current_routes, emp_location, current_unassigned, current_served, current_score)
            all_local_moves.extend(relocate_moves)
            
            # Phase 3: Swap
            swap_moves = self.explore_swap_moves(current_routes, emp_location, current_unassigned, current_served, current_score)
            all_local_moves.extend(swap_moves)
            
            # Phase 4: 2-Opt
            opt2_moves = self.explore_2opt_moves(current_routes, emp_location, current_unassigned, current_served, current_score)
            all_local_moves.extend(opt2_moves)
            
            # Phase 5: Or-Opt
            oropt_moves = self.explore_oropt_moves(current_routes, emp_location, current_unassigned, current_served, current_score)
            all_local_moves.extend(oropt_moves)
            
            # Phase 6: Exchange Groups
            exchange_moves = self.explore_exchange_group_moves(current_routes, emp_location, current_unassigned, current_served, current_score)
            all_local_moves.extend(exchange_moves)
            
            # Phase 7: Merge/Split
            merge_split_moves = self.explore_merge_split_moves(current_routes, emp_location, current_unassigned, current_served, current_score)
            all_local_moves.extend(merge_split_moves)
            
            # Select best move from ALL collected moves
            best_move = self.select_best_move(all_local_moves, current_served)
            
            if not best_move:
                break
                
            # Apply
            current_routes, emp_location, current_unassigned = best_move.apply_func(current_routes, emp_location, current_unassigned)
            current_served += best_move.priority_delta
            current_score += best_move.delta_score
            
            self.invalidate_vehicles(best_move.vehicles_involved)
            
        return current_routes, current_unassigned