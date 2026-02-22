from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from models import OptimizationRequest
from router import MatrixService
from logic import generate_routes
from geometry_processor import enrich_with_geometries
from algo.solver import solve_vrp
import time
import json
import os
from fastapi.middleware.cors import CORSMiddleware
from auth import router as auth_router, get_current_user
from io import BytesIO

app = FastAPI()

#For Auth
app.include_router(auth_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth in testing phase
# @app.post("/process-routes", dependencies=[Depends(get_current_user)])
@app.post("/process-routes")
async def process_routes(
    json_data: str = Form(...),
    file: UploadFile = File(...)
):
    # Parse the JSON string into our Pydantic model
    try:
        raw = json.loads(json_data)
        payload = OptimizationRequest(**raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in json_data: {e}")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {e}")

    # print(payload)
    start_time = time.time()
    
    # 1. Fetch Matrix (Math) - Fast
    #    This gets the 2D grid from OSRM into memory
    matrix = MatrixService(payload.employees, payload.vehicles)
    success = await matrix.fetch_matrix()
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to fetch matrix from OSRM")

    # 2. Transform Matrix for Solver
    #    Converts 2D grid -> List of Edges (e.g. [{'id': 'V01_E01', 'distance': 500}, ...])
    #    This matches the 'TC01_results.json' format the algo expects.
    matrix_edge_list = generate_routes(payload, matrix)
    # print(json.dumps(matrix_edge_list, indent=2))
    # print("space")
    
    # with open("matrix_edge_list.json", "w") as f:
    #     json.dump(matrix_edge_list, f, indent=2)

    # 3. Run Optimization (The Algo)
    #    Passes the parsed input + the edge list + the original Excel file
    try:
        # Convert Pydantic model to dict for the solver
        payload_dict = payload.model_dump()
        # print(json.dumps(payload_dict, indent=2))
        
        # with open("payload_dict.json", "w") as f:
        #     json.dump(payload_dict, f, indent=2)

        # Read the uploaded Excel file into bytes for the solver
        file_bytes = await file.read()
        file_io = BytesIO(file_bytes)
            
        result_json = solve_vrp(payload_dict, matrix_edge_list, file_bytes)
        
        # with open("algo_output.json", "w") as f:
        #     json.dump(result_json, f, indent=2)
            
    except Exception as e:
        print(f"Algorithm Error: {e}")
        raise HTTPException(status_code=500, detail=f"Algorithm failed: {str(e)}")

    # 4. Fetch Geometries (Visuals)
    #    Takes the algo output (routes list) and fetches Polyline strings from OSRM
    final_json = await enrich_with_geometries(result_json, payload)

    processing_time = time.time() - start_time
    
    # 5. Final Output
    filename = getattr(payload, 'filename', 'unknown_case')
    output_filename = f"{filename}_results.json"
    
    vehicle_count = len(final_json.get("vehicles", []))

    final_output = {
        "status": "success",
        "metadata": {
            "processed_pairs": vehicle_count,
            "time_taken": f"{processing_time:.2f}s"
        },
        "data": final_json
    }

    # Save locally (Optional)
    try:
        with open(output_filename, 'w') as f:
            json.dump(final_output, f, indent=4)
    except:
        pass

    return final_output