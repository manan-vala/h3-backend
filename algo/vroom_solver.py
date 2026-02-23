"""
vroom_solver.py  –  Calls vroom_bridge.py via subprocess in a Python 3.9/3.10 venv
===================================================================================
Because pyvroom's C extension is incompatible with Python 3.11/3.12 (numpy 2.x ABI
break), VROOM runs in a separate lightweight venv:

  ONE-TIME SETUP
  --------------
  # Install Python 3.10 from https://www.python.org/downloads/
  # (tick "Add to PATH" or use the py launcher)

  py -3.10 -m venv vroom_env
  vroom_env\\Scripts\\pip install pyvroom pandas openpyxl

  Then update VROOM_PYTHON below to point to that venv's python.exe

  ALTERNATIVE (if python 3.10 not installed):
    conda create -n vroom310 python=3.10
    conda activate vroom310
    pip install pyvroom pandas openpyxl
    # set VROOM_PYTHON to the conda env's python.exe

  ENVIRONMENT VARIABLE
  --------------------
  You can also set the env var VROOM_PYTHON_EXE instead of editing this file.
"""

import base64
import json
import os
import subprocess
import sys

# ── Path to the Python 3.9/3.10 executable that has pyvroom installed ────────
# Priority: env var  >  this file  >  skip VROOM
_VROOM_PYTHON = (
    os.environ.get("VROOM_PYTHON_EXE")          # 1. env var override
    or r"vroom_env\Scripts\python.exe"           # 2. local venv (relative to h3-backend/)
    # or r"C:\Python310\python.exe"              # 3. uncomment if using system Python 3.10
    # or r"C:\ProgramData\Miniconda3\envs\vroom310\python.exe"  # conda
)

_BRIDGE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vroom_bridge.py")


def solve_vroom(input_data, matrix_edge_list, file_bytes):
    """
    Call vroom_bridge.py in the Python 3.9/3.10 subprocess.
    Returns a dict in the route_sequence format expected by solver.py.
    Raises RuntimeError if VROOM is unavailable or fails.
    """
    # Resolve python exe path relative to h3-backend/
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # h3-backend/
    python_exe = os.path.join(here, _VROOM_PYTHON) if not os.path.isabs(_VROOM_PYTHON) else _VROOM_PYTHON

    if not os.path.isfile(python_exe):
        raise RuntimeError(
            f"VROOM Python not found at '{python_exe}'. "
            "Run setup: py -3.10 -m venv vroom_env && vroom_env\\Scripts\\pip install pyvroom pandas openpyxl"
        )

    # Send input as JSON via stdin (base64-encode the xlsx bytes)
    payload = json.dumps({
        "file_b64": base64.b64encode(file_bytes).decode(),
        "W1_COST":  0.7,
        "W2_TIME":  0.3,
    })

    try:
        proc = subprocess.run(
            [python_exe, _BRIDGE_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,           # give VROOM up to 60 s
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("VROOM bridge timed out after 60s")
    except FileNotFoundError:
        raise RuntimeError(f"Could not launch '{python_exe}'")

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"VROOM bridge exited {proc.returncode}: {stderr}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"VROOM bridge returned invalid JSON: {e}\nOutput: {proc.stdout[:500]}")

    if "error" in result:
        raise RuntimeError(f"VROOM bridge error: {result['error']}")

    return result
