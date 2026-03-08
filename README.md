# Route Optimization Engine — Backend

A production-grade Vehicle Routing Problem (VRP) optimization backend built with **FastAPI**, **Celery**, and **Redis**. The system accepts employee/vehicle data, fetches real-time road network distances from an **OSRM** server, runs three competing optimization algorithms in parallel (LNS, ALNS, VROOM), scores them against a unified feasibility checker, and returns the best solution with encoded road geometries for map rendering.

For detailed documentation on the solver algorithms themselves, see [`algo/README.md`](algo/README.md).

---

## Architecture Overview

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│   Client /   │ POST  │   FastAPI    │ queue  │    Celery    │
│   Frontend   │──────▶│   (main.py)  │───────▶│    Worker    │
│              │◀──────│   :8080      │◀───────│  (worker.py) │
│              │ poll   │              │ result  │              │
└──────────────┘       └──────┬───────┘       └──────┬───────┘
                              │                       │
                    ┌─────────▼─────────┐    ┌───────▼────────┐
                    │   PostgreSQL      │    │   Redis        │
                    │   (run logs,      │    │   (broker +    │
                    │    auth users)    │    │    result      │
                    └───────────────────┘    │    backend)    │
                                             └───────┬────────┘
                                                     │
                                            ┌────────▼────────┐
                                            │   OSRM Server   │
                                            │   (GCP VM)      │
                                            │   :5000         │
                                            └─────────────────┘
