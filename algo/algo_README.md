# Optimization Engine — Solver Algorithms

This directory contains the core optimization algorithms for the Vehicle Routing Problem (VRP). Three solvers run concurrently, and a unified feasibility scorer picks the best result.

---

## File Structure

```
algo/
├── __init__.py              # Exports solve_vrp()
├── solver.py                # Orchestrator — runs all 3 solvers in parallel, picks best
│
├── lns_algo.py              # LNS: Large Neighborhood Search optimizer
├── lns_local_search.py      # LNS: Local search operators (Relocate, Swap, 2-Opt, etc.)
├── lns_simulator.py         # LNS: Route simulation engine (cost, time, violations)
├── lns_utils.py             # Shared data structures, distance matrix, helpers
│
├── 16-02.py                 # ALNS: Enhanced Adaptive Large Neighborhood Search
│
├── vroom_solver.py          # VROOM: Subprocess launcher (isolated venv bridge)
├── vroom_bridge.py          # VROOM: Standalone solver (runs inside isolated venv)
├── vroom_matrix_patch.py    # VROOM: Windows MSVC pyvroom ABI fix
│
├── feasibilityfinal.py      # Unified feasibility scorer for solution comparison
├── check_lns.py             # LNS output validation against reference checker
└── templts/                 # Template/test input files (Excel, JSON)
```

---

## Three-Solver Tournament (`solver.py`)

The orchestrator runs **LNS**, **ALNS**, and **VROOM** concurrently using a `ThreadPoolExecutor(max_workers=3)`. Each solver receives the same input — a payload dict, an OSRM-derived edge list, and raw Excel bytes — and produces a solution in a standardized output format containing per-vehicle route sequences with timing, cost, and route link tags.

If any solver fails (exception or timeout), the remaining solutions are still evaluated. The ALNS solver has a **150-second hard timeout** in the thread pool; if it exceeds this but has published a partial result via its `result_ref` mechanism, that partial result is used instead of discarding the run.

All surviving solutions are scored by `feasibilityfinal.py`. The **selection logic** is:

1. **Filter** to solutions with zero hard constraint violations.
2. Among valid solutions, **maximize** employees served (strictly).
3. Among ties, **minimize** effective objective = `objective + (soft_violations × 100)`.
4. If no valid solutions exist, pick the one with the fewest hard violations, then most served, then lowest effective objective.

The `16-02.py` module is imported dynamically (via `importlib`) because its filename is not a valid Python identifier. A thread lock guards the import to prevent races when concurrent Celery tasks call `solve_vrp()` simultaneously.

---

## Solver 1: LNS — Large Neighborhood Search

**Files:** `lns_algo.py`, `lns_local_search.py`, `lns_simulator.py`

An iterative metaheuristic that starts from an empty (or provided) solution, repairs it, then repeatedly destroys and rebuilds parts of the solution to explore the search space. Simulated annealing acceptance allows temporary worsening to escape local optima.

### Initialization

1. Load employee/vehicle data from the Excel file via `lns_utils.load_data_from_bytes()`.
2. Build a `DistanceMatrix` from the OSRM edge list.
3. If starting from scratch, run **Regret-2 insertion** to build an initial feasible solution from all unassigned employees.

### Ruin (Destruction Operators)

Each iteration selects one operator uniformly at random. The destruction rate varies randomly between **10–60%** of employees per iteration.

- **Random Ruin** — removes a uniformly random subset of employees.
- **Spatial Ruin** — picks a random seed employee, computes distances from the seed to all assigned employees using the real distance matrix, and removes the closest `num_remove` employees. This creates a geographic "hole" in the solution.
- **Worst Ruin** — sorts employees by distance-to-office (descending), then removes them with a **cubic-biased random selection** (`index = random()^3 × len`), preferentially targeting high-cost outliers while maintaining some randomness.
- **Route Ruin** — completely empties ~20% of active vehicles, removing all their assigned employees. Forces major structural reorganization.

### Recreate (Repair Operators)

Each iteration selects one operator with 50/50 probability:

