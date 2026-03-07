from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any

class MetaInfo(BaseModel):
    generated_at: str
    source_files: List[str]
    format: str

class Metadata(BaseModel):
    test_case_id: str
    city: str
    distance_method: str
    allow_external_maps: bool
    priority_1_max_delay_min: int
    priority_2_max_delay_min: int
    priority_3_max_delay_min: int
    priority_4_max_delay_min: int
    priority_5_max_delay_min: int
    objective_cost_weight: float
    objective_time_weight: float

class Baseline(BaseModel):
    employee_id: str
    baseline_cost: float
    baseline_time_min: float

def normalize_time(v: str) -> str:
    """Validator to convert HH:MM:SS to HH:MM to prevent solver.py crashes."""
    if v and v.count(':') == 2:
        return v.rsplit(':', 1)[0]
    return v

class Employee(BaseModel):
    employee_id: str
    pickup_lat: float
    pickup_lng: float
    drop_lat: float
    drop_lng: float
    priority: int
    earliest_pickup: str  # Format "HH:MM"
    latest_drop: str      # Format "HH:MM"
    vehicle_preference: str
    sharing_preference: str

    @field_validator('earliest_pickup', 'latest_drop', mode='before')
    @classmethod
    def trim_seconds(cls, v: Any) -> Any:
        if isinstance(v, str):
            return normalize_time(v)
        return v

class Vehicle(BaseModel):
    vehicle_id: str
    current_lat: float
    current_lng: float
    fuel_type: str
    vehicle_type: str
    capacity: int
    cost_per_km: float
    avg_speed_kmph: float
    available_from: str   # Format "HH:MM"
    category: str

    @field_validator('available_from', mode='before')
    @classmethod
    def trim_seconds(cls, v: Any) -> Any:
        if isinstance(v, str):
            return normalize_time(v)
        return v

class OptimizationRequest(BaseModel):
    employees: List[Employee]
    vehicles: List[Vehicle]
    
    filename: Optional[str] = "unknown_case"
    
    # New fields (optional for backward compatibility)
    meta_info: Optional[MetaInfo] = None
    metadata: Optional[Metadata] = None
    baseline: Optional[List[Baseline]] = None