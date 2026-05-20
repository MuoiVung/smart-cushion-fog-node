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
import os
import requests
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
        self._compose_dir = project_root
        self._on_status = on_status
        self._on_log    = on_log

        self._running   = False
        self._poll_thread: Optional[threading.Thread] = None
        self._log_threads: list[threading.Thread] = []
        self._log_processes: list[subprocess.Popen] = []

        self._native_mode = not self.is_docker_available()

        self._ngrok_process: Optional[subprocess.Popen] = None
        self._ngrok_url: Optional[str] = None
        self._native_app_process: Optional[subprocess.Popen] = None
        self._native_mosquitto_process: Optional[subprocess.Popen] = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self, authtoken: Optional[str] = None) -> None:
        """Start services in a background thread."""
        threading.Thread(target=self._do_start, args=(authtoken,), daemon=True).start()

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

    def rebuild(self) -> None:
        """Force rebuild of Docker images (non-blocking)."""
        self._log("🔨 Rebuilding Docker images (this may take 1-2 minutes)…")
        thread = threading.Thread(target=self._do_rebuild, daemon=True)
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

    def _do_start(self, authtoken: Optional[str] = None) -> None:
        """Run `docker compose up` or start native app."""
        try:
            self._log("🚀 Starting services...")
            
            # Start Ngrok if token provided
            if authtoken:
                self._start_ngrok(authtoken)

            if self._native_mode:
                self._log("Native mode: Starting python app.py and mosquitto...")
                
                # 1. Start Mosquitto
                try:
                    # Ensure data dir exists for persistence
                    (self._root / "mosquitto" / "data").mkdir(parents=True, exist_ok=True)
                    
                    # Use relative path for config to avoid Unicode issues in absolute path
                    mosquitto_cmd = ["mosquitto", "-c", "mosquitto/config/mosquitto.conf"]
                    
                    # On Windows, try to find mosquitto if not in path
                    if sys.platform == "win32":
                        common_paths = [
                            os.environ.get("ProgramFiles", "C:\\Program Files") + "\\mosquitto\\mosquitto.exe",
                            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)") + "\\mosquitto\\mosquitto.exe"
                        ]
                        for p in common_paths:
                            if os.path.exists(p):
                                mosquitto_cmd[0] = p
                                break

                    self._native_mosquitto_process = subprocess.Popen(
                        mosquitto_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        cwd=str(self._root)
                    )
                    self._log("✅ Local Mosquitto broker started")
                except Exception as e:
                    self._log(f"⚠️ Could not start Mosquitto automatically: {e}. Ensure it is running manually.")

                # 2. Start Python App
                is_frozen = getattr(sys, 'frozen', False)
                app_cmd = [sys.executable, "--backend"] if is_frozen else [sys.executable, "app.py"]
                self._native_app_process = subprocess.Popen(
                    app_cmd,
                    cwd=str(self._root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                
                # Stream the output in a secondary thread
                def log_output(proc, prefix):
                    if proc and proc.stdout:
                        for line in proc.stdout:
                            stripped = line.rstrip()
                            if stripped:
                                upper_stripped = stripped.upper()
                                # Specifically exclude routine AI predictions and sklearn warnings to keep console clean
                                if any(x in stripped for x in ["[AI] ✅", "[AI] ⚠️", "UserWarning:", "warnings.warn("]):
                                    continue
                                if "TOTAL_P=" in upper_stripped or "CONF=" in upper_stripped:
                                    continue
                                
                                # Filter: only show important logs in the console
                                # Hide routine AI predictions and MQTT debug logs
                                is_important = any(word in upper_stripped for word in [
                                    "ERROR", "CRITICAL", "FAILED", "WARNING", "SUCCESS", 
                                    "READY", "STARTING", "STOPPED", "CONNECTED", "RELOADING", 
                                    "HOT-RELOAD", "🔥", "❌", "✅", "🚀", "⚠️"
                                ])
                                
                                # Include standard startup informative printouts
                                is_startup = any(word in upper_stripped for word in [
                                    "LOADING", "BOUND", "LISTENING", "PORT", "VERSION", "RUNNING"
                                ])
                                
                                if is_important or is_startup or "SYSTEM READY" in upper_stripped:
                                    self._log(f"[{prefix}] {stripped}")
                                
                threading.Thread(target=log_output, args=(self._native_mosquitto_process, "MQTT"), daemon=True).start()
                threading.Thread(target=log_output, args=(self._native_app_process, "APP"), daemon=True).start()

                self._log("✅ Native Python services started successfully")
                self._start_polling()
            else:
                self._run_compose(
                    ["up", "-d"],
                    stream_log=True,
                )
                self._log("✅ Docker services started successfully")
                self._start_container_logs()
                self._start_polling()
        except Exception as exc:
            self._log(f"❌ Failed to start services: {exc}")

    def _kill_port(self, port: int) -> None:
        """Force kill any process listening on a specific port."""
        import signal
        if sys.platform != "win32":
            try:
                result = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
                if result.returncode == 0:
                    pids = result.stdout.strip().split("\n")
                    for pid_str in pids:
                        if pid_str.strip():
                            pid = int(pid_str.strip())
                            if pid != os.getpid():
                                self._log(f"Force killing process {pid} on port {port}")
                                os.kill(pid, signal.SIGKILL)
            except Exception as e:
                self._log(f"Error killing port {port}: {e}")
        else:
            try:
                result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if f":{port}" in line:
                            parts = line.strip().split()
                            if len(parts) >= 5:
                                pid = int(parts[-1])
                                if pid != os.getpid() and pid > 0:
                                    self._log(f"Force killing process {pid} on port {port}")
                                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            except Exception as e:
                self._log(f"Error killing port {port}: {e}")

    def _do_stop(self) -> None:
        """Run `docker compose down` or terminate native process."""
        try:
            if self._native_mode:
                if self._native_app_process:
                    try:
                        self._native_app_process.terminate()
                        self._native_app_process.wait(timeout=1)
                    except Exception:
                        try:
                            self._native_app_process.kill()
                        except Exception:
                            pass
                    self._native_app_process = None
                
                if self._native_mosquitto_process:
                    try:
                        self._native_mosquitto_process.terminate()
                        self._native_mosquitto_process.wait(timeout=1)
                    except Exception:
                        try:
                            self._native_mosquitto_process.kill()
                        except Exception:
                            pass
                    self._native_mosquitto_process = None
                
                # Force kill any orphaned processes on our ports
                self._kill_port(8000)
                self._kill_port(1883)
                self._kill_port(4040)
                    
                self._log("🛑 Native Python & Mosquitto services stopped")
            else:
                self._stop_container_logs()
                self._run_compose(["down"], stream_log=True)
                
                # Also kill any leftover orphaned native processes just in case
                self._kill_port(8000)
                self._kill_port(1883)
                self._kill_port(4040)
                
                self._log("🛑 Docker services stopped")

            self._stop_ngrok()
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
                self._log("✅ Services stopped")
            self._stop_ngrok()
        except Exception as exc:
            self._log(f"❌ Failed to restart fog-node: {exc}")

    def _do_rebuild(self) -> None:
        """Run `docker compose build`."""
        try:
            if not self._native_mode:
                self._run_compose(["build"], stream_log=True)
                self._log("✅ Rebuild complete! You can now Start Services.")
            else:
                self._log("ℹ️ Native mode: No rebuild needed.")
        except Exception as exc:
            self._log(f"❌ Rebuild failed: {exc}")

    def _run_compose(self, args: list[str], stream_log: bool = False) -> None:
        """Execute a docker compose sub-command, optionally streaming output to log."""
        cmd = ["docker", "compose"] + args
        self._log(f"🛠️  Running: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=self._compose_dir
        )

        if stream_log:
            for line in process.stdout:
                self._log(f"  {line.strip()}")
        
        process.wait()
        if process.returncode != 0:
            raise Exception(f"Command failed with exit code {process.returncode}")

    def _start_container_logs(self) -> None:
        """Start streaming logs from running containers."""
        if self._native_mode:
            return

        def stream_container(service_name: str, prefix: str):
            try:
                proc = subprocess.Popen(
                    ["docker", "compose", "logs", "-f", "--tail", "50", service_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(self._root)
                )
                self._log_processes.append(proc)
                if proc.stdout:
                    for line in proc.stdout:
                        stripped = line.rstrip()
                        if stripped:
                            # Clean up docker log prefixes like "fog-node-1  |"
                            if "|" in stripped:
                                content = stripped.split("|", 1)[1].strip()
                            else:
                                content = stripped
                            # Filter: only show important logs in the console
                            # Hide routine AI predictions and MQTT debug logs
                            upper_content = content.upper()
                            is_important = any(word in upper_content for word in [
                                "ERROR", "CRITICAL", "FAILED", "WARNING", "SUCCESS", 
                                "READY", "STARTING", "STOPPED", "CONNECTED", "RELOADING", 
                                "HOT-RELOAD", "🔥", "❌", "✅", "🚀", "⚠️"
                            ])
                            
                            # Specifically exclude routine AI predictions to keep console clean
                            if "[AI] ✅" in content or "[AI] ⚠️" in content:
                                is_important = False
                            
                            if is_important or "SYSTEM READY" in upper_content:
                                self._log(f"[{prefix}] {content}")

            except Exception as e:
                self._log(f"⚠️ Log streaming failed for {service_name}: {e}")

        # Stream Fog Node and Mosquitto
        t1 = threading.Thread(target=stream_container, args=("fog-node", "FOG"), daemon=True)
        t2 = threading.Thread(target=stream_container, args=("mosquitto", "MQTT-SVC"), daemon=True)
        t1.start()
        t2.start()
        self._log_threads.extend([t1, t2])

    def _stop_container_logs(self) -> None:
        """Terminate log streaming processes."""
        for proc in self._log_processes:
            try:
                proc.terminate()
            except:
                pass
        self._log_processes.clear()
        self._log_threads.clear()


    # ── Ngrok Management ───────────────────────────────────────────────────

    def _start_ngrok(self, authtoken: str) -> None:
        """Start ngrok tunnel for port 8765."""
        try:
            # 1. Configure authtoken
            self._log("🔑 Configuring ngrok authtoken...")
            subprocess.run(["ngrok", "config", "add-authtoken", authtoken], check=True, capture_output=True)
            
            # 2. Start ngrok process
            self._log("🌐 Opening ngrok tunnel (port 8765)...")
            self._ngrok_process = subprocess.Popen(
                ["ngrok", "http", "8765", "--log=stdout"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            
            # 3. Wait a bit for it to initialize
            time.sleep(2)
            self._log("✅ Ngrok process started")
            
        except Exception as e:
            self._log(f"❌ Failed to start ngrok: {e}")

    def _stop_ngrok(self) -> None:
        """Kill the ngrok process."""
        if self._ngrok_process:
            self._log("🛑 Stopping ngrok...")
            self._ngrok_process.terminate()
            self._ngrok_process = None
            self._ngrok_url = None

    def get_ngrok_url(self) -> Optional[str]:
        """Fetch the public URL from ngrok's local API."""
        try:
            res = requests.get("http://localhost:4040/api/tunnels", timeout=2)
            data = res.json()
            # Find the https/wss tunnel
            for tunnel in data.get('tunnels', []):
                if tunnel.get('proto') == 'https':
                    public_url = tunnel.get('public_url')
                    # Convert https:// to wss://
                    return public_url.replace("https://", "wss://")
        except:
            pass
        return None

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
            # Check Mosquitto
            mosquitto_running = False
            if self._native_mosquitto_process and self._native_mosquitto_process.poll() is None:
                mosquitto_running = True
            else:
                # If managed process is not running, check if port 1883 is in use by someone else
                import socket
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    if s.connect_ex(('localhost', 1883)) == 0:
                        mosquitto_running = True
            
            status.mosquitto = ServiceState.RUNNING if mosquitto_running else ServiceState.STOPPED

            # Check Fog Node
            if self._native_app_process:
                if self._native_app_process.poll() is None:
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