- **Greedy Insertion** — shuffles unassigned employees, then for each one evaluates every possible insertion position across all vehicles. Insertion cost is measured as the **marginal delta** (new route cost minus baseline route cost for that vehicle), ensuring fair comparison across vehicles with different existing loads. An employee is only assigned if the marginal cost is less than the unassigned penalty (`W5 = 20,000`); otherwise it stays unassigned. Can also add employees as new solo groups on currently unused vehicles.

- **Regret-k Insertion** (k=2) — for each unassigned employee, computes the best and second-best insertion costs. The employee with the largest regret value (gap between best and second-best) is inserted first. This prevents greedy short-sightedness by prioritizing employees that would become expensive to insert later.

### Local Search (`lns_local_search.py`)

Runs **after every repair**, up to 100 improvement steps. Each step explores all six operator neighborhoods simultaneously and applies the single best-improving move found (steepest descent):

1. **Relocate** — moves an employee from one route/position to another. Samples up to 40 assigned employees and 8 target vehicles per step to keep it tractable.
2. **Swap** — exchanges two employees between different routes. Tests 80 random pairs per step.
3. **2-Opt** — reverses a segment within a group to untangle crossing paths. Tests 5 random `(i, j)` pairs per group (groups with < 3 members are skipped).
4. **Or-Opt** — moves a contiguous segment of 1–3 employees to a different position within the same group. Samples 10 vehicles, 5 segments per group, 3 insertion positions each.
5. **Exchange Groups** — swaps entire passenger groups between two vehicles. Tests 20 random vehicle pairs per step.
6. **Merge/Split** — merges two groups on the same vehicle into one, or splits a large group at a random position into two separate groups. Samples 5 vehicles for each sub-operator.

**Caching:** Route evaluations are cached by a key of `vehicle_id:group_structure_hash`. When a move is accepted, only the caches for the involved vehicles are invalidated — all other vehicles retain their cached scores.

**Move Selection:** Moves that increase served count are always preferred. Among purely score-improving moves, the one with the largest negative delta is selected. The search terminates when no improving move exists.

### Acceptance Criterion

Simulated annealing: `T₀ = 1000`, cooling rate = `0.995` per iteration. A candidate solution with score `Δ` worse than the current best is accepted with probability `exp(-Δ / T)`. The best solution seen across all iterations is always preserved.

### Objective Function

```
score = W1·cost + W2·time + W3·sharing_penalty + W4·vehicle_penalty
        + violation_penalty + unassigned_count × W5 + hard_violations × W6
```

| Weight | Value      | Purpose                          |
|--------|------------|----------------------------------|
| W1     | 1          | Operational cost (distance × ₹/km) |
| W2     | 1          | Travel time (minutes)            |
| W3     | 2,000      | Sharing preference violations    |
| W4     | 2,000      | Vehicle category mismatches      |
| W5     | 20,000     | Per unassigned employee          |
| W6     | 10,000,000 | Per hard constraint violation    |

The penalty hierarchy ensures the solver prefers leaving employees unassigned (W5) over violating hard constraints like capacity or time windows (W6), while strongly discouraging soft violations (W3, W4).

### Route Simulator (`lns_simulator.py`)

The "physics engine" for LNS. Simulates a vehicle's route group-by-group:

1. Start at the vehicle's depot at its `available_time`.
2. For each group: visit pickups in sequence (accumulating distance, travel time, wait time if arriving before `earliest_pickup`), then drive to the office for drop-off.
3. At each step, check capacity, time windows, priority deadline breaches, and sharing/vehicle preferences.

Operates in two modes:
- **Strict mode** (`allow_violations=False`) — returns infeasible immediately on any hard violation.
- **Penalty mode** (`allow_violations=True`) — accumulates heavy penalties instead, letting the search traverse temporarily invalid states to find better solutions on the other side.

Penalty values: capacity violation = 5,000,000 per excess passenger; time window violation = 50,000 per minute of delay; priority breach = 2,000,000 per occurrence.

---

## Solver 2: ALNS — Adaptive Large Neighborhood Search (`16-02.py`)

A more sophisticated variant of LNS with **adaptive operator selection**. Rather than choosing destruction operators uniformly at random, ALNS tracks each operator's historical performance and adjusts selection probabilities accordingly.

### Key Differences from LNS

