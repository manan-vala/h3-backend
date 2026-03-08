import httpx
import asyncio
import logging
import random
import os

logger = logging.getLogger("celery_worker")

# Configuration
OSRM_BASE = os.getenv("OSRM_URL", "http://34.131.59.11:5000")
OSRM_TABLE_URL = f"{OSRM_BASE}/table/v1/driving/"
OSRM_ROUTE_URL = f"{OSRM_BASE}/route/v1/driving/"

class MatrixService:
    """
    Handles the OSRM Table API for the optimization phase.
    """
    def __init__(self, employees, vehicles):
        self.index_map = {}
        self.coords_list = []
        self.durations = []
        self.distances = []
        
        # 1. Build Coordinate Index
        if employees:
            self._add_point("office", employees[0].drop_lat, employees[0].drop_lng)
        for v in vehicles:
            self._add_point(v.vehicle_id, v.current_lat, v.current_lng)
        for e in employees:
            self._add_point(e.employee_id, e.pickup_lat, e.pickup_lng)

    def _add_point(self, id, lat, lng):
        if id not in self.index_map:
            self.index_map[id] = len(self.coords_list)
            self.coords_list.append(f"{lng},{lat}")

    async def fetch_matrix(self):
        coords_string = ";".join(self.coords_list)
        # Request table for all points
        url = f"{OSRM_TABLE_URL}{coords_string}?annotations=duration,distance"
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(url, timeout=30.0)
                if resp.status_code == 200:
                    data = resp.json()
                    self.durations = data['durations']
                    self.distances = data['distances']
                    logger.info(f"[Matrix] Fetched {len(self.coords_list)}x{len(self.coords_list)} matrix successfully.")
                    return True
                else:
                    logger.error(f"[Matrix] API Error: HTTP {resp.status_code} — {resp.text[:200]}")
                    return False
            except Exception as e:
                logger.error(f"[Matrix] Connection Error: {e}")
                return False

    def get_pair(self, id_from, id_to):
        idx_from = self.index_map.get(id_from)
        idx_to = self.index_map.get(id_to)
        if idx_from is None or idx_to is None: return None
        return {
            "distance_meters": self.distances[idx_from][idx_to],
            "duration_seconds": self.durations[idx_from][idx_to]
        }


class RouteService:
    """
    Handles fetching geometry with Throttling and Retries.
    Prevents 'DDoS-ing' the server.
    """
    def __init__(self, max_concurrency=50):
        # 1. Semaphore to limit parallel requests (Throttling)
        self.sem = asyncio.Semaphore(max_concurrency)
        
        # 2. Optimized Client (Connection Pooling)
        self.client = httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=max_concurrency),
            timeout=10.0
        )

    async def fetch_geometry_safe(self, tag, src_coords, dst_coords, retries=3):
        """
        Fetches geometry with automatic retries and error handling.
        """
        # OSRM expects: lon,lat;lon,lat
        coords_str = f"{src_coords[1]},{src_coords[0]};{dst_coords[1]},{dst_coords[0]}"
        url = f"{OSRM_ROUTE_URL}{coords_str}?overview=full&geometries=geojson"
        
        async with self.sem: # Wait here if 50 requests are already running
            for attempt in range(retries):
                try:
                    resp = await self.client.get(url)
                    
                    if resp.status_code == 200:
                        routes = resp.json().get('routes', [])
                        if routes:
                            return tag, routes[0]['geometry']['coordinates']
                        else:
                            return tag, None # No route found (water?)

                    elif resp.status_code >= 500:
                        logger.warning(f"[Geometry] Server Error {tag} (Attempt {attempt+1}/{retries})")
                    else:
                        logger.warning(f"[Geometry] Bad Request {tag}: {resp.status_code}")
                        return tag, None

                except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout) as e:
                    logger.warning(f"[Geometry] Network Error {tag} (Attempt {attempt+1}/{retries}): {e}")

                # If we failed, wait 1 second before retrying (Backoff)
                if attempt < retries - 1:
                    await asyncio.sleep(1)

        logger.error(f"[Geometry] Failed to fetch {tag} after {retries} attempts.")
        return tag, None

    async def close(self):
        await self.client.aclose()