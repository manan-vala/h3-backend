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
from optimization_logger import log_optimization_run

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
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery_app = Celery("opti_worker", broker=REDIS_URL, backend=REDIS_URL)

def run_async(coro):
    """Safely bridge async code into a sync Celery worker context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@celery_app.task(bind=True)
def process_optimization_task(self, payload_dict: dict, file_path: str, file_bytes_b64: str):
    """
    Background task to run the heavy routing algorithm.
    """
    task_start = time.time()
    logger.info(f"=== TASK STARTED (ID: {self.request.id}) ===")
    logger.info(f"Employees: {len(payload_dict.get('employees', []))}, Vehicles: {len(payload_dict.get('vehicles', []))}")

    # Decode base64 file data back to raw bytes
    import base64
    file_bytes = base64.b64decode(file_bytes_b64)

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
        result_json, _score, winner_algorithm = solve_vrp(payload_dict, matrix_edge_list, file_bytes)
        vehicles_count = len(result_json.get("vehicles", []))
        algo_elapsed = time.time() - step_start
        logger.info(f"[Step 4/5] Solver done. {vehicles_count} vehicles in result ({algo_elapsed:.1f}s)")

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

        # --- Log successful run to PostgreSQL ---
        try:
            log_optimization_run(
                filename=payload_dict.get("filename", "unknown"),
                num_employees=len(payload_dict.get("employees", [])),
                num_vehicles=len(payload_dict.get("vehicles", [])),
                winner_algorithm=winner_algorithm,
                employees_served=_score.get("served_count", 0),
                hard_violations=_score.get("hard_violations", 0),
                soft_violations=_score.get("soft_violations", 0),
                objective_score=_score.get("objective", 0.0),
                total_cost=_score.get("total_cost", 0.0),
                total_time_min=_score.get("total_time_min", 0.0),
                algo_duration_seconds=round(algo_elapsed, 2),
                total_duration_seconds=round(total_time, 2),
                celery_task_id=self.request.id,
                vehicles_in_solution=vehicles_count,
            )
        except Exception as log_err:
            logger.warning(f"Non-fatal: failed to log optimization run: {log_err}")

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

@celery_app.task
def cleanup_orphaned_files():
    """
    Background periodic task to delete files in temp_uploads older than 24 hours.
    Requires Celery Beat to be running to trigger periodically.
    """
    try:
        cutoff = time.time() - (24 * 3600)  # 24 hours ago
        temp_dir = "temp_uploads"
        if not os.path.exists(temp_dir):
            return "temp_uploads does not exist"
            
        deleted_count = 0
        for filename in os.listdir(temp_dir):
            path = os.path.join(temp_dir, filename)
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                try:
                    os.remove(path)
                    logger.info(f"Deleted old orphaned file: {path}")
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"Error deleting old file {path}: {e}")
                    
        return f"Cleaned up {deleted_count} old files."
    except Exception as e:
        logger.error(f"Error in cleanup task: {e}")
        raise e

# Setup Celery Beat schedule for native task scheduling
celery_app.conf.beat_schedule = {
    'cleanup-temp-files-every-12-hours': {
        'task': 'worker.cleanup_orphaned_files',
        'schedule': 43200.0,  # Run every 12 hours (in seconds)
    },
}
