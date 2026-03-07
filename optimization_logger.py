"""
optimization_logger.py – Safely log successful optimization runs to PostgreSQL.

All operations are wrapped in try/except so a DB failure NEVER crashes the
optimization pipeline.  The log table is capped at MAX_LOG_ROWS (default 1000);
oldest entries beyond the cap are deleted after each insert.
"""

import logging
from typing import Optional

from sqlalchemy import delete, select, func
from database import SessionLocal
from db_models import OptimizationRunLog

logger = logging.getLogger("optimization_logger")

MAX_LOG_ROWS = 1000


def log_optimization_run(
    *,
    filename: str,
    num_employees: int,
    num_vehicles: int,
    winner_algorithm: str,
    employees_served: int,
    hard_violations: int,
    soft_violations: int,
    objective_score: float,
    total_cost: float,
    total_time_min: float,
    algo_duration_seconds: Optional[float] = None,
    total_duration_seconds: Optional[float] = None,
    celery_task_id: Optional[str] = None,
    vehicles_in_solution: Optional[int] = None,
) -> None:
    """Insert a new optimization run log and enforce the row cap.

    This function opens its own DB session so it can be called from any
    context (Celery worker, CLI script, etc.) without needing a FastAPI
    dependency-injected session.
    """
    try:
        session = SessionLocal()
        try:
            row = OptimizationRunLog(
                filename=filename,
                num_employees=num_employees,
                num_vehicles=num_vehicles,
                winner_algorithm=winner_algorithm,
                employees_served=employees_served,
                hard_violations=hard_violations,
                soft_violations=soft_violations,
                objective_score=objective_score,
                total_cost=total_cost,
                total_time_min=total_time_min,
                algo_duration_seconds=algo_duration_seconds,
                total_duration_seconds=total_duration_seconds,
                celery_task_id=celery_task_id,
                vehicles_in_solution=vehicles_in_solution,
            )
            session.add(row)
            session.commit()
            logger.info(f"Logged optimization run: {filename} | winner={winner_algorithm}")

            _enforce_log_limit(session)
        finally:
            session.close()
    except Exception:
        # Never let a logging failure bubble up and kill the pipeline
        logger.exception("Failed to log optimization run (non-fatal)")


def _enforce_log_limit(session, max_rows: int = MAX_LOG_ROWS) -> None:
    """Delete the oldest rows if the table exceeds *max_rows*."""
    try:
        total = session.scalar(select(func.count()).select_from(OptimizationRunLog))
        if total is None or total <= max_rows:
            return

        excess = total - max_rows
        # Sub-query: IDs of the oldest `excess` rows
        oldest_ids_sq = (
            select(OptimizationRunLog.id)
            .order_by(OptimizationRunLog.created_at.asc())
            .limit(excess)
            .subquery()
        )
        session.execute(
            delete(OptimizationRunLog).where(OptimizationRunLog.id.in_(select(oldest_ids_sq)))
        )
        session.commit()
        logger.info(f"Pruned {excess} old optimization log(s) (cap={max_rows})")
    except Exception:
        session.rollback()
        logger.exception("Failed to enforce log limit (non-fatal)")
