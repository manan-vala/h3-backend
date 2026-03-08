"""
vroom_test.py  –  Integration test for algo.vroom_solver
=========================================================
Exercises vroom_solver.solve_vroom() using the sample spreadsheet at
algo/templts/test_input.xlsx.

IMPORTANT: this script does NOT set VROOM_PYTHON_EXE to sys.executable.
Setting it to sys.executable was the bug in the original version — it forced
the bridge to use the main venv (Python 3.12 + numpy 2.x) which is exactly
the environment that triggers the "Incompatible buffer format" ABI crash.

Usage
-----
    python vroom_test.py [--sample path/to/file.xlsx]

Prerequisites
-------------
1. Create the isolated vroom_env (one-time):

       python setup_vroom_env.py

   — or manually:

       py -3.12 -m venv vroom_env
       vroom_env\\Scripts\\pip install "numpy<2" pyvroom pandas openpyxl

2. Optionally override the venv location:

       set VROOM_PYTHON_EXE=C:\\path\\to\\vroom_env\\Scripts\\python.exe
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# Make `algo` importable when running from the project root.
_ROOT = os.path.abspath(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── do NOT set VROOM_PYTHON_EXE here ──────────────────────────────────────
# The original script did:
#     os.environ["VROOM_PYTHON_EXE"] = sys.executable   ← BUG
# That forced the bridge to use the main venv which has numpy 2.x and triggers
# the ABI crash.  The correct behaviour is to let vroom_solver._resolve_python_exe()
# find the isolated vroom_env automatically.
# ──────────────────────────────────────────────────────────────────────────

from algo import vroom_solver  # noqa: E402  (import after path setup)


def _print_env_info() -> None:
    """Print diagnostic information useful for bug reports."""
    print("=" * 60)
    print("Environment diagnostics")
    print("=" * 60)
    print(f"  Python       : {sys.executable}")
    print(f"  Version      : {sys.version.split()[0]}")

    try:
        import numpy as np
        print(f"  numpy        : {np.__version__}  ← main venv (NOT used for pyvroom)")
    except ImportError:
        print("  numpy        : not installed in main venv")

    vroom_exe_override = os.environ.get("VROOM_PYTHON_EXE", "")
    if vroom_exe_override:
        print(f"  VROOM_PYTHON_EXE (env): {vroom_exe_override}")
    else:
        print("  VROOM_PYTHON_EXE (env): (not set — auto-detection will run)")

    # Show which vroom python vroom_solver would pick
    try:
        resolved = vroom_solver._resolve_python_exe()
        print(f"  vroom Python : {resolved}  ✓")
        # show numpy version inside the isolated venv
        probe = subprocess.run(
            [resolved, "-c", "import numpy; print(numpy.__version__)"],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            print(f"  vroom numpy  : {probe.stdout.strip()}  (must be < 2.0)")
    except RuntimeError as exc:
        print(f"  vroom Python : RESOLUTION FAILED\n    {exc}")
    print("=" * 60)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test vroom_solver.solve_vroom()")
    parser.add_argument(
        "--sample",
        default=os.path.join("algo", "templts", "TestCase_TC03.xlsx"),
        help="Path to the input .xlsx file (default: algo/templts/TestCase_TC03.xlsx)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print full JSON output from the solver",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    _print_env_info()
    print()

    # Validate sample file
    if not os.path.isfile(args.sample):
        print(f"ERROR: sample file not found: {args.sample}", file=sys.stderr)
        print(
            "Tip: pass --sample <path> to specify a different file.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Running solver on: {args.sample}")
    with open(args.sample, "rb") as fh:
        file_bytes = fh.read()

    try:
        result = vroom_solver.solve_vroom({}, [], file_bytes)
    except RuntimeError as exc:
        msg = str(exc)
        print("\n[FAIL] RuntimeError from vroom_solver:", file=sys.stderr)
        print(f"  {msg}", file=sys.stderr)

        # Targeted hints for known failure modes
        if "No compatible VROOM Python found" in msg:
            print(
                "\nFix: run  python setup_vroom_env.py  to create the isolated venv.",
                file=sys.stderr,
            )
        elif "Incompatible buffer format" in msg:
            print(
                "\nThe isolated vroom_env has numpy >= 2.  Fix:\n"
                '  vroom_env/Scripts/pip install "numpy<2"\n'
                "or re-run:  python setup_vroom_env.py --recreate",
                file=sys.stderr,
            )
        elif "pyvroom not installed" in msg or "ModuleNotFoundError" in msg:
            print(
                "\npyvroom is not installed in the isolated venv.  Fix:\n"
                "  vroom_env/Scripts/pip install pyvroom pandas openpyxl",
                file=sys.stderr,
            )
        sys.exit(1)

    print("\n[PASS] Solver completed successfully.")
    if args.verbose:
        print("\n=== VROOM solver output ===")
        print(json.dumps(result, indent=2))
    else:
        # Just print a summary so the test is readable at a glance
        routes = result.get("routes", [])
        print(f"  Routes returned : {len(routes)}")
        total_cost = result.get("summary", {}).get("cost", "n/a")
        print(f"  Summary cost    : {total_cost}")
        print("\n(pass --verbose to see full JSON output)")


if __name__ == "__main__":
    main()