import asyncio
import polyline
from router import RouteService

async def enrich_with_geometries(schedule_data, input_payload):
    """
    1. Parse unique route tags from the schedule.
    2. Lookup coordinates.
    3. Fetch & Compress geometries.
    4. Replace 'routes' with 'route_geometry'.
    """
    
    # --- Step 1: Build Coordinate Map ---
    coord_map = {}
    if input_payload.employees:
        coord_map['office'] = (input_payload.employees[0].drop_lat, input_payload.employees[0].drop_lng)
    
    for v in input_payload.vehicles:
        coord_map[v.vehicle_id] = (v.current_lat, v.current_lng)
        
    for e in input_payload.employees:
        coord_map[e.employee_id] = (e.pickup_lat, e.pickup_lng)

    # --- Step 2: Identify Unique Segments ---
    unique_tags = set()
    vehicles_list = schedule_data.get("vehicles", [])
    
    # Extract tags from the existing 'routes' list
    for v_data in vehicles_list:
        for tag in v_data.get("routes", []):
            unique_tags.add(tag)

    if not unique_tags:
        return schedule_data

    print(f"[GEO] Fetching geometry for {len(unique_tags)} segments...")

    # --- Step 3: Fetch Data ---
    router = RouteService(max_concurrency=50)
    tasks = []

    def parse_tag(tag):
        parts = tag.split('_')
        # IMPORTANT: This assumes IDs do NOT contain underscores.
        # If IDs ever contain underscores (e.g. "EMP_001"), this will break.
        if len(parts) == 2: return parts[0], parts[1]
        if "office" in tag:
            if tag.startswith("office_"): return "office", tag.replace("office_", "", 1)
            if tag.endswith("_office"): return tag.rsplit("_office", 1)[0], "office"
        # Fallback: log a warning for ambiguous tags
        print(f"Ambiguous route tag: '{tag}' — assuming first two parts")
        return parts[0], parts[1]

    for tag in unique_tags:
        src_id, dst_id = parse_tag(tag)
        src = coord_map.get(src_id)
        dst = coord_map.get(dst_id)
        
        if src and dst:
            tasks.append(router.fetch_geometry_safe(tag, src, dst))
        else:
            print(f"[ERROR] Missing coords: {tag}")

    results_list = await asyncio.gather(*tasks)
    await router.close()

    # # --- Step 4: Compress & Store (Old) ---
    # geometry_map = {}
    # for tag, coords in results_list:
    #     if coords:
    #         # OSRM = [lon, lat] -> Polyline = [lat, lon]
    #         swapped_coords = [(p[1], p[0]) for p in coords]
    #         geometry_map[tag] = polyline.encode(swapped_coords)
    #     else:
    #         geometry_map[tag] = ""

    # --- Step 4: Compress & Store (New) ---
    geometry_map = {}
    for tag, coords in results_list:
        if coords:
            # 1. OSRM = [lon, lat] -> Polyline = [lat, lon]
            swapped_coords = [(p[1], p[0]) for p in coords]
            
            # 2. Look up exact coordinates to fix the Snapping Gap
            src_id, dst_id = parse_tag(tag)
            src = coord_map.get(src_id)
            dst = coord_map.get(dst_id)
            
            # 3. Inject exact points to visually bridge the route to the markers
            if src:
                swapped_coords.insert(0, src)
            if dst:
                swapped_coords.append(dst)
                
            # 4. Encode the complete path
            geometry_map[tag] = polyline.encode(swapped_coords)
        else:
            geometry_map[tag] = ""

    # --- Step 5: Inject into Final JSON ---
    for v_data in vehicles_list:
        v_data["route_geometry"] = []
        
        # Iterate over old 'routes' to preserve order
        for tag in v_data.get("routes", []):
            v_data["route_geometry"].append({
                "segment_id": tag,
                "geometry": geometry_map.get(tag, "")
            })
            
        # Remove the old 'routes' key to clean up
        if "routes" in v_data:
            del v_data["routes"]

    return schedule_data