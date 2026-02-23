from .lns_algo import LNSOptimizer
from .vroom_solver import solve_vroom
import json
import importlib.util
import sys
import os
from .feasibilityfinal import get_feasibility_score

# Trick to import 16-02.py which is not a valid python module name
def import_custom_module(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    # Tell Python this module lives inside the 'algo' package so that
    # relative imports (e.g. `from .lns_utils import ...`) work correctly.
    module.__package__ = __package__  # same package as solver.py ("algo")
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

def solve_vrp(input_data, matrix_edge_list, file_bytes):
    """
    Main Solver Function that tries multiple algorithms and picks the best.
    """
    solutions = []
    
    # # 1. LNS Solver
    # try:
    #     lns = LNSOptimizer(file_bytes, matrix_edge_list)
    #     lns.optimize(max_iterations=100)
    #     solutions.append(("LNS", lns.get_formatted_output()))
    # except Exception as e:
    #     print(f"LNS Solver failed: {e}")

    # # 2. ALNS Solver (from 16-02.py) – hard 30 s wall-clock timeout
    # try:
    #     import concurrent.futures
    #     curr_dir = os.path.dirname(__file__)
    #     alns_mod = import_custom_module("alns_solver_16_02", os.path.join(curr_dir, "16-02.py"))
    #     with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    #         fut = pool.submit(alns_mod.solve_alns, input_data, matrix_edge_list, file_bytes)
    #         try:
    #             alns_res = fut.result(timeout=30)   # 30 s hard cap
    #             solutions.append(("ALNS", alns_res))
    #         except concurrent.futures.TimeoutError:
    #             print("ALNS Solver timed out (>30 s), skipping.")
    # except Exception as e:
    #     print(f"ALNS Solver failed: {e}")

    # 3. VROOM Solver
    try:
        vroom_res = solve_vroom(input_data, matrix_edge_list, file_bytes)
        solutions.append(("VROOM", vroom_res))
    except Exception as e:
        print(f"VROOM Solver failed: {e}")

    if not solutions:
        raise Exception("All solvers failed")

    # Evaluate and pick best
    scored_solutions = []
    for name, sol in solutions:
        score = get_feasibility_score(file_bytes, matrix_edge_list, sol)
        scored_solutions.append((name, sol, score))
        print(f"Solver {name}: Served={score['served_count']}, HardViolations={score['hard_violations']}, Objective={score['objective']:.2f}, SoftViolations={score['soft_violations']}")

    # Ranking criteria:
    # 1. strictly 0 hard constraint violations
    # 2. then compare the number of people serviced(higher is better) strictly
    # 3. minimized objective cost and time(lower is better)
    # 4. Number of soft constraints(lower is better)
    
    # Filter valid solutions (hard_violations == 0)
    valid_sols = [s for s in scored_solutions if s[2]['hard_violations'] == 0]
    
    if not valid_sols:
        # If no valid solutions, pick the one with least hard violations
        best_overall = min(scored_solutions, key=lambda x: (x[2]['hard_violations'], -x[2]['served_count'], x[2]['objective'], x[2]['soft_violations']))
    else:
        # Pick best from valid ones
        best_overall = min(valid_sols, key=lambda x: (-x[2]['served_count'], x[2]['objective'], x[2]['soft_violations']))

    print(f"Selected Best Solver: {best_overall[0]}")
    return best_overall[1]

