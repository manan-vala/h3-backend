# Velora Optimization Engine - LNS & Local Search

## Important Stuff 
- Before running the code please set the proper file locations in lns_algo and lns_check
- The folder structure I am using for the lns_check has a file called ../Utils/solution_check.py
  which is puneets which checks if everything is correct

This directory contains the advanced optimization engine for the Velora Corporate Mobility Challenge. The solution employs a hybrid **Large Neighborhood Search (LNS)** metaheuristic combined with **Local Search** refinement to navigate the complex solution space of the Vehicle Routing Problem (VRP) with heterogeneous constraints.

## 📂 File Structure

- **`lns_algo.py`**: The main entry point and orchestrator. Implements the LNS loop (Ruin & Recreate) and manages the optimization lifecycle.
- **`lns_local_search.py`**: A specialized module for fine-tuning solutions. It runs after the reconstruction phase to strictly improve local route quality using operators like Relocate, Swap, and 2-Opt.
- **`lns_simulator.py`**: The "Physics Engine" of the optimizer. It calculates routes, times, and costs, and evaluates constraint violations (Capacity, Time Windows, Max Delay).
- **`lns_utils.py`**: Data structures (`Employee`, `Vehicle`) and helper functions (Haversine distance, data loading).

## 🧠 Algorithmic Approach

The solution iteratively improves upon an initial schedule (or starts from scratch) using the following process:

### 1. Ruin (Destruction)
We intentionally "break" parts of the current solution to escape local optima.
- **Random Ruin**: Removes random employees to introduce noise.
- **Spatial Ruin**: Removes clusters of employees geographically close to a random seed.
- **Route Ruin (Structure Break)**: Completely empties specific vehicles to force major structural reorganization.

### 2. Recreate (Repair)
We repair the partial solution using a **Greedy Insertion** heuristic.
- Unassigned employees are inserted into the "cheapest" valid position available across all vehicles.
- "Cheapest" is defined by minimal increase in operational cost and violation penalties.
- The algorithm can add new vehicles (from the pool) if necessary.

### 3. Local Search (Improvement)
After repair, we run an extensive local search to polish the routes. In each step, we explore multiple neighborhoods and select the single best move:

1.  **Relocate**: Moves a customer from one route/position to another.
2.  **Swap**: Exchanges two customers between different routes.
3.  **2-Opt**: Reverses a segment of a route to untangle crossing paths (improves geometry).
4.  **Or-Opt**: Moves a contiguous segment of customers (e.g., sequence of 2 or 3) to a different position in the same route.
5.  **Exchange Groups**: Swaps entire groups of passengers between two different vehicles.
6.  **Merge/Split**: Merges two small groups into one, or splits a large group into two, to better fit vehicle capacities or reduce delays.

### 4. Objective Function
The optimizer minimizes a weighted sum of:
1.  **Operational Cost**: Distance × Vehicle Cost/km.
2.  **Travel Time**: Total time employees spend in transit.
3.  **Soft Penalties**: Violations of sharing preferences or vehicle type preferences.
4.  **Hard Penalties**: Heavily weighted costs for violating Capacity, Time Windows, or leaving employees unassigned.

## 🚀 Usage

To run the optimization process:

```bash
# From the project root
cd Prog
python lns_algo.py
```

### Configuration
You can adjust parameters in `Prog/lns_algo.py` (bottom `__main__` block):
- `max_iterations`: Number of LNS loops (default: 50-100).
- `destruction_rate`: Percentage of solution to destroy in each step (default: 0.2 - 0.3).
- `weights`: Loaded from `weights.json` to prioritize Cost vs. Time.

## 🔧 Key Features
- **Soft Constraints**: The simulator allows temporary violations (with high penalties) during the search, enabling the algorithm to traverse "invalid" states to reach better "valid" solutions.
- **Cache System**: `LocalSearch` implements caching for route evaluations to drastically speed up iteration times.
- **Extensible**: New operators can be added to `lns_local_search.py`.
- **Dynamic Repair**: Can dynamically add new vehicles or utilize existing empty ones to satisfy demand.
