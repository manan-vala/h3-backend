# test_routes.py
import json
import os
from fastapi import APIRouter, HTTPException
from algo.solver import solve_vrp

# Create a router specifically for testing endpoints
router = APIRouter(
    prefix="/test",
    tags=["testing"]
)

@router.get("/run-solver")
def run_test_solver_native():
    try:
        # 1. Define paths to the test files. 
        # __file__ refers to test_routes.py, so this resolves to the root folder.
        base_dir = os.path.dirname(os.path.abspath(__file__))
        excel_path = os.path.join(base_dir, "algo", "templts", "TestCase_TC03.xlsx")
        matrix_path = os.path.join(base_dir, "algo", "templts", "matrix_edge_list.json")
        payload_path = os.path.join(base_dir, "algo", "templts", "payload_dict.json")

        # 2. Check if files exist before trying to open them
        if not os.path.exists(excel_path):
            raise HTTPException(status_code=404, detail=f"Excel file not found at {excel_path}")
        if not os.path.exists(matrix_path) or not os.path.exists(payload_path):
            raise HTTPException(status_code=404, detail="Required JSON payload/matrix files not found")

        # 3. Read the Excel file as bytes (just like run_solver.py does)
        with open(excel_path, "rb") as f:
            file_bytes = f.read()

        # 4. Load the required JSON configuration files
        with open(matrix_path, "r", encoding="utf-8") as f:
            matrix_edge_list = json.load(f)

        with open(payload_path, "r", encoding="utf-8") as f:
            input_data = json.load(f)

        # 5. Execute the solver directly in memory
        result, score, winner = solve_vrp(input_data, matrix_edge_list, file_bytes)

        # 6. Return the results
        return {
            "status": "success",
            "winner_algorithm": winner,
            "score": score,
            "result": result
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))