import socket
import requests
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

class DiscoveryService:
    """
    Service to report the Fog Node's local and public IP to Firebase
    to facilitate automatic discovery by the Web App.
    """
    def __init__(self, device_id: str, firebase_url: str):
        self.device_id = device_id
        self.firebase_url = firebase_url.rstrip('/')
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def get_local_ip(self) -> str:
        """Determines the local IP address used for outbound traffic."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Connect to a public IP (doesn't send any data) to find local interface IP
            s.connect(('8.8.8.8', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
        finally:
            s.close()
        return ip

    def get_public_ip(self) -> Optional[str]:
        """Fetches the public IP address of the current network."""
        try:
            response = requests.get('https://api.ipify.org', timeout=5)
            return response.text
        except Exception as e:
            logger.warning(f"Could not fetch public IP: {e}")
            return None

    def report_status(self):
        """Sends IP metadata to Firebase."""
        local_ip = self.get_local_ip()
        public_ip = self.get_public_ip()
        
        url = f"{self.firebase_url}/devices/{self.device_id}.json"
        payload = {
            "local_ip": local_ip,
            "public_ip": public_ip,
            "timestamp": int(time.time() * 1000)
        }
        
        try:
            response = requests.put(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"Discovery: Reported IP {local_ip} to Firebase.")
            else:
                logger.error(f"Discovery: Failed to report to Firebase: {response.status_code}")
        except Exception as e:
            logger.error(f"Discovery: Network error reporting to Firebase: {e}")

    def start(self, interval_sec: int = 300):
        """Starts a background thread to periodically report status."""
        def _loop():
            while not self._stop_event.is_set():
                self.report_status()
                # Wait for interval or stop signal
                self._stop_event.wait(interval_sec)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        logger.info("Discovery service started.")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
