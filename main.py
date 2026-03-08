from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from models import OptimizationRequest
from celery.result import AsyncResult
from auth import router as auth_router, get_current_user
from test_routes import router as test_router
from worker import celery_app, process_optimization_task
from database import get_db
from db_models import OptimizationRunLog  # ensures table is registered with Base
from sqlalchemy.orm import Session
import json
import logging
import os
import uuid

# --- File-based Logging (shared with worker) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("worker_debug.log", mode="a"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("fastapi_main")

app = FastAPI()

# For Auth
app.include_router(auth_router)

# For Testing
# Remove in production
app.include_router(test_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_CONCURRENT_JOBS = 4  # same number as --concurrency

def get_active_job_count() -> int:
    inspector = celery_app.control.inspect(timeout=2.0)
    active = inspector.active() or {}
    reserved = inspector.reserved() or {}
    
    active_count = sum(len(tasks) for tasks in active.values())
    reserved_count = sum(len(tasks) for tasks in reserved.values())
    return active_count + reserved_count


# Ensure the temporary directory exists for storing uploaded files
TEMP_DIR = "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)


# --- Health Check: Verify Redis is alive ---
@app.get("/health")
async def health_check():
    try:
        # Ping Redis through Celery's connection
        celery_app.control.ping(timeout=2.0)
        return {"status": "ok", "redis": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "degraded", "redis": f"error: {e}"}


# Endpoint 1: Receive the payload and start the background job
# Auth in testing phase:
# @app.post("/process-routes/start", dependencies=[Depends(get_current_user)])
@app.post("/process-routes/start")
async def start_processing(
    json_data: str = Form(...),
    file: UploadFile = File(...)
):
    # Gate check
    # intended to show error when redis or celery worker is unreachable
    current_load = get_active_job_count()
    if current_load >= MAX_CONCURRENT_JOBS:
        raise HTTPException(
            status_code=429,
            detail=f"Server is at capacity ({MAX_CONCURRENT_JOBS} jobs running). Please try again later."
        )

    logger.info(f"[API] /process-routes/start called. File: {file.filename}")
    
    # 1. Parse the JSON string into our Pydantic model
    try:
        raw = json.loads(json_data)
        payload = OptimizationRequest(**raw)
        logger.info(f"[API] Parsed payload: {len(payload.employees)} employees, {len(payload.vehicles)} vehicles")
    except json.JSONDecodeError as e:
        logger.error(f"[API] JSON parse error: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON in json_data: {e}")
    except Exception as e:
        logger.error(f"[API] Validation error: {e}")
        raise HTTPException(status_code=422, detail=f"Validation error: {e}")

    # 2. Save the Excel file to disk temporarily 
    # (Since payloads can be up to 2MB, passing the file path to Celery is safest)
    unique_id = str(uuid.uuid4())
    temp_filename = f"{TEMP_DIR}/{unique_id}_{file.filename}"
    
    try:
        with open(temp_filename, "wb") as f:
            f.write(await file.read())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {e}")

    # 3. Serialize payload for Redis/Celery and dispatch
    try:
        payload_dict = payload.model_dump()
        # Read from the saved temp file (the UploadFile stream is already exhausted)
        with open(temp_filename, "rb") as fb:
            file_bytes = fb.read()
        # Base64-encode so the bytes survive Celery's JSON serializer
        import base64
        file_bytes_b64 = base64.b64encode(file_bytes).decode("ascii")
        task = process_optimization_task.delay(payload_dict, temp_filename, file_bytes_b64)
        logger.info(f"[API] Task dispatched to Celery. task_id={task.id}")
    except Exception as e:
        logger.error(f"[API] Failed to dispatch task to Celery: {e}")
        # Cleanup temp file if dispatch fails
        if os.path.exists(temp_filename):
            os.remove(temp_filename)
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {e}")

    # 4. Return receipt immediately
    return {
        "status": "queued", 
        "task_id": task.id,
        "message": "Optimization task started in the background."
    }


# Endpoint 2: Poll for the status of the job
# Auth in testing phase:
# @app.get("/process-routes/status/{task_id}", dependencies=[Depends(get_current_user)])
@app.get("/process-routes/status/{task_id}")
async def get_processing_status(task_id: str):
    task_result = AsyncResult(task_id, app=celery_app)
    logger.info(f"[API] Status poll for task_id={task_id}, state={task_result.state}")

    if task_result.state == 'PENDING' or task_result.state == 'STARTED':
        return {"status": "processing"}

    elif task_result.state == 'SUCCESS':
        return {
            "status": "completed",
            "result": task_result.result
        }

    elif task_result.state == 'FAILURE':
        return {
            "status": "failed",
            "error": str(task_result.info)
        }

    # Fallback for other states (e.g., REJECTED, REVOKED)
    return {"status": task_result.state.lower()}


# --- Optimization Run Logs ---
@app.get("/optimization-logs")
def get_optimization_logs(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Return recent optimization run logs, newest first."""
    rows = (
        db.query(OptimizationRunLog)
        .order_by(OptimizationRunLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "filename": r.filename,
            "num_employees": r.num_employees,
            "num_vehicles": r.num_vehicles,
            "winner_algorithm": r.winner_algorithm,
            "employees_served": r.employees_served,
            "hard_violations": r.hard_violations,
            "soft_violations": r.soft_violations,
            "objective_score": r.objective_score,
            "total_cost": r.total_cost,
            "total_time_min": r.total_time_min,
            "algo_duration_seconds": r.algo_duration_seconds,
            "total_duration_seconds": r.total_duration_seconds,
            "celery_task_id": r.celery_task_id,
            "vehicles_in_solution": r.vehicles_in_solution,
        }
        for r in rows
    ]