import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional


def _probe(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def discover_hosts(
    subnet: str,
    port: int = 22,
    timeout: float = 0.5,
    max_workers: int = 128,
    limit: Optional[int] = None,
) -> List[str]:
    network = ipaddress.ip_network(subnet, strict=False)
    results: List[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_probe, str(ip), port, timeout): str(ip)
            for ip in network.hosts()
        }
        for future in as_completed(future_map):
            ip = future_map[future]
            if future.result():
                results.append(ip)
                if limit and len(results) >= limit:
                    break
    return results