```

### Request Lifecycle

1. **Client** sends a `POST /process-routes/start` with a JSON payload (employees + vehicles) and an Excel file.
2. **FastAPI** validates the payload via Pydantic, saves the Excel file temporarily, base64-encodes it, and dispatches a Celery task. A concurrency gate rejects requests with HTTP 429 if the worker is at capacity.
3. **Celery Worker** picks up the task from Redis and executes a 5-step pipeline:
   - **Step 1** — Reconstruct the Pydantic payload from the serialized dict.
   - **Step 2** — Fetch an N×N distance/duration matrix from OSRM (`/table/v1/driving/`).
   - **Step 3** — Transform the matrix into a flat edge list consumed by the solvers.
   - **Step 4** — Run the VRP solver (`algo/solver.py`), which launches **LNS**, **ALNS**, and **VROOM** concurrently in a thread pool, scores all solutions via `feasibilityfinal.py`, and selects the winner. This is the compute-heavy step and can take **up to ~15 minutes**.
   - **Step 5** — Fetch route geometries from OSRM (`/route/v1/driving/`) for map rendering, with throttled concurrent requests (50 max).
4. **Client** polls `GET /process-routes/status/{task_id}` until the result is ready.
5. On success, the result is logged to **PostgreSQL** for historical tracking.

---

## Project Structure

```
h3-backend/
├── main.py                  # FastAPI application & endpoints
├── worker.py                # Celery worker, task definition, beat schedule
├── models.py                # Pydantic request/response models
├── router.py                # OSRM client — MatrixService & RouteService
├── logic.py                 # Edge list generation from distance matrix
├── geometry_processor.py    # Geometry enrichment (polyline encoding)
├── database.py              # SQLAlchemy engine, session, Base
├── db_models.py             # ORM model for optimization_run_logs
├── optimization_logger.py   # Safe DB logger with row-cap enforcement
├── auth.py                  # JWT authentication (register/login)
├── run_solver.py            # Standalone CLI to run the solver offline
├── debug_alns.py            # Diagnostic script for ALNS debugging
├── vroom_test.py            # Integration test for VROOM solver path
├── test_vroom_matrix.py     # Low-level pyvroom matrix format test
├── setup_vroom_env.bat      # Windows script to create isolated VROOM venv
├── algo/                    # Solver algorithms — see algo/README.md
│   ├── __init__.py
│   ├── solver.py
│   ├── lns_algo.py
│   ├── lns_local_search.py
│   ├── lns_simulator.py
│   ├── lns_utils.py
│   ├── 16-02.py
│   ├── feasibilityfinal.py
│   ├── vroom_solver.py
│   ├── vroom_bridge.py
│   ├── vroom_matrix_patch.py
│   ├── check_lns.py
│   └── templts/
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── _env.example
```

---

## Prerequisites

- **Python 3.9+** (3.10+ recommended; 3.12 works for everything except pyvroom)
- **Redis** (message broker + Celery result backend)
- **PostgreSQL 16** (optimization run logs + user auth)
- **OSRM server** deployed and accessible (default: `http://34.131.59.11:5000`)
- **Isolated Python venv** for pyvroom with `numpy < 2` (see [VROOM Setup](#vroom-setup))

---

## Quick Start

### 1. Clone and Install Dependencies

```bash
cd h3-backend
pip install -r requirements.txt
```

### 2. Set Up Environment Variables

Copy the example env file and fill in your values:

```bash
cp _env.example .env
```

| Variable           | Description                                       | Default                                                    |
|--------------------|---------------------------------------------------|------------------------------------------------------------|
| `REDIS_URL`        | Redis connection string                           | `redis://localhost:6379/0`                                 |
| `OSRM_URL`         | Base URL of the OSRM server                       | `http://34.131.59.11:5000`                                 |
| `SECRET_KEY`       | JWT signing key for authentication                | *(required)*                                               |
| `DATABASE_URL`     | PostgreSQL connection string                      | `postgresql+psycopg2://kriti:kriti_pwd@localhost:5432/routeopti` |
| `VROOM_PYTHON_EXE` | Path to the isolated VROOM Python interpreter     | Auto-detected from `vroom_env/`                            |

### 3. Start Infrastructure (Redis + PostgreSQL)

**Redis:**

```bash
docker run -d --name redis-dev -p 6379:6379 redis:latest
```

**PostgreSQL:**

```bash
docker run -d \
  --name routeopti-postgres \
  -e POSTGRES_USER=kriti \
  -e POSTGRES_PASSWORD=kriti_pwd \
  -e POSTGRES_DB=routeopti \
  -p 5432:5432 \
  -v pgdata:/var/lib/postgresql/data \
  --restart unless-stopped \
  postgres:16
```

Database tables (`users`, `optimization_run_logs`) are created automatically on first startup via `Base.metadata.create_all()`.

### 4. Start the Celery Worker

**Windows:**

```bash
celery -A worker.celery_app worker --loglevel=info --pool=solo --concurrency=4
```

**Linux:**

```bash
celery -A worker.celery_app worker --loglevel=info --pool=prefork --concurrency=2
```

> **Important:** When using `--concurrency=2` on Linux, set `MAX_CONCURRENT_JOBS = 2` in `main.py` to match, so the API gate check correctly rejects requests when the worker is at capacity.

### 5. Start the FastAPI Server

```bash
uvicorn main:app --reload --port 8080
```

The API is now live at `http://localhost:8080`. Interactive docs at `http://localhost:8080/docs`.

---

## Docker Compose (Full Stack)

To run the API and worker as containers with Redis:

```bash
docker-compose up --build
```

This starts three services: `redis`, `api` (port 8080), and `worker`. PostgreSQL and OSRM are expected to be running externally.

---

## API Endpoints

### Route Optimization

| Method | Path                              | Description                              |
|--------|-----------------------------------|------------------------------------------|
| POST   | `/process-routes/start`           | Submit an optimization job               |
| GET    | `/process-routes/status/{task_id}`| Poll job status (processing/completed/failed) |

**POST `/process-routes/start`** expects a multipart form with two fields:

- `json_data` (string) — JSON string matching the `OptimizationRequest` schema (employees, vehicles, optional metadata/baseline).
- `file` (binary) — The source Excel file (`.xlsx`) with three sheets: `employees`, `vehicles`, `metadata`.

The `metadata` sheet provides priority delay limits (`priority_N_max_delay_min`), objective weights (`objective_cost_weight`, `objective_time_weight`), and other configuration. Time fields (`earliest_pickup`, `latest_drop`, `available_from`) accept `HH:MM` or `HH:MM:SS` format — seconds are automatically trimmed by Pydantic validators.

**Response:**

```json
{
  "status": "queued",
  "task_id": "abc123-...",
  "message": "Optimization task started in the background."
}
```

**GET `/process-routes/status/{task_id}`** returns:

| State       | Response                                           |
|-------------|-----------------------------------------------------|
| Processing  | `{"status": "processing"}`                          |
| Completed   | `{"status": "completed", "result": { ... }}`        |
| Failed      | `{"status": "failed", "error": "..."}`              |

The completed `result` contains per-vehicle route assignments with step-by-step sequence, timing, cost, and polyline-encoded geometries for map rendering.

### Optimization Logs

| Method | Path                 | Description                                     |
|--------|----------------------|-------------------------------------------------|
| GET    | `/optimization-logs` | Paginated history of successful runs (newest first) |

Query params: `limit` (default 50, max 1000), `offset` (default 0). Returns algorithm winner, objective score, served count, violation counts, and timing.

### Authentication

| Method | Path        | Description                            |
|--------|-------------|----------------------------------------|
| POST   | `/register` | Create a new user, returns JWT token   |
| POST   | `/login`    | Authenticate, returns JWT token        |

> Auth is currently **disabled** on optimization endpoints (commented out) during the testing phase.

### Health

| Method | Path      | Description                  |
|--------|-----------|------------------------------|
| GET    | `/health` | Pings Redis via Celery       |

---

## Server Components

### Pydantic Models (`models.py`)

The `OptimizationRequest` model validates incoming data with automatic time format normalization — any `HH:MM:SS` values are trimmed to `HH:MM` via field validators to prevent downstream solver crashes. Key sub-models: `Employee` (pickup/drop coordinates, priority 1–5, time windows, vehicle/sharing preferences), `Vehicle` (location, fuel/vehicle type, capacity, cost per km, average speed, availability, category), plus optional `Metadata` and `Baseline`.

### OSRM Integration (`router.py`)

- **`MatrixService`** — builds a coordinate index from all employees, vehicles, and the office location, then fetches an N×N duration/distance matrix in a single OSRM `/table` API call (30s timeout).
- **`RouteService`** — fetches individual route geometries with semaphore-limited concurrency (default 50), connection pooling (20 keepalive), and automatic retries with 1-second backoff on server errors or network failures (3 attempts).

### Edge List Generation (`logic.py`)

Transforms the N×N matrix into a flat list of directional edges covering all required pairs: employee↔employee (permutations), vehicle→employee, employee→office, office→employee, vehicle→office, and office→vehicle. Each edge carries distance (meters) and duration (seconds). The edge IDs use the `"{from}_{to}"` format that all solvers rely on for distance lookups.

### Geometry Enrichment (`geometry_processor.py`)

After the solver produces route assignments, this module:
1. Parses unique route segment tags from the `routes` field of each vehicle.
2. Fetches road geometry for each segment from OSRM `/route/v1/driving/` with throttled concurrency.
3. Injects exact source/destination coordinates at the start and end of each polyline to eliminate visual snapping gaps between route lines and map markers.
4. Encodes paths as polylines (via the `polyline` library) and replaces the `routes` field with `route_geometry` containing `{segment_id, geometry}` pairs.

### Optimization Logger (`optimization_logger.py`)

Logs each successful run to PostgreSQL with full traceability: algorithm winner, objective score, costs, timing, served count, violation counts, vehicle count, and Celery task ID. The table is capped at **1,000 rows** — oldest entries are pruned after each insert. All DB operations are wrapped in try/except so a logging failure never crashes the pipeline.

### Celery Worker (`worker.py`)

- Runs the 5-step optimization pipeline as a background task.
- Bridges async OSRM calls into the sync Celery context via `asyncio.new_event_loop()`.
- Includes a **Celery Beat** scheduled task (`cleanup_orphaned_files`) that purges temporary upload files older than 24 hours, running every 12 hours.
- All logs are written to both `worker_debug.log` and stdout.
- Each task is bookended with `=== TASK STARTED ===` and `=== TASK COMPLETED ===` markers including the Celery task ID and total elapsed time.

### Concurrency Control

The API enforces a server-side job limit (`MAX_CONCURRENT_JOBS`) by inspecting Celery's active + reserved task queues via the Celery inspector. Requests exceeding capacity receive HTTP `429 Too Many Requests`.

### Database (`database.py`, `db_models.py`)

SQLAlchemy with PostgreSQL via psycopg2. The `OptimizationRunLog` model stores input metadata (filename, employee/vehicle counts), result metadata (winner algorithm, served count, violations, objective score, costs), timing (algo duration, total pipeline duration), and traceability (Celery task ID, vehicle count). Tables are auto-created on startup.

### Authentication (`auth.py`)

JWT-based auth with bcrypt password hashing via passlib. Tokens expire after 30 minutes (configurable). The `User` model stores username and hashed password. Currently disabled on optimization endpoints during testing — the `Depends(get_current_user)` guards are commented out.

---

## VROOM Setup

The VROOM solver requires an **isolated virtual environment** with `numpy < 2` due to pyvroom's ABI incompatibility with numpy 2.x. See [`algo/README.md`](algo/README.md) for the full technical explanation.

**Windows:**

```bash
.\setup_vroom_env.bat
```

**Manual / Linux:**

```bash
python3.10 -m venv vroom_env
vroom_env/bin/pip install "numpy<2" pyvroom pandas openpyxl
```

The solver bridge auto-detects `vroom_env/` relative to the project root. Override with the `VROOM_PYTHON_EXE` environment variable if the venv is elsewhere.

---

## Standalone Solver CLI

Run the solver outside of the web stack for testing and debugging:

```bash
python run_solver.py --excel path/to/input.xlsx \
                     --matrix algo/templts/matrix_edge_list.json \
                     --payload algo/templts/payload_dict.json \
                     --output solver_output.json
```

---

## Debugging & Testing

- **`debug_alns.py`** — Step-by-step diagnostic for the ALNS solver. Traces a single insertion (V01 picks up E01), validates routes, and runs a short 5-second ALNS pass to check served vs. unassigned counts.
- **`vroom_test.py`** — Integration test for the VROOM solver path. Prints full environment diagnostics (Python version, numpy version in both main and isolated venvs, interpreter resolution), then runs `solve_vroom()` on a sample spreadsheet.
- **`test_vroom_matrix.py`** — Low-level probe that tests every `array` format code to determine which ones `_vroom.Matrix` accepts on the current platform.
- **`worker_debug.log`** — Shared log file written by both FastAPI and the Celery worker with timestamped step-by-step progress.

---

## Configuration Reference

| Setting                       | Location                  | Default | Description                                     |
|-------------------------------|---------------------------|---------|-------------------------------------------------|
| `MAX_CONCURRENT_JOBS`         | `main.py`                 | 4       | Max parallel Celery tasks before 429 rejection  |
| `MAX_LOG_ROWS`                | `optimization_logger.py`  | 1000    | Row cap for the optimization logs table         |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `auth.py`                 | 30      | JWT token lifetime                              |
| OSRM geometry concurrency     | `router.py`               | 50      | Max parallel geometry fetch requests            |
| Celery Beat interval          | `worker.py`               | 12h     | Orphaned file cleanup frequency                 |

For solver-specific configuration (LNS iterations, ALNS time limits, VROOM parameters), see [`algo/README.md`](algo/README.md).
