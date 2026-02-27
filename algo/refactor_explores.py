import sys

file_path = r'c:\Users\manan\OneDrive\Desktop\Shin-Sekai\Kriti26\H3\code\FINAL_backend\algo\lns_local_search.py'
with open(file_path, 'r') as f:
    content = f.read()

# Replace all rebuild_location_map at the end of apply_* functions
content = content.replace(
    '        new_emp_loc = self.rebuild_location_map(new_routes)\n        return new_routes, new_emp_loc, set(unassigned)',
    '        new_emp_loc = self.rebuild_location_map(new_routes) if rebuild_map else {}\n        return new_routes, new_emp_loc, set(unassigned)'
)

# Replace explore_relocate_moves inner loop
content = content.replace(
"""                    new_routes, _, _ = self.apply_relocate(routes, emp_location, unassigned, emp_id, target_veh_id, g_idx, len(group))
                    served, score = self.calculate_objective(new_routes)
                    if served >= current_served and score < current_score - 0.001:""",
"""                    new_routes, _, _ = self.apply_relocate(routes, emp_location, unassigned, emp_id, target_veh_id, g_idx, len(group), rebuild_map=False)
                    feasible, d_served, d_score = self.evaluate_move_delta(routes, new_routes, [current_veh, target_veh_id])
                    served = current_served + d_served
                    score = current_score + d_score
                    if feasible and served >= current_served and score < current_score - 0.001:"""
)

# Replace explore_swap_moves inner loop
content = content.replace(
"""            new_routes, _, _ = self.apply_swap(routes, emp_location, unassigned, e1, e2)
            if not new_routes: continue
            served, score = self.calculate_objective(new_routes)
            if served >= current_served and score < current_score - 0.001:""",
"""            new_routes, _, _ = self.apply_swap(routes, emp_location, unassigned, e1, e2, rebuild_map=False)
            if not new_routes: continue
            feasible, d_served, d_score = self.evaluate_move_delta(routes, new_routes, [emp_location[e1][0], emp_location[e2][0]])
            served = current_served + d_served
            score = current_score + d_score
            if feasible and served >= current_served and score < current_score - 0.001:"""
)

# Replace explore_2opt_moves inner loop
content = content.replace(
"""                     new_routes, _, _ = self.apply_2opt(routes, emp_location, unassigned, veh_id, g_idx, i, j)
                     served, score = self.calculate_objective(new_routes)
                     if served >= current_served and score < current_score - 0.001:""",
"""                     new_routes, _, _ = self.apply_2opt(routes, emp_location, unassigned, veh_id, g_idx, i, j, rebuild_map=False)
                     feasible, d_served, d_score = self.evaluate_move_delta(routes, new_routes, [veh_id])
                     served = current_served + d_served
                     score = current_score + d_score
                     if feasible and served >= current_served and score < current_score - 0.001:"""
)

# Replace explore_oropt_moves inner loop
content = content.replace(
"""                         new_routes, _, _ = self.apply_oropt(routes, emp_location, unassigned, veh_id, g_idx, start, end, insert_pos)
                         if not new_routes: continue
                         
                         served, score = self.calculate_objective(new_routes)
                         if served >= current_served and score < current_score - 0.001:""",
"""                         new_routes, _, _ = self.apply_oropt(routes, emp_location, unassigned, veh_id, g_idx, start, end, insert_pos, rebuild_map=False)
                         if not new_routes: continue
                         feasible, d_served, d_score = self.evaluate_move_delta(routes, new_routes, [veh_id])
                         served = current_served + d_served
                         score = current_score + d_score
                         if feasible and served >= current_served and score < current_score - 0.001:"""
)

# Replace explore_exchange_group_moves inner loop
content = content.replace(
"""             new_routes, _, _ = self.apply_exchange_groups(routes, emp_location, unassigned, va, ga, vb, gb)
             if not new_routes: continue
             
             served, score = self.calculate_objective(new_routes)
             if served >= current_served and score < current_score - 0.001:""",
"""             new_routes, _, _ = self.apply_exchange_groups(routes, emp_location, unassigned, va, ga, vb, gb, rebuild_map=False)
             if not new_routes: continue
             feasible, d_served, d_score = self.evaluate_move_delta(routes, new_routes, [va, vb])
             served = current_served + d_served
             score = current_score + d_score
             if feasible and served >= current_served and score < current_score - 0.001:"""
)

# Replace merge_groups inner loop
content = content.replace(
"""                 new_routes, _, _ = self.apply_merge_groups(routes, emp_location, unassigned, vid, g1, g2)
                 if not new_routes: continue
                 
                 served, score = self.calculate_objective(new_routes)
                 if served >= current_served and score < current_score - 0.001:""",
"""                 new_routes, _, _ = self.apply_merge_groups(routes, emp_location, unassigned, vid, g1, g2, rebuild_map=False)
                 if not new_routes: continue
                 feasible, d_served, d_score = self.evaluate_move_delta(routes, new_routes, [vid])
                 served = current_served + d_served
                 score = current_score + d_score
                 if feasible and served >= current_served and score < current_score - 0.001:"""
)

# Replace split_groups inner loop
content = content.replace(
"""                 new_routes, _, _ = self.apply_split_group(routes, emp_location, unassigned, vid, g_idx, split_pos)
                 if not new_routes: continue
                 
                 served, score = self.calculate_objective(new_routes)
                 if served >= current_served and score < current_score - 0.001:""",
"""                 new_routes, _, _ = self.apply_split_group(routes, emp_location, unassigned, vid, g_idx, split_pos, rebuild_map=False)
                 if not new_routes: continue
                 feasible, d_served, d_score = self.evaluate_move_delta(routes, new_routes, [vid])
                 served = current_served + d_served
                 score = current_score + d_score
                 if feasible and served >= current_served and score < current_score - 0.001:"""
)

with open(file_path, 'w') as f:
    f.write(content)

print(f"Refactoring complete for {file_path}")
