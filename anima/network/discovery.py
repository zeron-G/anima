"""Node discovery — mDNS (zeroconf) + manual peer configuration."""

import socket
from typing import Callable

from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf
from anima.utils.logging import get_logger

log = get_logger("network.discovery")

SERVICE_TYPE = "_anima._tcp.local."


def get_local_ip() -> str:
    """Get this machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class DiscoveryService:
    """mDNS service for automatic node discovery on LAN."""

    def __init__(
        self,
        node_id: str,
        port: int = 9420,
        capabilities: list[str] | None = None,
        on_node_found: Callable | None = None,
        on_node_removed: Callable | None = None,
    ):
        self._node_id = node_id
        self._port = port
        self._capabilities = capabilities or []
        self._on_node_found = on_node_found
        self._on_node_removed = on_node_removed
        self._zeroconf: Zeroconf | None = None
        self._browser: ServiceBrowser | None = None
        self._info: ServiceInfo | None = None
        self._discovered: dict[str, str] = {}  # node_id -> "ip:port"

    def start(self) -> None:
        """Start advertising and browsing for ANIMA nodes."""
        self._zeroconf = Zeroconf()
        ip = get_local_ip()

        # Register our service
        self._info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=f"{self._node_id}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=self._port,
            properties={
                b"node_id": self._node_id.encode(),
                b"capabilities": ",".join(self._capabilities).encode(),
            },
        )
        self._zeroconf.register_service(self._info)
        log.info("mDNS service registered: %s at %s:%d", self._node_id, ip, self._port)

        # Browse for other nodes
        self._browser = ServiceBrowser(
            self._zeroconf, SERVICE_TYPE, handlers=[self._on_service_change]
        )

    def stop(self) -> None:
        if self._info and self._zeroconf:
            self._zeroconf.unregister_service(self._info)
        if self._zeroconf:
            self._zeroconf.close()
        log.info("mDNS discovery stopped")

    def get_discovered(self) -> dict[str, str]:
        return dict(self._discovered)

    def _on_service_change(self, zeroconf: Zeroconf, service_type: str,
                           name: str, state_change) -> None:
        if state_change == ServiceStateChange.Added or state_change == ServiceStateChange.Updated:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                node_id = info.properties.get(b"node_id", b"").decode()
                if node_id and node_id != self._node_id:
                    addresses = info.parsed_addresses()
                    if addresses:
                        addr = f"{addresses[0]}:{info.port}"
                        self._discovered[node_id] = addr
                        log.info("Discovered node: %s at %s", node_id, addr)
                        if self._on_node_found:
                            self._on_node_found(node_id, addr)
        elif state_change == ServiceStateChange.Removed:
            # Extract node_id from service name (format: "<node_id>._anima._tcp.local.")
            node_id = name.replace(f".{service_type}", "").rstrip(".")
            addr = self._discovered.pop(node_id, None)
            if addr:
                log.info("Node removed: %s at %s", node_id, addr)
                if self._on_node_removed:
                    self._on_node_removed(node_id, addr)
