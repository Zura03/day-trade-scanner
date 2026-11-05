"""Run every test, each in its own process since they patch modules:
    .venv/bin/python tests/run_all.py
They use made-up data, no network and don't touch your real data folder."""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(HERE.parent), str(HERE / "guard")])}
failed = []
for t in sorted(HERE.glob("test_*.py")):
    r = subprocess.run([sys.executable, str(t)], cwd=HERE.parent, env=env, capture_output=True, text=True)
    print(("ok  " if r.returncode == 0 else "FAIL"), t.name, flush=True)
    if r.returncode:
        failed.append(t.name)
        print(r.stdout[-800:], r.stderr[-1500:], sep="\n")
print(f"{len(failed)} failed" if failed else "all passed")
sys.exit(1 if failed else 0)