- **Adaptive weights** — operator selection probabilities are updated every 100 iterations using a decay-weighted scoring system (`decay = 0.8`). Operators that led to global-best improvements score 6.0, local improvements score 3.0, accepted-but-equal score 1.0, rejected score 0.0.
- **7 destruction operators** instead of 4 (see below).
- **Adaptive destruction rates** — rates widen (up to 50%) during stagnation (>500 iterations without improvement) and narrow (down to 5%) when improving frequently.
- **Decaying noise in repair** — noise starts at 10% and linearly decays to 0 over the run, encouraging exploration early and exploitation late.
- **Time representation** — uses fractional day (0.0–1.0 where 0.375 = 09:00) instead of minutes.
- **Data structures** — routes are stored as ordered lists of `RouteLeg` objects (pickup/drop pairs) rather than the nested group lists used by LNS. The `Solution` class tracks routes per vehicle plus a set of unassigned request IDs.

### Destruction Operators (7 total)

1. **Random** — baseline random removal.
2. **Worst Cost** — removes employees with highest historical insertion cost, preferentially targeting the most expensive assignments.
3. **Shaw (Relatedness)** — removes employees that are geographically proximate with similar time windows and priority levels.
4. **Sharing Violation** — specifically targets employees currently violating sharing preferences.
5. **Time Window** — targets employees with tight or nearly-violated time windows.
6. **Route** — removes all employees from 1–2 entire vehicles.
7. **Zone** — geographic zone-based removal using coordinate clustering.

### Repair

Regret-based insertion with configurable noise. The noise level starts at 10% of the insertion cost and decays linearly to 0 over the optimization run. This balances diversification (noisy early insertions explore more of the solution space) with intensification (clean late insertions converge to the best-known region).

### Timeout Recovery

The solver publishes its best-so-far formatted solution to a shared `result_ref[0]` after every improvement. The `_write_result` call is wrapped in try/except so it never crashes the solve loop. If `solver.py`'s 150-second hard timeout fires, the last published result is used rather than discarding the run entirely.

### Early Termination

The solver stops early if any of these conditions are met:
- **300 iterations** without a global-best improvement.
- **Acceptance rate** drops below 2% over 1,000 iterations.
- **Wall-clock time limit** reached (default 38 seconds, configurable via `config.ALNS_TIME_LIMIT`).

### Configuration

| Parameter                 | Default  | Description                                    |
|---------------------------|----------|------------------------------------------------|
| `ALNS_ITERATIONS`         | 10,000   | Max iterations                                 |
| `ALNS_TIME_LIMIT`         | 38s      | Wall-clock time limit                          |
| `DESTROY_RATE_MIN/MAX`    | 0.05/0.40| Base destruction rate range                    |
| `TEMPERATURE_START/END`   | 100/0.01 | Simulated annealing temperature range          |
| `WEIGHT_UPDATE_INTERVAL`  | 100      | Iterations between operator weight updates     |
| `WEIGHT_DECAY`            | 0.8      | Exponential decay for operator weight smoothing|
| `EARLY_TERMINATION_ITERATIONS` | 300 | Stagnation iterations before early stop        |
| `K_NEAREST_VEHICLES`      | 3        | Vehicles considered per request during repair  |
| `UNASSIGNED_PENALTY`      | 1,000,000| Cost for each unassigned employee              |
| `VEHICLE_PREF_PENALTY`    | 500      | Soft penalty for vehicle category mismatch     |
| `SHARING_PREF_PENALTY`    | 200      | Soft penalty for sharing preference violation  |

---

## Solver 3: VROOM

**Files:** `vroom_solver.py`, `vroom_bridge.py`, `vroom_matrix_patch.py`

