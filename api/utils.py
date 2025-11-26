import ipaddress
import platform
import re
import socket
import subprocess
from typing import Optional


def import_class(path: str):
    module_name = ".".join(path.split(".")[:-1])
    class_name = path.split(".")[-1]
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


def _normalize_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-f]", "", mac.lower())


def _ping(ip: str) -> None:
    system = platform.system().lower()
    try:
        if system == "windows":
            subprocess.run(["ping", "-n", "1", "-w", "200", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _read_arp() -> str:
    system = platform.system().lower()
    cmd = ["arp", "-a"] if system == "windows" else ["arp", "-an"]
    return subprocess.check_output(cmd, text=True, encoding="utf-8", errors="ignore")


def _get_local_network() -> Optional[ipaddress.IPv4Network]:
    system = platform.system().lower()
    try:
        ip = None
        netmask = None
        if system == "windows":
            output = subprocess.check_output(["ipconfig"], text=True, encoding="utf-8", errors="ignore")
            for line in output.splitlines():
                line = line.strip()
                if "IPv4 Address" in line or "IPv4-адрес" in line:
                    ip = line.split(":")[-1].strip()
                elif "Subnet Mask" in line or "Маска подсети" in line:
                    netmask = line.split(":")[-1].strip()
                if ip and netmask:
                    break
        else:
            # ip -4 addr
            try:
                output = subprocess.check_output(["ip", "-4", "addr"], text=True, encoding="utf-8", errors="ignore")
                for line in output.splitlines():
                    line = line.strip()
                    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
                    if m and not m.group(1).startswith("127."):
                        ip = m.group(1)
                        prefix = int(m.group(2))
                        netmask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
                        break
            except Exception:
                pass
            if not ip:
                # ifconfig fallback (macOS/BSD/Linux)
                try:
                    output = subprocess.check_output(["ifconfig"], text=True, encoding="utf-8", errors="ignore")
                    for line in output.splitlines():
                        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)\s+netmask (0x[0-9a-f]+)", line, re.IGNORECASE)
                        if m and not m.group(1).startswith("127."):
                            ip = m.group(1)
                            mask_hex = int(m.group(2), 16)
                            netmask = str(ipaddress.IPv4Address(mask_hex))
                            break
                        m2 = re.search(r"inet (\d+\.\d+\.\d+\.\d+)\s+netmask (\d+\.\d+\.\d+\.\d+)", line)
                        if m2 and not m2.group(1).startswith("127."):
                            ip = m2.group(1)
                            netmask = m2.group(2)
                            break
                except Exception:
                    pass
        if not ip:
            try:
                host_ip = socket.gethostbyname(socket.gethostname())
                if host_ip and not host_ip.startswith("127."):
                    ip = host_ip
                    netmask = "255.255.255.0"
            except Exception:
                pass
        if not ip or not netmask:
            return None
        ip_int = int(ipaddress.IPv4Address(ip))
        mask_int = int(ipaddress.IPv4Address(netmask))
        network_int = ip_int & mask_int
        return ipaddress.IPv4Network((network_int, mask_int), strict=False)
    except Exception:
        return None


def find_ip_by_mac(target_mac: str) -> Optional[str]:
    """
    Определяет подсеть (ip/mask), делает ping sweep, читает ARP и ищет MAC.
    """
    target = _normalize_mac(target_mac)
    net = _get_local_network()
    if not net:
        return None

    for ip in net.hosts():
        _ping(str(ip))

    arp_output = _read_arp()
    for line in arp_output.splitlines():
        line = line.strip()
        m = re.search(r"\((?P<ip>\d+\.\d+\.\d+\.\d+)\)\s+at\s+(?P<mac>[0-9a-f:]{17})", line, re.IGNORECASE)
        if m and _normalize_mac(m.group("mac")) == target:
            return m.group("ip")
        m2 = re.search(r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mac>([0-9a-f]{2}-){5}[0-9a-f]{2})", line, re.IGNORECASE)
        if m2 and _normalize_mac(m2.group("mac")) == target:
            return m2.group("ip")
    return None
