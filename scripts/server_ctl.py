"""Start / stop the town server as an independent process (used by start.bat / stop.bat).

    python scripts/server_ctl.py start
    python scripts/server_ctl.py stop

start: make sure the aitown-pg container is up and accepting connections, refuse to
       open a second server if one already answers on the port, launch uvicorn
       detached (closing the console does not kill it), log to a fixed file outside
       the repo, and wait for /healthz before printing where the world resumed.
stop:  the standard shutdown -- pause (freeze the clock), wait until no background
       work is in flight and the snapshot is drained, confirm the snapshot on disk
       matches the frozen clock, then stop the process that owns the port.

Standard library only. Prints no secrets: the server reads its own .env.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("AI_TOWN_PORT", "8010"))
BASE = f"http://127.0.0.1:{PORT}"
CONTAINER = "aitown-pg"
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AITown", "logs")
LOG = os.path.join(LOG_DIR, "server.log")
# Defaults the operator has run the era with; any value already set in the
# environment wins.
SERVER_ENV = {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
              "AI_TOWN_GATE_DIAG": "1", "AI_TOWN_BUDGET_USD": "30"}


def _get(path: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _post(path: str, body: dict) -> dict | None:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def _psql(sql: str) -> str:
    r = _run(["docker", "exec", CONTAINER, "psql", "-U", "aitown", "-d", "aitown", "-At", "-c", sql])
    return r.stdout.strip()


def ensure_db() -> bool:
    if _run(["docker", "version", "--format", "{{.Server.Version}}"]).returncode != 0:
        print("Docker is not running -- start Docker Desktop first.")
        return False
    running = _run(["docker", "ps", "-q", "--filter", f"name=^{CONTAINER}$"]).stdout.strip()
    if not running:
        print(f"starting container {CONTAINER} ...")
        if _run(["docker", "start", CONTAINER]).returncode != 0:
            print(f"could not start {CONTAINER} (does the container exist? see README, local Postgres).")
            return False
    for _ in range(60):
        if _run(["docker", "exec", CONTAINER, "pg_isready", "-U", "aitown"]).returncode == 0:
            return True
        time.sleep(1)
    print(f"{CONTAINER} did not become ready within 60 s.")
    return False


def start() -> int:
    if _get("/healthz") is not None:
        st = _get("/api/state") or {}
        print(f"already running on port {PORT} ({st.get('clock', '?')}) -- not starting a second one.")
        return 0
    if not ensure_db():
        return 1
    os.makedirs(LOG_DIR, exist_ok=True)
    env = {**SERVER_ENV, **os.environ}
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    log = open(LOG, "ab")
    log.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
    log.flush()
    subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.server:app",
                      "--host", "127.0.0.1", "--port", str(PORT)],
                     cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    print(f"log: {LOG}")
    for _ in range(120):
        if _get("/healthz", timeout=2) is not None:
            st = _get("/api/state") or {}
            print(f"resumed at {st.get('clock', '?')}  ({'paused' if st.get('paused') else 'running'}, "
                  f"speed {st.get('speed', '?')}x)  ->  {BASE}")
            return 0
        time.sleep(1)
    print(f"server did not answer /healthz within 120 s -- see the log: {LOG}")
    return 1


def _port_pids() -> set[int]:
    if os.name != "nt":
        r = _run(["lsof", "-ti", f"tcp:{PORT}", "-sTCP:LISTEN"])
        return {int(x) for x in r.stdout.split() if x.isdigit()}
    pids = set()
    for line in _run(["netstat", "-ano", "-p", "TCP"]).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{PORT}") and parts[3] == "LISTENING":
            pids.add(int(parts[4]))
    return pids


def stop() -> int:
    st = _get("/api/state")
    if st is None:
        print(f"no server answering on port {PORT}.")
        return 0
    if not st.get("paused"):
        if _post("/api/control", {"cmd": "pause"}) is None:
            print("pause request failed -- server left running.")
            return 1
    print("paused; waiting for in-flight work to land ...")
    for _ in range(300):
        st = _get("/api/state") or {}
        if st.get("paused") and st.get("inflight") == 0 and st.get("drained"):
            break
        time.sleep(1)
    else:
        print(f"still busy after 300 s (inflight={st.get('inflight')}, drained={st.get('drained')}) "
              "-- server left running, paused. Run stop again.")
        return 1
    minute = st.get("minute")
    if minute is not None:
        # the running world's snapshot is the one written last
        on_disk = _psql("select minute from world_snapshots order by created_at desc limit 1")
        if on_disk != str(minute):
            print(f"snapshot on disk is minute {on_disk or 'none'}, clock is {minute} "
                  "-- server left running, paused. Run stop again.")
            return 1
    print(f"snapshot confirmed at {st.get('clock')}")
    pids = _port_pids()
    if not pids:
        print("could not find the server process.")
        return 1
    for pid in pids:
        if os.name == "nt":
            _run(["taskkill", "/PID", str(pid), "/T", "/F"])
        else:
            os.kill(pid, 15)
    for _ in range(20):
        if _get("/healthz", timeout=1) is None:
            print("server stopped.")
            return 0
        time.sleep(0.5)
    print("server still answering -- check the process.")
    return 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("start", "stop"):
        print(__doc__)
        sys.exit(2)
    sys.exit(start() if cmd == "start" else stop())
