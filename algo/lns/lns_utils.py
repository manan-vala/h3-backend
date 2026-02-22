import math
import json
from dataclasses import dataclass
from datetime import time as dtime, datetime
import pandas as pd
import os

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

def load_data(excel_path: str):
    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"{excel_path} not found")

    emp_df = pd.read_excel(excel_path, sheet_name='employees')
    veh_df = pd.read_excel(excel_path, sheet_name='vehicles')
    meta_df = pd.read_excel(excel_path, sheet_name='metadata')
    
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

class DistanceMatrix:
    def __init__(self, matrix_path: str):
        if not os.path.exists(matrix_path):
            self.matrix = {}
        else:
            with open(matrix_path, 'r') as f:
                data = json.load(f)
            self.matrix = {item['id']: item for item in data}

    def get_data(self, id1: str, id2: str):
        if id1 == id2:
            return 0.0, 0.0
        
        # In the matrix, office is sometimes used as 'office'
        # Vehicles are V01, Employees are E01 etc.
        key1 = f"{id1}_{id2}"
        key2 = f"{id2}_{id1}"
        
        if key1 in self.matrix:
            item = self.matrix[key1]
            return item['distance_meters'] / 1000.0, item['duration_seconds'] / 60.0
        elif key2 in self.matrix:
            item = self.matrix[key2]
            return item['distance_meters'] / 1000.0, item['duration_seconds'] / 60.0
        
        # Fallback to haversine if not found? 
        # The user specifically asked to use the matrix.
        return None, None

import json
