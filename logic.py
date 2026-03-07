from itertools import permutations, product

def generate_routes(data, matrix_service):
    """
    Uses the pre-fetched Matrix to generate all pair data instantly.
    """
    employees = data.employees
    vehicles = data.vehicles
    
    results = []

    # Helper to construct the final object
    def add_result(id, type, id_from, id_to):
        pair_data = matrix_service.get_pair(id_from, id_to)
        if pair_data:
            results.append({
                "id": id,
                "type": type,
                "distance_meters": pair_data["distance_meters"],
                "duration_seconds": pair_data["duration_seconds"],
                "geometry": None 
            })

    # 1. Employee - Employee (Permutations for both directions)
    emp_ids = [e.employee_id for e in employees]
    for e1_id, e2_id in permutations(emp_ids, 2):
        add_result(f"{e1_id}_{e2_id}", "emp-emp", e1_id, e2_id)

    # 2. Vehicle - Employee
    for v in vehicles:
        for e in employees:
            add_result(f"{v.vehicle_id}_{e.employee_id}", "veh-emp", v.vehicle_id, e.employee_id)

    # 3. Employee - Office
    for e in employees:
        add_result(f"{e.employee_id}_office", "emp-office", e.employee_id, "office")

    # 4. Office - Employee
    for e in employees:
        add_result(f"office_{e.employee_id}", "office-emp", "office", e.employee_id)

    # 5. Vehicle - Office
    for v in vehicles:
        add_result(f"{v.vehicle_id}_office", "veh-office", v.vehicle_id, "office")
        
    # 6. Office - Vehicle
    for v in vehicles:
        add_result(f"office_{v.vehicle_id}", "office-veh", "office", v.vehicle_id)

    return results