import json, os, sys, tempfile, types
from datetime import datetime
from pathlib import Path
sys.path.insert(0, os.getcwd())
tmp = Path(tempfile.mkdtemp())
import cli
from dtrader.markets import INDIA
import dtrader.data.fyers_provider as fp
fp.TOKEN_PATH = tmp / "tok.json"; fp.CALLS_PATH = tmp / "calls.json"
import dtrader.broker_sync as bs
bs.sync = lambda cfg: []
log = []
fp.fyers_login = lambda *a, **k: (log.append("LOGIN"), fp.TOKEN_PATH.write_text(json.dumps({"access_token": "T", "created": INDIA.now().isoformat()})))
cli._kickstart = lambda label: log.append(f"KICK {label}")
cli.time_sleep = None
import time; time.sleep = lambda s: log.append(f"SLEEP {s/60:.0f}m")
clock = {}
INDIA.__class__.now = lambda self: clock["t"]
def run(t, token_created=None, mon="not running", force=False):
    clock["t"] = datetime.fromisoformat(t).replace(tzinfo=INDIA.tz); log.clear()
    if token_created: fp.TOKEN_PATH.write_text(json.dumps({"access_token": "T", "created": token_created + "+05:30"}))
    else: fp.TOKEN_PATH.unlink(missing_ok=True)
    cli._job_state = lambda label: "running" if "dashboard" in label else mon
    print(f"\n===== {t} token={token_created} monitor={mon}")
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.cmd_today({}, types.SimpleNamespace(force=force))
    out = buf.getvalue()
    print("\n".join(l for l in out.splitlines() if "Fyers" in l or "monitor" in l or "logged" in l or "login" in l or "closed" in l))
    print("actions:", log)
run("2026-11-02T06:00:00")                                    # 30 min before reset -> waits then logs in
run("2026-11-02T07:10:00", token_created="2026-11-02T00:07:00")  # old token -> logs in
run("2026-11-02T07:20:00", token_created="2026-11-02T07:10:00")  # valid -> no login
run("2026-11-02T10:30:00", token_created="2026-11-02T07:10:00")  # mid-session, monitor stopped -> kickstart
run("2026-11-02T10:30:00", token_created="2026-11-02T07:10:00", mon="running")
run("2026-11-07T08:00:00")                                    # Saturday -> no login
