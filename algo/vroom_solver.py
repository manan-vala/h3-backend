"""
vroom_solver.py  –  Calls vroom_bridge.py via an isolated subprocess venv
==========================================================================
ROOT CAUSE OF THE ABI CRASH
----------------------------
pyvroom's PyPI wheels (including the cp312 builds) were compiled with pybind11
< 2.12 and numpy 1.x headers.  pybind11 bakes numpy dtype format strings into
the .so at compile-time.  When numpy 2.x is installed at *runtime* those
strings no longer match, so even passing a plain Python list to
set_durations_matrix() triggers "Incompatible buffer format!" because pybind11's
type-caster probes numpy's buffer protocol as a fallback path.

CHOSEN FIX: isolated venv (Python 3.12 + numpy<2)
---------------------------------------------------
The main venv keeps numpy 2.x untouched.  A small sidecar venv named
`vroom_env` carries pyvroom + numpy<2.  vroom_solver.py talks to it via
subprocess / JSON / stdin, which is the architecture already in place.

ONE-TIME SETUP (Windows – py launcher)
---------------------------------------
  py -3.12 -m venv vroom_env
  vroom_env\\Scripts\\pip install "numpy<2" pyvroom pandas openpyxl

ONE-TIME SETUP (Mac / Linux)
-----------------------------
  python3.12 -m venv vroom_env
  vroom_env/bin/pip install "numpy<2" pyvroom pandas openpyxl

ALTERNATIVE – auto-setup
-------------------------
  python setup_vroom_env.py          # ships alongside this file

ENVIRONMENT VARIABLE OVERRIDE
------------------------------
  set VROOM_PYTHON_EXE=C:\\path\\to\\vroom_env\\Scripts\\python.exe
  # export VROOM_PYTHON_EXE=/path/to/vroom_env/bin/python   (Mac/Linux)
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dotenv import load_dotenv
load_dotenv()  # load .env into os.environ before _resolve_python_exe runs

# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------

# _HERE is the directory that contains this very file (i.e. algo/)
_HERE = os.path.dirname(os.path.abspath(__file__))

# Root of the back-end project (parent of algo/)
_BACKEND_ROOT = os.path.dirname(_HERE)

# The bridge script lives next to this file
_BRIDGE_SCRIPT = os.path.join(_HERE, "vroom_bridge.py")

# Platform-aware venv python path
_VENV_PYTHON_REL = (
    os.path.join("vroom_env", "Scripts", "python.exe")   # Windows
    if sys.platform == "win32"
    else os.path.join("vroom_env", "bin", "python")       # Mac / Linux
)

_CACHED_PYTHON_EXE = None

def _resolve_python_exe() -> str:
    """
    Return the path of a Python interpreter that has a working pyvroom
    (i.e. pyvroom compiled against numpy<2).

    Resolution order
    ----------------
    1. VROOM_PYTHON_EXE environment variable  (absolute or relative to cwd)
    2. vroom_env inside the back-end root     (the recommended isolated venv)
    3. vroom_env relative to cwd              (fallback for unusual layouts)

    Raises RuntimeError with actionable instructions if nothing is found.

    NOTE: we deliberately do NOT fall back to sys.executable (the main venv).
    The main venv carries numpy 2.x which is ABI-incompatible with the
    pyvroom wheel.  Falling back to sys.executable would silently reproduce
    the "Incompatible buffer format" crash every time.
    """
    global _CACHED_PYTHON_EXE
    if _CACHED_PYTHON_EXE is not None:
        return _CACHED_PYTHON_EXE
    candidates: list[tuple[str, str]] = []

    env_override = os.environ.get("VROOM_PYTHON_EXE", "").strip()
    if env_override:
        path = env_override if os.path.isabs(env_override) else os.path.join(os.getcwd(), env_override)
        candidates.append(("VROOM_PYTHON_EXE env var", path))

    candidates.append((
        "vroom_env in back-end root",
        os.path.join(_BACKEND_ROOT, _VENV_PYTHON_REL),
    ))
    candidates.append((
        "vroom_env relative to cwd",
        os.path.join(os.getcwd(), _VENV_PYTHON_REL),
    ))

    for label, path in candidates:
        if os.path.isfile(path):
            # Quick sanity-check: make sure pyvroom is importable and that the
            # numpy version inside the venv is < 2 (the whole point of isolation).
            ok, reason = _probe_venv(path)
            if ok:
                _CACHED_PYTHON_EXE = path
                return path
            # Found the exe but it fails the probe — warn and keep looking.
            print(
                f"[vroom_solver] WARNING: found python at '{label}' ({path}) "
                f"but it failed the compatibility probe: {reason}",
                file=sys.stderr,
            )

    # Nothing usable found → give the developer a concrete fix.
    raise RuntimeError(
        "No compatible VROOM Python found.\n\n"
        "Quick fix — create an isolated venv with numpy<2:\n\n"
        "  Windows:\n"
        "    py -3.12 -m venv vroom_env\n"
        '    vroom_env\\Scripts\\pip install "numpy<2" pyvroom pandas openpyxl\n\n'
        "  Mac / Linux:\n"
        "    python3.12 -m venv vroom_env\n"
        '    vroom_env/bin/pip install "numpy<2" pyvroom pandas openpyxl\n\n'
        "Or run:  python setup_vroom_env.py\n\n"
        "Then re-run your command."
    )


def _probe_venv(python_exe: str) -> tuple[bool, str]:
    """
    Launch *python_exe* and verify that:
      (a) pyvroom is importable
      (b) numpy version in that venv is < 2.0 (ABI-safe for installed pyvroom wheels)

    Returns (True, "") on success or (False, reason_string) on failure.
    Deliberately avoids importing pyvroom in *this* process to keep the main
    venv's numpy 2.x from tainting anything.
    """
    probe = (
        "import sys, os\n"
        "# ensure our workspace's algo directory (where vroom_matrix_patch lives)\n"
        "# is on the child interpreter's path so that the patch can be imported\n"
        "sys.path.insert(0, os.path.join(os.getcwd(), 'algo'))\n"
        "try:\n"
        "    import vroom_matrix_patch\n"
        "except ImportError:\n"
        "    pass\n"
        "try:\n"
        "    import numpy as np\n"
        "    major = int(np.__version__.split('.')[0])\n"
        "    if major >= 2:\n"
        "        print(f'numpy {np.__version__} >= 2 — ABI incompatible with pyvroom wheels', file=sys.stderr)\n"
        "        sys.exit(2)\n"
        "except ImportError:\n"
        "    print('numpy not installed', file=sys.stderr); sys.exit(3)\n"
        "try:\n"
        "    import vroom\n"
        "except ImportError as e:\n"
        "    print(f'pyvroom not installed: {e}', file=sys.stderr); sys.exit(4)\n"
        "# Quick functional test — the exact call that triggers the ABI crash\n"
        "p = vroom.Input()\n"
        "p.set_durations_matrix(profile='car', matrix_input=[[0,1],[1,0]])\n"
        "print('ok')\n"
    )
    try:
        result = subprocess.run(
            [python_exe, "-c", probe],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    if result.returncode == 0 and result.stdout.strip() == "ok":
        return True, ""
    return False, (result.stderr.strip() or f"exit code {result.returncode}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def solve_vroom(
    input_data: dict,
    matrix_edge_list: list,
    file_bytes: bytes,
) -> dict:
    """
    Dispatch the VRP problem to vroom_bridge.py running inside the isolated
    venv and return the route-sequence dict expected by solver.py.

    Parameters
    ----------
    input_data      : reserved for future use (currently unused by bridge)
    matrix_edge_list: OSRM distance/duration pairs from the Table API.
                      Each entry: {id, type, distance_meters, duration_seconds, geometry}
    file_bytes      : raw bytes of the .xlsx input workbook

    Raises
    ------
    RuntimeError    : VROOM unavailable, bridge crashed, or returned an error
    """
    python_exe = _resolve_python_exe()

    # Strip the unused 'geometry' field to reduce payload size over stdin.
    # Each entry can carry a large polyline string; we only need distance/duration.
    compact_matrix = [
        {
            "id": entry["id"],
            "distance_meters": entry["distance_meters"],
            "duration_seconds": entry["duration_seconds"],
        }
        for entry in (matrix_edge_list or [])
        if entry.get("distance_meters") is not None
    ] if matrix_edge_list else []

    payload = json.dumps({
        "file_b64": base64.b64encode(file_bytes).decode(),
        "matrix_edge_list": compact_matrix,
        "W1_COST": 0.7,
        "W2_TIME": 0.3,
    })

    try:
        proc = subprocess.run(
            [python_exe, _BRIDGE_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("VROOM bridge timed out after 120 s")
    except FileNotFoundError:
        raise RuntimeError(f"Could not launch VROOM Python at '{python_exe}'")

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        # Provide an actionable hint for the one error that led to this redesign.
        if "Incompatible buffer format" in stderr:
            stderr += (
                "\n\n*** ABI mismatch detected ***\n"
                "The pyvroom wheel in the vroom_env was compiled against numpy 1.x\n"
                "but a numpy 2.x runtime is present.  Fix:\n"
                "  pip install --upgrade-strategy eager \"numpy<2\" (inside vroom_env)\n"
                "or re-run:  python setup_vroom_env.py --recreate"
            )
        raise RuntimeError(f"VROOM bridge exited {proc.returncode}:\n{stderr}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"VROOM bridge returned invalid JSON: {exc}\n"
            f"First 500 chars of output:\n{proc.stdout[:500]}"
        )

    if "error" in result:
        raise RuntimeError(f"VROOM bridge returned an error: {result['error']}")

    return result