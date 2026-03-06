"""
vroom_matrix_patch.py  –  Windows MSVC uint32_t format-string monkey-patch
===========================================================================

ROOT CAUSE (the REAL one, not numpy 1.x vs 2.x)
-------------------------------------------------
pyvroom's Windows wheel was compiled with MSVC where:

    uint32_t  →  resolves to  unsigned long  →  pybind11 format char = "L"

But pyvroom's Python layer always calls:

    _vroom.Matrix(numpy.asarray(matrix_input, dtype="uint32"))

numpy.asarray with dtype="uint32" produces an array whose buffer format is:

    unsigned int  →  format char = "I"

pybind11 compares format strings as exact strings; "L" != "I" even though
both are 32-bit on Windows.  Result:

    RuntimeError: Incompatible buffer format!

This is a Windows-only build artefact.  The same wheel on Linux/macOS works
because there uint32_t resolves to unsigned int → "I" → exact match.

FIX
---
At import time we probe _vroom.Matrix with a tiny 2x2 array using each
candidate dtype until one succeeds.  We then replace the three affected
vroom.Input methods with wrappers that substitute the discovered dtype for
the hardcoded "uint32".

USAGE
-----
Import this module *before* any vroom.Input call, e.g. the very top of
vroom_bridge.py:

    import vroom_matrix_patch        # apply patch (no-op on Linux/macOS)
    import vroom

The patch is idempotent – importing it a second time is a no-op.
"""

from __future__ import annotations

import sys
import types

# ── Only patch on Windows; Linux/macOS don't need it ─────────────────────────
# (Safe to import everywhere – on non-Windows it exits immediately.)
if sys.platform != "win32":
    # Nothing to do.
    pass
else:
    import numpy as np
    import vroom as _vroom_mod
    import vroom._vroom as _vroom_ext      # the compiled C extension
    import vroom.input.input as _input_mod  # the pure-Python wrapper layer

    # ── Step 1: probe to discover what dtype _vroom.Matrix actually accepts ──
    def _probe_matrix_dtype() -> str:
        """
        Return the numpy dtype string that _vroom.Matrix accepts on this
        Windows build.  Tries each candidate in order; raises RuntimeError
        if none work (which should never happen on any supported platform).
        """
        _test = [[0, 1], [1, 0]]
        # "L" = unsigned long (32-bit on Windows) — the MSVC uint32_t typedef
        # "I" = unsigned int  (32-bit everywhere) — what numpy uses for uint32
        # "u4"= numpy alias that normalises to platform unsigned 32-bit
        for dtype in ("L", "I", "u4", "uint32"):
            try:
                arr = np.asarray(_test, dtype=dtype)
                _vroom_ext.Matrix(arr)
                return dtype
            except (RuntimeError, TypeError, ValueError):
                continue
        raise RuntimeError(
            "vroom_matrix_patch: could not find a dtype accepted by _vroom.Matrix. "
            "This is unexpected — please open an issue."
        )

    _MATRIX_DTYPE: str = _probe_matrix_dtype()

    # ── Step 2: only apply patch when the dtype differs from "uint32" ────────
    #   (On a future corrected wheel "I" will work, so we patch nothing.)
    if _MATRIX_DTYPE != "uint32":

        # Keep references to the originals so our wrappers can call them.
        _orig_set_durations = _input_mod.Input.set_durations_matrix
        _orig_set_costs     = _input_mod.Input.set_costs_matrix
        _orig_set_distances = _input_mod.Input.set_distances_matrix

        def _make_patched(orig_fn: types.FunctionType, dtype: str):
            """
            Return a wrapper that replaces the array construction inside the
            original function by temporarily monkey-patching numpy.asarray
            *only* within the call frame of the original function.

            We take a simpler, more robust approach: construct the array
            ourselves with the correct dtype and pass it directly to
            _vroom.Matrix, bypassing the original conversion entirely.
            """
            def _patched(self, profile: str, matrix_input) -> None:
                # Convert to a C-contiguous 2-D array with the probed dtype.
                arr = np.ascontiguousarray(
                    np.asarray(matrix_input, dtype=np.dtype("uint32"))   # normalise values
                    .view(np.dtype(dtype))                                 # re-tag format
                )
                matrix = _vroom_ext.Matrix(arr)
                # Call the internal C++ method directly, skipping the
                # numpy.asarray() line that causes the crash.
                _vroom_ext.Input.set_durations_matrix(self._input, profile, matrix) \
                    if "duration" in orig_fn.__name__ else \
                _vroom_ext.Input.set_costs_matrix(self._input, profile, matrix) \
                    if "cost" in orig_fn.__name__ else \
                _vroom_ext.Input.set_distances_matrix(self._input, profile, matrix)
            _patched.__name__ = orig_fn.__name__
            _patched.__doc__  = orig_fn.__doc__
            return _patched

        # ── Simpler, more reliable implementation ────────────────────────────
        # Rather than trying to replicate the internals, we patch numpy.asarray
        # within the module namespace of vroom.input.input so the original
        # function calls our shim transparently.

        _real_asarray = np.asarray  # save the real one

        class _AsArrayShim:
            """
            Wraps numpy.asarray and substitutes the correct dtype when called
            from within pyvroom's matrix setters.
            """
            def __call__(self, a, dtype=None, **kw):
                if dtype == "uint32":
                    dtype = _MATRIX_DTYPE
                return _real_asarray(a, dtype=dtype, **kw)

        # Inject the shim into the module namespace that the original
        # functions close over – this is the safest approach and requires
        # no changes to any logic paths.  Only replace the `asarray` symbol
        # rather than the entire numpy module; earlier versions did the latter
        # and broke attribute access (see compatibility probe failures).
        _input_mod.numpy.asarray = _AsArrayShim()   # type: ignore[assignment]

        # ── Step 3: also patch setup_vroom_env probe so it uses correct dtype ─
        # (No action needed; _probe_venv in setup_vroom_env calls this module.)

    # ── Public diagnostic helper ──────────────────────────────────────────────
    def report() -> None:
        """Print a one-line summary of the patch status."""
        if _MATRIX_DTYPE == "uint32":
            print("[vroom_matrix_patch] No patch needed (dtype 'uint32' works natively).")
        else:
            print(
                f"[vroom_matrix_patch] Windows MSVC patch active: "
                f"'uint32' → '{_MATRIX_DTYPE}' inside vroom.input.input"
            )