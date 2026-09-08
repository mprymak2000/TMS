#!/usr/bin/env python3
"""
Starts the DB, backend, and frontend for local development.

Usage:
    python start.py             # run database + backend + frontend inline, in this terminal
    python start.py --separate  # open database + backend + frontend in their own terminal windows

Works regardless of which Docker runtime you're using — Colima, Docker Desktop, or
Docker Desktop's WSL2 backend on Windows. The `docker`/`docker compose` CLI behaves
identically no matter which of these is actually running underneath; this script only
ever actively launches Colima itself (macOS, if installed and not already running) —
for Docker Desktop or WSL2, start that yourself first if `docker info` isn't responding.
"""

import argparse
import contextlib
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
BACKEND_CMD = "poetry run uvicorn main:app --reload --host 0.0.0.0"
FRONTEND_CMD = "npm run dev"
DATABASE_CMD = "docker compose up"
PORTS = {"backend": 8000, "frontend": 5173}


def port_in_use(port: int) -> bool:
    """Connect rather than bind. A leftover server bound to 127.0.0.1:8000 doesn't stop a new one
    binding 0.0.0.0:8000 — both succeed, and loopback wins, so the browser silently keeps talking to
    the old process while the new one looks healthy. Connecting is what actually detects that."""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_ports_free() -> bool:
    busy = {name: port for name, port in PORTS.items() if port_in_use(port)}
    if not busy:
        return True
    print("\nSomething is already listening on:")
    for name, port in busy.items():
        print(f"  {name}: port {port}")
    print("\nStarting anyway would leave you talking to the old process. Free them first:")
    print(f"  kill $(lsof -ti :{','.join(str(p) for p in busy.values())})" if platform.system() != "Windows"
          else f"  Get-NetTCPConnection -LocalPort {list(busy.values())[0]} | Select OwningProcess")
    return False


def ensure_docker_runtime():
    # Make sure a Docker runtime is actually running (Docker Desktop, Colima, or
    # anything else) before "docker compose up" is attempted anywhere. Doesn't touch
    # compose itself — that's the actual monitored/windowed action, handled separately.
    already_running = subprocess.run(
        ["docker", "info"], capture_output=True, check=False
    ).returncode == 0

    if already_running:
        print("Docker is already running.")
    elif platform.system() == "Darwin" and shutil.which("colima"):
        print("Colima isn't running — starting it now.")
        subprocess.run(["colima", "start"], check=False)
    else:
        print("Docker doesn't appear to be running — start your Docker runtime (Colima, Docker Desktop, or Docker Desktop's WSL2 backend on Windows) manually if this fails.")

    print("Note: this script only ever stops the database container on exit — it never stops your Docker runtime itself (Colima, Docker Desktop, or Docker Desktop's WSL2 backend). Stop that manually if you want it shut down too.")


def stop_database():
    print("Stopping database (docker compose down)...")
    subprocess.run(["docker", "compose", "down"], cwd=ROOT, check=False)


def open_separate_window(cmd: str, cwd: Path):
    system = platform.system()
    if system == "Darwin":
        script = f'tell application "Terminal" to do script "cd {cwd} && {cmd}"'
        subprocess.run(["osascript", "-e", script])
    elif system == "Windows":
        subprocess.run(["powershell", "-NoExit", "-Command", f"cd '{cwd}'; {cmd}"])
    elif system == "Linux":
        for term in ("gnome-terminal", "konsole", "xterm"):
            try:
                subprocess.Popen([term, "--", "bash", "-c", f"cd {cwd} && {cmd}; exec bash"])
                return
            except FileNotFoundError:
                continue
        print(f"No terminal emulator found (tried gnome-terminal/konsole/xterm) — run manually: cd {cwd} && {cmd}")
    else:
        print(f"Unrecognized OS '{system}' — run manually: cd {cwd} && {cmd}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--separate",
        action="store_true",
        help="Open database/backend/frontend in separate terminal windows instead of running inline",
    )
    args = parser.parse_args()

    if not check_ports_free():
        sys.exit(1)

    ensure_docker_runtime()

    if args.separate:
        print("Opening database, backend, and frontend in separate terminal windows...")
        open_separate_window(DATABASE_CMD, ROOT)
        time.sleep(1)
        open_separate_window(BACKEND_CMD, BACKEND_DIR)
        time.sleep(1)
        open_separate_window(FRONTEND_CMD, FRONTEND_DIR)
        print("Done — database, backend, and frontend are starting in their own windows.")
        return

    print("Starting database, backend, and frontend inline (Ctrl+C stops all)...")
    # start_new_session puts each child in its own process group, so kill_group can take down the
    # shell *and* the uvicorn/vite underneath it. terminate() alone only kills the shell, which is
    # how servers used to survive and squat on their ports.
    procs = {
        "database": subprocess.Popen(DATABASE_CMD, shell=True, cwd=ROOT, start_new_session=True),
        "backend": subprocess.Popen(BACKEND_CMD, shell=True, cwd=BACKEND_DIR, start_new_session=True),
        "frontend": subprocess.Popen(FRONTEND_CMD, shell=True, cwd=FRONTEND_DIR, start_new_session=True),
    }

    def kill_group(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()

    def shutdown() -> None:
        print("\nStopping database, backend, and frontend...")
        for name, proc in procs.items():
            if name != "database":
                kill_group(proc)
        for name, proc in procs.items():
            if name == "database":
                continue
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print(f"{name} didn't stop, killing it")
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        stop_database()

    # Closing the terminal sends SIGHUP; without this the children outlive the script.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: (shutdown(), sys.exit(0)))

    try:
        while True:
            for name, proc in procs.items():
                code = proc.poll()
                if code is not None:
                    print(f"\n{name} exited unexpectedly (code {code}) — stopping everything else...")
                    for other_name, other_proc in procs.items():
                        if other_name != name:
                            kill_group(other_proc)
                    stop_database()
                    return
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
