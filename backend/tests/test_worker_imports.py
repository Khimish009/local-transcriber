"""T12.3 — worker entrypoints must not import torch or numpy at module level.

TORCH_NUM_THREADS only reaches the convolution thread pool if OMP_NUM_THREADS is set before
torch starts, so `worker.main` applies it before warming up the models. A module-level
`import torch` anywhere on the import path would silently undo that.
"""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

PROBE = """
import sys
import worker.main, benchmark.run, benchmark.equivalence
heavy = sorted({"torch", "numpy", "pyannote", "gigaam"} & set(sys.modules))
assert not heavy, heavy
"""


def test_entrypoints_import_without_ml_libraries() -> None:
    result = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", PROBE],
        cwd=BACKEND,
        env={"PYTHONPATH": str(BACKEND)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
