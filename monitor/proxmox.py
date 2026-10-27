"""Only explicit GET endpoints; no Proxmox mutation methods exist here."""
from concurrent.futures import ThreadPoolExecutor
import math
import re
import subprocess
import ssl
import time
from urllib.parse import quote, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context


class APIError(Exception):
    def __init__(self, message, kind="api_error"):
        super().__init__(message)
        self.kind = kind


class CertificateAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        # Permit an explicitly approved leaf as trust anchor on Python 3.11+.
        # Certificate/expiry/hostname verification remain required.
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


def get(host, endpoint):
    node = quote(host["node"], safe="")
    allowed = {f"/nodes/{node}/status", f"/nodes/{node}/storage",
               f"/nodes/{node}/network", "/cluster/resources"}
    if endpoint not in allowed:
        raise APIError("Unsupported monitoring endpoint.")
    # Ignore proxy/netrc environment so they cannot alter credential routing.
    with requests.Session() as client:
        client.trust_env = False
        if host.get("ca_file"):
            client.mount("https://", CertificateAdapter())
        try:
            response = client.get(
                host["url"] + "/api2/json" + endpoint,
                headers={"Authorization": f"PVEAPIToken={host['token_id']}={host['token_secret']}"},
                verify=host.get("ca_file") or True, timeout=(3, 5), allow_redirects=False,
            )
            if response.status_code in (401, 403):
                raise APIError("API access denied. Check the user and token permissions.")
            if response.status_code != 200:
                raise APIError(f"API returned HTTP {response.status_code}.")
            return response.json()["data"]
        except requests.exceptions.SSLError:
            raise APIError("Certificate verification failed. Check certificate trust in Configuration.", "tls_error") from None
        except requests.exceptions.Timeout:
            raise APIError("API request timed out.") from None
        except requests.exceptions.RequestException:
            raise APIError("Cannot connect to the Proxmox API.") from None
        except OSError:
            raise APIError("Cannot read the configured trusted CA file.") from None
        except (ValueError, KeyError):
            raise APIError("Unexpected API response.") from None


def ping(host):
    """One bounded ICMP echo to the configured API host; no shell interpolation."""
    try:
        result = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", "2", "--", urlsplit(host["url"]).hostname],
            capture_output=True, text=True, timeout=3, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        )
        match = re.search(r"time[=<]([\d.]+)\s*ms", result.stdout)
        return float(match.group(1)) if result.returncode == 0 and match else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def collect(host):
    node = quote(host["node"], safe="")
    endpoints = {"status": f"/nodes/{node}/status", "storage": f"/nodes/{node}/storage",
                 "network": f"/nodes/{node}/network", "guests": "/cluster/resources"}
    # Independent reads keep a slow optional metric from hiding CPU/RAM.
    errors, data, failures = {}, {}, {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {k: pool.submit(get, host, endpoint) for k, endpoint in endpoints.items()}
        echo = pool.submit(ping, host)
        for key, job in jobs.items():
            try:
                data[key] = job.result()
            except APIError as exc:
                errors[key] = str(exc)
                failures[key] = exc
        latency = echo.result()
    if "status" not in data:
        raise failures["status"]
    status = data["status"]
    memory = status.get("memory", {})
    cpu = status.get("cpuinfo", {})
    guests = [g for g in data.get("guests", []) if g.get("node") == host["node"]
              and g.get("type") in ("qemu", "lxc") and not g.get("template")]
    counts = {}
    if "guests" in data:
        for kind in ("qemu", "lxc"):
            group = [g for g in guests if g["type"] == kind]
            counts[kind] = {"running": sum(g.get("status") == "running" for g in group),
                            "stopped": sum(g.get("status") == "stopped" for g in group),
                            "other": sum(g.get("status") not in ("running", "stopped") for g in group),
                            "total": len(group)}
    return {"sampled_at": time.time(), "demo": False, "errors": errors,
            "cpu": {"model": cpu.get("model"), "cores": cpu.get("cores"),
                    "sockets": cpu.get("sockets"), "threads": cpu.get("cpus"),
                    "usage": status.get("cpu")},
            "memory": {"total": memory.get("total"), "used": memory.get("used"),
                       "free": memory.get("free"), "available": memory.get("available")},
            "storage": data.get("storage") if "storage" in data else None,
            "network": [{k: iface.get(k) for k in ("iface", "type", "active", "autostart")}
                        for iface in data.get("network", [])],
            "guests": counts, "ping_ms": latency, "version": status.get("pveversion")}


def demo_sample(host):
    phase = time.time() / 25
    total = 128 * 1024**3
    used = int(total * (0.43 + 0.03 * math.sin(phase / 2)))
    return {"sampled_at": time.time(), "demo": True, "errors": {},
            "cpu": {"model": "Example 24-core CPU", "cores": 24, "sockets": 1,
                    "threads": 48, "usage": 0.22 + 0.09 * math.sin(phase)},
            "memory": {"total": total, "used": used, "free": total-used, "available": total-used},
            "storage": [{"storage": "local", "type": "dir", "total": 240 * 1024**3,
                         "used": 41 * 1024**3, "avail": 199 * 1024**3, "active": 1},
                        {"storage": "local-lvm", "type": "lvmthin", "total": 2 * 1024**4,
                         "used": int(0.7 * 1024**4), "avail": int(1.3 * 1024**4), "active": 1}],
            "network": [{"iface": "eno1", "type": "eth", "active": 1}],
            "guests": {"qemu": {"running": 12, "stopped": 3, "other": 0, "total": 15},
                       "lxc": {"running": 2, "stopped": 0, "other": 0, "total": 2}},
            "ping_ms": round(9 + 2 * math.sin(phase), 2), "version": "Demo data"}
