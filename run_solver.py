"""
run_solver.py  –  Standalone script to execute algo/solver.py (solve_vrp)

Usage:
    python run_solver.py --excel <path_to_excel.xlsx>
                         [--matrix <matrix_edge_list.json>]
                         [--payload <payload_dict.json>]
                         [--output <output.json>]
                         [--iterations <int>]

Defaults:
    --matrix   algo/templts/matrix_edge_list.json
    --payload  algo/templts/payload_dict.json
    --output   solver_output.json
    --iterations 100  (passed through to LNS; ALNS/VROOM ignore this)

The Excel file must have three sheets: employees, vehicles, metadata.
(The same format that the FastAPI /process-routes endpoint expects.)
"""

import argparse
import json
import os
import sys
import time

# ── Ensure both h3-backend/ AND h3-backend/algo/ are on sys.path ─────────────
# h3-backend/ is needed so `from algo.solver import ...` works.
# h3-backend/algo/ is needed so the bare imports inside lns_algo.py, etc.
# (e.g. `from lns_utils import ...`) also resolve correctly.
HERE = os.path.dirname(os.path.abspath(__file__))
ALGO_DIR = os.path.join(HERE, "algo")
for _p in (HERE, ALGO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from algo.solver import solve_vrp          # noqa: E402  (after sys.path patch)


# ─────────────────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the VRP solver (LNS / ALNS / VROOM) on an Excel input."
    )
    parser.add_argument(
        "--excel", "-e",
        required=True,
        help="Path to the input Excel file (.xlsx) with sheets: employees, vehicles, metadata."
    )
    parser.add_argument(
        "--matrix", "-m",
        default=os.path.join(HERE, "algo", "templts", "matrix_edge_list.json"),
        help="Path to the matrix edge list JSON file. "
             "Default: algo/templts/matrix_edge_list.json"
    )
    parser.add_argument(
        "--payload", "-p",
        default=os.path.join(HERE, "algo", "templts", "payload_dict.json"),
        help="Path to the payload / input-data JSON file. "
             "Default: algo/templts/payload_dict.json"
    )
    parser.add_argument(
        "--output", "-o",
        default=os.path.join(HERE, "solver_output.json"),
        help="Where to write the JSON result. Default: solver_output.json"
    )
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()

    # 1. Load Excel as raw bytes ──────────────────────────────────────────────
    excel_path = os.path.abspath(args.excel)
    if not os.path.isfile(excel_path):
        sys.exit(f"[ERROR] Excel file not found: {excel_path}")
    print(f"[INFO] Loading Excel file : {excel_path}")
    with open(excel_path, "rb") as f:
        file_bytes = f.read()

    # 2. Load matrix edge list ────────────────────────────────────────────────
    matrix_path = os.path.abspath(args.matrix)
    if not os.path.isfile(matrix_path):
        sys.exit(f"[ERROR] Matrix edge list not found: {matrix_path}")
    print(f"[INFO] Loading matrix     : {matrix_path}")
    with open(matrix_path, "r", encoding="utf-8") as f:
        matrix_edge_list = json.load(f)

    # 3. Load payload / input data ────────────────────────────────────────────
    payload_path = os.path.abspath(args.payload)
    if not os.path.isfile(payload_path):
        sys.exit(f"[ERROR] Payload JSON not found: {payload_path}")
    print(f"[INFO] Loading payload    : {payload_path}")
    with open(payload_path, "r", encoding="utf-8") as f:
        input_data = json.load(f)

    # 4. Run the solver ───────────────────────────────────────────────────────
    print("\n[INFO] Starting solver …\n")
    t0 = time.time()
    try:
        result, score, winner = solve_vrp(input_data, matrix_edge_list, file_bytes)
    except Exception as exc:
        sys.exit(f"[ERROR] Solver raised an exception:\n  {exc}")
    elapsed = time.time() - t0
    print(f"\n[INFO] Solver finished in {elapsed:.2f}s")

    # 5. Write output ─────────────────────────────────────────────────────────
    output_path = os.path.abspath(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"[INFO] Result saved to    : {output_path}")

    # 6. Quick summary ────────────────────────────────────────────────────────
    vehicles_used = len(result.get("vehicles", []))
    total_cost    = result.get("summary", {}).get("total_cost_all_vehicles", "N/A")
    total_time    = score.get("total_time_min", "N/A")
    objective     = score.get("objective", "N/A")
    print("\n" + "-" * 30 + " Summary " + "-" * 30)
    print(f"   Vehicles used : {vehicles_used}")
    print(f"   Total cost    : {total_cost}")
    print(f"   Total time    : {total_time:.2f} min" if isinstance(total_time, (int, float)) else f"   Total time    : {total_time}")
    print(f"   Objective     : {objective:.2f}" if isinstance(objective, (int, float)) else f"   Objective     : {objective}")
    print("-" * 61 + "\n")


if __name__ == "__main__":
    main()