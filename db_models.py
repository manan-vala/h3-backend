"""
db_models.py – SQLAlchemy ORM models for application data.

All models use the shared Base from database.py so that
Base.metadata.create_all() picks them up automatically.
"""

from sqlalchemy import Column, Integer, Float, String, DateTime, func
from database import Base


class OptimizationRunLog(Base):
    """Stores summary data for each successful optimization run (capped at 1000)."""

    __tablename__ = "optimization_run_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # --- Input metadata ---
    filename = Column(String, nullable=False, default="unknown")
    num_employees = Column(Integer, nullable=False)
    num_vehicles = Column(Integer, nullable=False)

    # --- Result metadata ---
    winner_algorithm = Column(String, nullable=False)          # LNS | ALNS | VROOM
    employees_served = Column(Integer, nullable=False)
    hard_violations = Column(Integer, nullable=False, default=0)
    soft_violations = Column(Integer, nullable=False, default=0)
    objective_score = Column(Float, nullable=False)
    total_cost = Column(Float, nullable=False)
    total_time_min = Column(Float, nullable=False)

    # --- Timing ---
    algo_duration_seconds = Column(Float, nullable=True)       # Solver step only
    total_duration_seconds = Column(Float, nullable=True)      # Full pipeline

    # --- Traceability ---
    celery_task_id = Column(String, nullable=True)
    vehicles_in_solution = Column(Integer, nullable=True)

    def __repr__(self):
        return (
            f"<OptimizationRunLog id={self.id} file={self.filename!r} "
            f"winner={self.winner_algorithm} served={self.employees_served}>"
        )
