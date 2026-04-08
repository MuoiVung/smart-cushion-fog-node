"""
Docker Compose Manager for the Fog Node Launcher.

Manages the lifecycle of Docker Compose services (start, stop, status polling).
All subprocess calls are run in a background thread to keep the UI responsive.

Services managed:
  - mosquitto  (MQTT broker)
  - fog-node   (AI inference + WebSocket server)
"""

import json
import logging
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ServiceState(Enum):
    UNKNOWN    = "unknown"
    STARTING   = "starting"
    RUNNING    = "running"
    STOPPED    = "stopped"
    ERROR      = "error"


class ServiceStatus:
    """Snapshot of running service states."""

    def __init__(self) -> None:
        self.mosquitto: ServiceState = ServiceState.UNKNOWN
        self.fog_node:  ServiceState = ServiceState.UNKNOWN
        self.client_count: int = 0      # Reserved for future WS client count


# Callback type: receives a ServiceStatus on every poll cycle
StatusCallback = Callable[[ServiceStatus], None]
LogCallback    = Callable[[str], None]


class DockerManager:
    """
    Wraps `docker compose` CLI commands.

    Usage:
        dm = DockerManager(project_root, on_status=my_callback, on_log=log_fn)
        dm.start()   # non-blocking
        dm.stop()    # non-blocking
    """

    POLL_INTERVAL = 3  # seconds between status polls

    def __init__(
        self,
        project_root: Path,
        on_status: Optional[StatusCallback] = None,
        on_log:    Optional[LogCallback]    = None,
    ) -> None:
        self._root      = project_root
        self._on_status = on_status
        self._on_log    = on_log

        self._running   = False
        self._poll_thread: Optional[threading.Thread] = None

        self._native_mode = not self.is_docker_available()
        self._native_process: Optional[subprocess.Popen] = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start Docker Compose services (non-blocking)."""
        self._log("Starting Docker services…")
        thread = threading.Thread(target=self._do_start, daemon=True)
        thread.start()

    def stop(self) -> None:
        """Stop Docker Compose services (non-blocking)."""
        self._log("Stopping Docker services…")
        self._running = False
        thread = threading.Thread(target=self._do_stop, daemon=True)
        thread.start()

    def restart_fog_node(self) -> None:
        """Restart only the fog-node service (e.g., after model change)."""
        self._log("Restarting fog-node service…")
        thread = threading.Thread(target=self._do_restart_fog, daemon=True)
        thread.start()

    def is_docker_available(self) -> bool:
        """Return True if docker CLI is installed and responsive."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    # ── Private: subprocess operations ────────────────────────────────────

    def _do_start(self) -> None:
        """Run `docker compose up` or native `python app.py` and start polling."""
        try:
            if self._native_mode:
                self._log("Native mode: Starting python app.py...")
                self._native_process = subprocess.Popen(
                    [sys.executable, "app.py"],
                    cwd=str(self._root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                
                # Stream the output in a secondary thread
                def log_native_output():
                    if self._native_process and self._native_process.stdout:
                        for line in self._native_process.stdout:
                            stripped = line.rstrip()
                            if stripped:
                                self._log(stripped)
                threading.Thread(target=log_native_output, daemon=True).start()

                self._log("✅ Native Python services started successfully")
                self._start_polling()
            else:
                self._run_compose(
                    ["up", "-d", "--build"],
                    stream_log=True,
                )
                self._log("✅ Docker services started successfully")
                self._start_polling()
        except Exception as exc:
            self._log(f"❌ Failed to start services: {exc}")

    def _do_stop(self) -> None:
        """Run `docker compose down` or terminate native process."""
        try:
            if self._native_mode:
                if self._native_process:
                    self._native_process.terminate()
                    try:
                        self._native_process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._native_process.kill()
                    self._native_process = None
                self._log("🛑 Native Python services stopped")
            else:
                self._run_compose(["down"], stream_log=True)
                self._log("🛑 Docker services stopped")

            if self._on_status:
                status = ServiceStatus()
                status.mosquitto = ServiceState.STOPPED
                status.fog_node  = ServiceState.STOPPED
                self._on_status(status)
        except Exception as exc:
            self._log(f"❌ Failed to stop services: {exc}")

    def _do_restart_fog(self) -> None:
        """Run `docker compose restart fog-node` or restart native app."""
        try:
            if self._native_mode:
                self._do_stop()
                self._do_start()
            else:
                self._run_compose(["restart", "fog-node"], stream_log=True)
                self._log("🔄 fog-node restarted")
        except Exception as exc:
            self._log(f"❌ Failed to restart fog-node: {exc}")

    def _run_compose(self, args: list[str], stream_log: bool = False) -> None:
        """Execute a docker compose sub-command, optionally streaming output to log."""
        cmd = ["docker", "compose"] + args
        self._log(f"$ {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            cwd=str(self._root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if stream_log and process.stdout:
            for line in process.stdout:
                stripped = line.rstrip()
                if stripped:
                    self._log(stripped)
        process.wait()
        if process.returncode not in (0, None):
            raise RuntimeError(f"docker compose exited with code {process.returncode}")

    # ── Private: status polling ────────────────────────────────────────────

    def _start_polling(self) -> None:
        """Start a background thread that polls service health every POLL_INTERVAL s."""
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        while self._running:
            status = self._query_status()
            if self._on_status:
                self._on_status(status)
            time.sleep(self.POLL_INTERVAL)

    def _query_status(self) -> ServiceStatus:
        """Run `docker compose ps --format json` (or check native process) and parse the output."""
        status = ServiceStatus()
        
        if self._native_mode:
            # Mosquitto is assumed Running externally if user sets it up natively.
            status.mosquitto = ServiceState.RUNNING
            if self._native_process:
                if self._native_process.poll() is None:
                    status.fog_node = ServiceState.RUNNING
                else:
                    status.fog_node = ServiceState.ERROR
            else:
                status.fog_node = ServiceState.STOPPED
            return status

        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json"],
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return status

            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            for line in lines:
                try:
                    svc = json.loads(line)
                    name  = svc.get("Service", "")
                    state = svc.get("State", "").lower()

                    svc_state = (
                        ServiceState.RUNNING if state == "running"
                        else ServiceState.ERROR if state in ("exited", "dead")
                        else ServiceState.STARTING
                    )

                    if "mosquitto" in name:
                        status.mosquitto = svc_state
                    elif "fog" in name:
                        status.fog_node = svc_state
                except json.JSONDecodeError:
                    continue
        except (subprocess.TimeoutExpired, Exception) as exc:
            logger.warning(f"Status poll failed: {exc}")
        return status

    # ── Logging helper ─────────────────────────────────────────────────────

    def _log(self, message: str) -> None:
        logger.info(message)
        if self._on_log:
            self._on_log(message)
