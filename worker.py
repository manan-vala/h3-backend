import asyncio
import json
import logging
import os
import time
import traceback
from celery import Celery
from models import OptimizationRequest
from router import MatrixService
from logic import generate_routes
from geometry_processor import enrich_with_geometries
from algo.solver import solve_vrp

# --- File-based Logging ---
# All logs go to worker_debug.log so you can always check what happened
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("worker_debug.log", mode="a"),
        logging.StreamHandler()  # Also print to terminal
    ]
)
logger = logging.getLogger("celery_worker")

# Initialize Celery pointing to local Redis
celery_app = Celery(
    "opti_worker",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

def run_async(coro):
    """Safely bridge async code into a sync Celery worker context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@celery_app.task(bind=True)
def process_optimization_task(self, payload_dict: dict, file_path: str):
    """
    Background task to run the heavy routing algorithm.
    """
    task_start = time.time()
    logger.info(f"=== TASK STARTED (ID: {self.request.id}) ===")
    logger.info(f"Employees: {len(payload_dict.get('employees', []))}, Vehicles: {len(payload_dict.get('vehicles', []))}")

    try:
        # 1. Reconstruct Pydantic payload
        logger.info("[Step 1/5] Reconstructing Pydantic payload...")
        payload = OptimizationRequest(**payload_dict)
        logger.info(f"[Step 1/5] Done. ({time.time() - task_start:.1f}s elapsed)")

        # (Optional) Read file bytes if you plan to update solver.py to use the Excel file
        # with open(file_path, "rb") as f:
        #     file_bytes = f.read()

        # 2. Fetch Matrix (Bridging Async to Sync)
        logger.info("[Step 2/5] Fetching OSRM distance matrix...")
        step_start = time.time()
        matrix = MatrixService(payload.employees, payload.vehicles)
        success = run_async(matrix.fetch_matrix())
        logger.info(f"[Step 2/5] Matrix fetch {'SUCCEEDED' if success else 'FAILED'} ({time.time() - step_start:.1f}s)")
        
        if not success:
            raise Exception("Failed to fetch matrix from OSRM. Check if OSRM server is reachable.")

        # 3. Transform Matrix
        logger.info("[Step 3/5] Generating route edge list...")
        step_start = time.time()
        matrix_edge_list = generate_routes(payload, matrix)
        logger.info(f"[Step 3/5] Generated {len(matrix_edge_list)} edges ({time.time() - step_start:.1f}s)")

        # 4. Run Optimization Algorithm
        logger.info("[Step 4/5] Running VRP solver...")
        step_start = time.time()
        result_json = solve_vrp(payload_dict, matrix_edge_list)
        vehicles_count = len(result_json.get("vehicles", []))
        logger.info(f"[Step 4/5] Solver done. {vehicles_count} vehicles in result ({time.time() - step_start:.1f}s)")

        # 5. Fetch Geometries (Bridging Async to Sync)
        logger.info("[Step 5/5] Fetching route geometries from OSRM...")
        step_start = time.time()
        final_json = run_async(enrich_with_geometries(result_json, payload))
        logger.info(f"[Step 5/5] Geometries done ({time.time() - step_start:.1f}s)")

        # Inject extra summary fields
        total_time = time.time() - task_start
        total_vehicle_time = sum(v.get("total_time_minutes", 0) for v in final_json.get("vehicles", []))

        final_json.setdefault("summary", {})
        final_json["summary"]["total_algo_time_seconds"] = round(total_time, 2)
        final_json["summary"]["total_vehicle_time_minutes"] = round(total_vehicle_time, 2)

        # Ensure result is JSON-serializable for Redis
        serializable_result = json.loads(json.dumps(final_json))

        logger.info(f"=== TASK COMPLETED (ID: {self.request.id}) Total: {total_time:.1f}s ===")
        return serializable_result

    except Exception as e:
        logger.error(f"=== TASK FAILED (ID: {self.request.id}) ===")
        logger.error(f"Error: {e}")
        logger.error(traceback.format_exc())
        raise e
        
    finally:
        # Cleanup: Always delete the temporary Excel file
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up temp file: {file_path}")