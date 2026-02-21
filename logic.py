from itertools import combinations

def generate_routes(data, matrix_service):
    """
    Uses the pre-fetched Matrix to generate all pair data instantly.
    """
    employees = data.employees
    vehicles = data.vehicles
    
    results = []

    # Helper to construct the final object
    def add_result(id, type, id_from, id_to):
        data = matrix_service.get_pair(id_from, id_to)
        if data:
            results.append({
                "id": id,
                "type": type,
                "distance_meters": data["distance_meters"],
                "duration_seconds": data["duration_seconds"],
                # NOTE: Table API does not return geometry. 
                # We save bandwidth by not fetching it for 26,000 pairs.
                "geometry": None 
            })

    # 1. Employee - Employee (Combinations)
    for e1, e2 in combinations(employees, 2):
        add_result(
            f"{e1.employee_id}_{e2.employee_id}", "emp-emp",
            e1.employee_id, e2.employee_id
        )

    # 2. Vehicle - Employee (Product)
    for v in vehicles:
        for e in employees:
            add_result(
                f"{v.vehicle_id}_{e.employee_id}", "veh-emp",
                v.vehicle_id, e.employee_id
            )

    # 3. Office - Vehicle
    for v in vehicles:
        add_result(f"office_{v.vehicle_id}", "office-veh", "office", v.vehicle_id)

    # 4. Office - Employee
    for e in employees:
        add_result(f"office_{e.employee_id}", "office-emp", "office", e.employee_id)

    return results