import json
import importlib.util
import sys
import os
import threading
import concurrent.futures
from .lns_algo import LNSOptimizer
from .vroom_solver import solve_vroom
from .feasibilityfinal import get_feasibility_score

# sys.modules is shared across all threads. Guard the dynamic import so that
# concurrent calls to solve_vrp() don't race when registering the ALNS module.
_import_lock = threading.Lock()

# Trick to import 16-02.py which is not a valid python module name
def import_custom_module(module_name, file_path):
    with _import_lock:
        if module_name in sys.modules:
            return sys.modules[module_name]
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
    Main Solver Function that tries multiple algorithms in parallel and picks the best.
    """
    solutions = []

    # ── Worker functions (each runs in its own thread) ────────────────────────

    def run_lns():
        lns = LNSOptimizer(file_bytes, matrix_edge_list)
        lns.optimize(max_iterations=100)
        return ("LNS", lns.get_formatted_output())

    def run_alns():
        curr_dir = os.path.dirname(__file__)
        alns_mod = import_custom_module(
            "alns_solver_16_02", os.path.join(curr_dir, "16-02.py"))
        result_ref = [None]  # shared container written by ALNS thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(alns_mod.solve_alns,
                              input_data, matrix_edge_list, file_bytes, result_ref)
            try:
                alns_res = fut.result(timeout=150)  # 150 s hard cap
                return ("ALNS", alns_res)
            except concurrent.futures.TimeoutError:
                if result_ref[0] is not None:
                    print("ALNS Solver timed out – using best partial result found so far.")
                    return ("ALNS", result_ref[0])
                else:
                    raise RuntimeError("ALNS Solver timed out with no partial result")

    def run_vroom():
        return ("VROOM", solve_vroom(input_data, matrix_edge_list, file_bytes))

    # ── Run all three solvers concurrently ────────────────────────────────────

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(run_lns):   "LNS",
            executor.submit(run_alns):  "ALNS",
            executor.submit(run_vroom): "VROOM",
        }
        for fut in concurrent.futures.as_completed(futures):
            name = futures[fut]
            try:
                solutions.append(fut.result())
            except Exception as e:
                print(f"{name} Solver failed: {e}")

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
    # 3. minimized effective objective (objective + soft_violation_penalty) (lower is better)
    #    Each soft violation adds SOFT_VIOLATION_PENALTY to the objective for comparison.

    SOFT_VIOLATION_PENALTY = 50  # 1 soft violation = 50 objective cost

    def effective_objective(score):
        return score['objective'] + score['soft_violations'] * SOFT_VIOLATION_PENALTY

    # Filter valid solutions (hard_violations == 0)
    valid_sols = [s for s in scored_solutions if s[2]['hard_violations'] == 0]
    
    if not valid_sols:
        # If no valid solutions, pick the one with least hard violations
        best_overall = min(scored_solutions, key=lambda x: (x[2]['hard_violations'], -x[2]['served_count'], effective_objective(x[2])))
    else:
        # Pick best from valid ones
        best_overall = min(valid_sols, key=lambda x: (-x[2]['served_count'], effective_objective(x[2])))

    print(f"Selected Best Solver: {best_overall[0]}")
    return best_overall[1], best_overall[2], best_overall[0]