Uses the [pyvroom](https://github.com/VROOM-Project/pyvroom) library (Python bindings for the VROOM C++ solver) for fast heuristic routing.

### Isolation Architecture

pyvroom's PyPI wheels are compiled with pybind11 < 2.12 and numpy 1.x headers. pybind11 bakes numpy dtype format strings into the `.so` at compile time. When numpy 2.x is present at runtime, those format strings no longer match, and even passing a plain Python list to `set_durations_matrix()` triggers `"Incompatible buffer format!"` because pybind11's type-caster probes numpy's buffer protocol as a fallback.

**Solution:** The main venv keeps numpy 2.x untouched. A sidecar venv named `vroom_env/` carries pyvroom + `numpy < 2`. `vroom_solver.py` communicates with it via subprocess, passing JSON over stdin and reading JSON from stdout.

**Interpreter resolution chain:**
1. `VROOM_PYTHON_EXE` environment variable
2. `vroom_env/` in the backend root directory
3. `vroom_env/` relative to the current working directory

Each candidate is probed with a functional test (imports vroom, calls `set_durations_matrix` with a 2×2 matrix) before use. The system **deliberately never** falls back to `sys.executable` to prevent silent ABI crashes.

### How the Bridge Works (`vroom_bridge.py`)

1. **Parse input** — reads the Excel file and builds a unified location index mapping unique (lat, lng) pairs to integer indices.
2. **Build matrices** — constructs per-vehicle NxN duration and cost matrices. OSRM edge list data is used where available; haversine × 1.3 road factor is the fallback. Duration matrices are **scaled by each vehicle's speed ratio** relative to a 30 km/h OSRM reference speed. Cost matrices encode the weighted objective `int(100 × (W1·cpk·dist_km + W2·dur_min))`.
3. **Model the problem** — each employee becomes a VROOM shipment (pickup → delivery pair) with time windows and capacity constraints. Priority delays from metadata are encoded into delivery time window upper bounds. All vehicles are registered with their capacity, availability window, and per-vehicle cost/duration profiles.
4. **Solve** — `exploration_level=5`, 2 threads, 120-second subprocess timeout.
5. **Format output** — converts VROOM's solution dataframe into the standardized route_sequence format. Consecutive stops at the same physical location are merged. Route links (`"{from}_{to}"`) are generated for geometry enrichment downstream.

### Windows ABI Patch (`vroom_matrix_patch.py`)

On Windows with MSVC, `uint32_t` resolves to `unsigned long` (pybind11 format char `"L"`), but numpy's `uint32` dtype uses `unsigned int` (format char `"I"`). The patch:

1. Probes `_vroom.Matrix` at import time with each candidate dtype.
2. If the native `"uint32"` doesn't work, monkey-patches `numpy.asarray` within pyvroom's `vroom.input.input` module namespace to substitute the discovered working dtype.

The patch is idempotent and a no-op on Linux/macOS.

### VROOM Setup

**Windows:**
```bash
.\setup_vroom_env.bat
```

**Manual / Linux:**
```bash
python3.10 -m venv vroom_env
vroom_env/bin/pip install "numpy<2" pyvroom pandas openpyxl
```

---

## Feasibility Scorer (`feasibilityfinal.py`)

Provides `get_feasibility_score(file_bytes, matrix_edge_list, solution_json)` — a **solver-agnostic** evaluation that replays any solution step-by-step against the original constraints from the Excel file. This is the single source of truth used by `solver.py` to compare solutions across all three algorithms.

**Evaluation process:** For each vehicle in the solution, the scorer walks through the `route_sequence`, accumulating travel time, cost, and checking constraints at each stop. It uses the same `DistanceMatrix` with haversine fallback as the LNS solver.

**Checks performed:**
- Capacity violations (more passengers than vehicle seats)
- Priority deadline breaches (arrival at office later than `latest_drop + max_delay`)
- Duplicate assignments (same employee assigned to multiple vehicles)
- Sharing preference violations (group size exceeds employee's sharing limit)
- Vehicle category mismatches (employee prefers premium but assigned to standard)

**Returns:**

| Field             | Description                                      |
|-------------------|--------------------------------------------------|
| `served_count`    | Number of distinct employees served              |
| `hard_violations` | Capacity + deadline + duplicate violation count  |
| `soft_violations` | Sharing + vehicle category violation count       |
| `objective`       | `Wc × total_cost + Wt × total_time_min`         |
| `total_cost`      | Sum of distance × cost_per_km across all vehicles|
| `total_time_min`  | Total travel + wait time in minutes              |

---

## Shared Utilities (`lns_utils.py`)

Used by LNS, the feasibility scorer, and indirectly by the ALNS solver.

- **`Employee` dataclass** — id, priority, pickup/drop coordinates, earliest_pickup (minutes), latest_drop (minutes), max_delay (minutes from metadata), vehicle_preference, sharing_preference.
- **`Vehicle` dataclass** — id, capacity, speed (km/h), cost_per_km, category, start coordinates, available_time (minutes).
- **`DistanceMatrix`** — wraps the OSRM edge list for O(1) lookups by `"{from}_{to}"` key. Fallback chain: reverse key → haversine × 1.3 road factor (if coordinates registered) → hard default (10 km, 30 min).
- **`load_data_from_bytes()`** — parses the Excel file into Employee/Vehicle dicts.
- **`time_to_minutes()`** — robust time parser handling `datetime.time`, `datetime.datetime`, `"HH:MM"` strings, and Excel time fractions (values < 10.0 are treated as fractions of a day, e.g., 0.375 → 540 minutes).
- **`haversine()`** — great-circle distance in km.

---

## Output Format

All three solvers produce output in the same structure:

```json
{
  "vehicles": [
    {
      "vehicle_id": "V01",
      "vehicle_type": "standard",
      "capacity": 4,
      "avg_speed_kmph": 30.0,
      "total_cost": 125.50,
      "total_time_minutes": 45.3,
      "total_steps": 5,
      "routes": ["V01_E03", "E03_E07", "E07_office", "office_E12", "E12_office"],
      "route_sequence": [
        {"step": 0, "location": "V01", "arrival_time": "08:00", "departure_time": "08:00"},
        {"step": 1, "location": "E03", "arrival_time": "08:12", "departure_time": "08:12"},
        {"step": 2, "location": "E07", "arrival_time": "08:25", "departure_time": "08:25"},
        {"step": 3, "location": "office", "arrival_time": "08:40"},
        {"step": 4, "location": "E12", "arrival_time": "08:55", "departure_time": "08:55"},
        {"step": 5, "location": "office", "arrival_time": "09:10"}
      ]
    }
  ],
  "summary": {
    "total_cost_all_vehicles": 450.75
  }
}
```

The `routes` array contains link tags in `"{from}_{to}"` format, which are consumed downstream by `geometry_processor.py` to fetch and encode road geometries for map rendering. After enrichment, the `routes` field is replaced with `route_geometry` containing polyline-encoded paths.

---

## Debugging & Testing

- **`check_lns.py`** — validates LNS output against an external reference checker (`Utils/solution_check.py`). Expects output at `Output/output_lns.json` and input at `Test_Data/TestCase_TC04.xlsx`.
- **`debug_alns.py`** (in parent directory) — step-by-step ALNS diagnostic. Traces a single insertion (V01 picks up E01, drops at office), validates the route, then runs a short 5-second ALNS pass.
- **`vroom_test.py`** (in parent directory) — integration test for the VROOM path with environment diagnostics.
- **`test_vroom_matrix.py`** (in parent directory) — low-level probe for `_vroom.Matrix` format compatibility.

---

## Configuration Quick Reference

| Parameter                           | File              | Default      |
|-------------------------------------|-------------------|--------------|
| LNS iterations                      | `solver.py` call  | 100          |
| LNS simulated annealing T₀          | `lns_algo.py`     | 1000         |
| LNS cooling rate                    | `lns_algo.py`     | 0.995        |
| LNS destruction rate range          | `lns_algo.py`     | 10–60%       |
| LNS local search max steps          | `lns_algo.py`     | 100          |
| LNS unassigned penalty (W5)         | `lns_algo.py`     | 20,000       |
| LNS hard violation penalty (W6)     | `lns_algo.py`     | 10,000,000   |
| ALNS max iterations                 | `16-02.py`        | 10,000       |
| ALNS time limit                     | `16-02.py`        | 38s          |
| ALNS early termination (stagnation) | `16-02.py`        | 300 iters    |
| ALNS operator weight decay          | `16-02.py`        | 0.8          |
| ALNS unassigned penalty             | `16-02.py`        | 1,000,000    |
| ALNS hard timeout in solver.py      | `solver.py`       | 150s         |
| VROOM exploration level             | `vroom_bridge.py` | 5            |
| VROOM threads                       | `vroom_bridge.py` | 2            |
| VROOM subprocess timeout            | `vroom_solver.py` | 120s         |
| Soft violation penalty (ranking)    | `solver.py`       | 100          |
| Road factor (haversine → road)      | multiple files    | 1.3×         |
