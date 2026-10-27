"""One ICMP echo, without a shell or elevated process privileges."""
import ipaddress
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
from urllib.parse import urlsplit

from icmplib import ICMPLibError, NameLookupError, SocketPermissionError
from icmplib import ping as icmp_ping


class PingError(Exception):
    """Local probe failure, distinct from a target that sends no echo reply."""


def ping(host):
    address = urlsplit(host["url"]).hostname
    if platform.system() == "Windows":
        # Resolve first so the executable receives a numeric address, never an option.
        try:
            try:
                ipaddress.ip_address(address)
            except ValueError:
                address = socket.getaddrinfo(address, None, type=socket.SOCK_DGRAM)[0][4][0]
            executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE"
            result = subprocess.run([str(executable), "-n", "1", "-w", "2000", address],
                                    capture_output=True, text=True, errors="replace", timeout=3)
        except socket.gaierror:
            raise PingError("Ping address lookup failed on the monitor.") from None
        except subprocess.TimeoutExpired:
            raise PingError("The monitor's ping command did not finish in time.") from None
        except OSError:
            raise PingError("The monitor could not start its ping command.") from None
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            raise PingError("The monitor's ping command failed.")
        # Match the RTT unit rather than the localized word for 'time'.
        match = re.search(r"[=<]\s*([\d]+(?:[.,]\d+)?)\s*(?:ms|毫秒)", result.stdout, re.IGNORECASE)
        if not match:
            raise PingError("Ping returned no readable round-trip time.")
        return float(match.group(1).replace(",", "."))
    try:
        result = icmp_ping(address, count=1, interval=0, timeout=2, privileged=False)
        return result.avg_rtt if result.is_alive else None
    except SocketPermissionError:
        raise PingError("The monitor OS does not permit unprivileged ICMP probes.") from None
    except NameLookupError:
        raise PingError("Ping address lookup failed on the monitor.") from None
    except (ICMPLibError, OSError):
        raise PingError("The monitor could not send an ICMP probe.") from None
