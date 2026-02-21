import requests
import json
import time

INPUT_FILE = 'TestCase_TC01_parsed.json' 
API_URL = "http://localhost:8080/process-routes"

def test_pipeline():
    # 1. Load Data
    try:
        with open(INPUT_FILE, 'r') as f:
            payload = json.load(f)
        print(f"Loaded {len(payload['employees'])} employees and {len(payload['vehicles'])} vehicles.")
    except FileNotFoundError:
        print(f"❌ File not found: {INPUT_FILE}")
        return

    # 2. Send Request
    print("🚀 Sending request to Backend...")
    start = time.time()
    
    try:
        response = requests.post(API_URL, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            meta = data.get('metadata', {})
            result = data.get('data', {})
            vehicles = result.get('vehicles', [])

            print(f"\n✅ Success!")
            print(f"⏱️  Time Taken: {meta.get('time_taken')}")
            print(f"🚚 Processed Vehicles: {len(vehicles)}")
            print(f"💰 Total Cost: {result.get('summary', {}).get('total_cost_all_vehicles')}")

            # Check Sample
            if vehicles:
                v1 = vehicles[0]
                print(f"\n--- Sample Vehicle: {v1.get('vehicle_id')} ---")
                geoms = v1.get('route_geometry', [])
                print(f"Route Segments: {len(geoms)}")
                if geoms:
                    print(f"First Segment Polyline: {geoms[0].get('geometry')[:30]}...")
            
            # Save Debug
            with open('final_response.json', 'w') as f:
                json.dump(data, f, indent=4)
            print("\nSaved full response to 'final_response.json'")
            
        else:
            print(f"❌ Error {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"❌ Connection Failed: {e}")

if __name__ == "__main__":
    test_pipeline()