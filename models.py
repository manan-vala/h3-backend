from pydantic import BaseModel, Field
from typing import List, Optional

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

class OptimizationRequest(BaseModel):
    employees: List[Employee]
    vehicles: List[Vehicle]
    
    filename: Optional[str] = "unknown_case"