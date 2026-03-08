import math
from dataclasses import dataclass
from datetime import time as dtime, datetime
import pandas as pd
import os
from io import BytesIO

@dataclass
class Employee:
    id: str
    priority: int
    pickup_lat: float
    pickup_lng: float
    drop_lat: float
    drop_lng: float
    earliest_pickup: float
    latest_drop: float
    max_delay: float
    vehicle_preference: str
    sharing_preference: str

@dataclass
class Vehicle:
    id: str
    capacity: int
    speed: float
    cost_per_km: float
    category: str
    start_lat: float
    start_lng: float
    available_time: float

class DistanceMatrix:
    def __init__(self, matrix_edge_list):
        self.data = {item['id']: item for item in matrix_edge_list}
        # Store location coordinates extracted from identifiable entries
        # so we can compute haversine fallbacks when edges are missing.
        self._loc_coords: dict = {}  # id -> (lat, lng)

    def register_location(self, loc_id: str, lat: float, lng: float):
        """Register a location's coordinates for haversine fallback."""
        self._loc_coords[loc_id] = (lat, lng)

    def get_dist_dur(self, from_id: str, to_id: str, speed_kmph: float = 30.0):
        if from_id == to_id:
            return 0.0, 0.0
        
        key = f"{from_id}_{to_id}"
        if key in self.data:
            return self.data[key]['distance_meters'] / 1000.0, self.data[key]['duration_seconds'] / 60.0
        
        # Try reverse just in case, though OSRM is usually directed
        rev_key = f"{to_id}_{from_id}"
        if rev_key in self.data:
            return self.data[rev_key]['distance_meters'] / 1000.0, self.data[rev_key]['duration_seconds'] / 60.0

        # Haversine-based fallback (consistent with ALNS solver)
        # Apply 1.3x road-factor: real roads are ~20-40% longer than straight-line
        if from_id in self._loc_coords and to_id in self._loc_coords:
            lat1, lng1 = self._loc_coords[from_id]
            lat2, lng2 = self._loc_coords[to_id]
            km = haversine(lat1, lng1, lat2, lng2) * 1.3
            travel_min = (km / speed_kmph) * 60.0 if speed_kmph > 0 else 0.0
            return km, travel_min

        return 10.0, 30.0  # last resort default

def time_to_minutes(t):
    """Handle datetime.time, datetime.datetime, string, or float (fraction of day)"""
    if isinstance(t, str):
        try:
            h, m = t.split(":")[:2]
            return int(h) * 60 + int(m)
        except ValueError:
            return 0
    if isinstance(t, dtime):
        return t.hour * 60 + t.minute
    if isinstance(t, datetime):
        return t.hour * 60 + t.minute
    if isinstance(t, (int, float)):
        # If it's like 0.375 (Excel time fraction), convert to minutes
        if t < 10.0: # Heuristic for fraction of day
             return float(t) * 24 * 60
        return float(t)
    return 0

def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))

def load_data_from_bytes(file_bytes: bytes):
    excel_file = BytesIO(file_bytes)
    emp_df = pd.read_excel(excel_file, sheet_name='employees', engine='openpyxl')
    veh_df = pd.read_excel(excel_file, sheet_name='vehicles', engine='openpyxl')
    meta_df = pd.read_excel(excel_file, sheet_name='metadata', engine='openpyxl')
    
    # Parse max delays
    max_delays = {}
    for _, row in meta_df.iterrows():
        key = str(row['key'])
        if key.startswith('priority_') and key.endswith('_max_delay_min'):
            try:
                p = int(key.split('_')[1])
                max_delays[p] = float(row['value'])
            except:
                continue
    
    employees = {}
    for _, row in emp_df.iterrows():
        emp_id = str(row['employee_id'])
        priority = int(row['priority'])
        
        employees[emp_id] = Employee(
            id=emp_id,
            priority=priority,
            pickup_lat=float(row['pickup_lat']),
            pickup_lng=float(row['pickup_lng']),
            drop_lat=float(row['drop_lat']),
            drop_lng=float(row['drop_lng']),
            earliest_pickup=time_to_minutes(row['earliest_pickup']),
            latest_drop=time_to_minutes(row['latest_drop']),
            max_delay=max_delays.get(priority, 30),
            vehicle_preference=str(row['vehicle_preference']).lower(),
            sharing_preference=str(row['sharing_preference']).lower()
        )
    
    vehicles = {}
    for _, row in veh_df.iterrows():
        veh_id = str(row['vehicle_id'])
        vehicles[veh_id] = Vehicle(
            id=veh_id,
            capacity=int(row['capacity']),
            speed=float(row['avg_speed_kmph']),
            cost_per_km=float(row['cost_per_km']),
            category=str(row['category']).lower(),
            start_lat=float(row['current_lat']),
            start_lng=float(row['current_lng']),
            available_time=time_to_minutes(row['available_from'])
        )
        
    return employees, vehicles

def load_data(excel_path: str):
    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"{excel_path} not found")

    with open(excel_path, 'rb') as f:
        file_bytes = f.read()
    
    return load_data_from_bytes(file_bytes)
