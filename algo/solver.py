from .lns_algo import LNSOptimizer
from .vroom_solver import solve_vroom
import json
import importlib.util
import sys
import os
import concurrent.futures
from multiprocessing import Manager
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


def _run_lns(file_bytes, matrix_edge_list):
    lns = LNSOptimizer(file_bytes, matrix_edge_list)
    lns.optimize(max_iterations=100)
    return lns.get_formatted_output()


def _run_alns(input_data, matrix_edge_list, file_bytes, result_ref):
    curr_dir = os.path.dirname(__file__)
    alns_mod = import_custom_module("alns_solver_16_02", os.path.join(curr_dir, "16-02.py"))
    return alns_mod.solve_alns(input_data, matrix_edge_list, file_bytes, result_ref)


def _run_vroom(input_data, matrix_edge_list, file_bytes):
    return solve_vroom(input_data, matrix_edge_list, file_bytes)


def solve_vrp(input_data, matrix_edge_list, file_bytes):
    """
    Main Solver Function that tries multiple algorithms concurrently and picks the best.
    """
    solutions = []
    
    with Manager() as manager:
        alns_result_ref = manager.list([None])
        futures = {}
        
        with concurrent.futures.ProcessPoolExecutor(max_workers=3) as executor:
            futures['LNS'] = executor.submit(_run_lns, file_bytes, matrix_edge_list)
            futures['ALNS'] = executor.submit(_run_alns, input_data, matrix_edge_list, file_bytes, alns_result_ref)
            futures['VROOM'] = executor.submit(_run_vroom, input_data, matrix_edge_list, file_bytes)
            
            for name, fut in futures.items():
                try:
                    # Time cap of 15 mins for each solver as stated
                    result = fut.result(timeout=900)
                    solutions.append((name, result))
                except concurrent.futures.TimeoutError:
                    if name == 'ALNS':
                        if alns_result_ref[0] is not None:
                            print("ALNS Solver timed out – using best partial result found so far.")
                            solutions.append(("ALNS", alns_result_ref[0]))
                        else:
                            print("ALNS Solver timed out with no partial result, skipping.")
                    else:
                        print(f"{name} Solver timed out.")
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

