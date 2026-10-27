"""Configuration is private runtime state, never part of the source checkout."""
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit

LOCK = threading.RLock()
ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
NODE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")


def text(value, name, limit=160):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must contain 1–{limit} characters.")
    if any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} contains control characters.")
    return value.strip()


def validate_host(data, previous=None):
    if not isinstance(data, dict):
        raise ValueError("A server object is required.")
    host = {k: text(data.get(k), k) for k in ("id", "name", "provider", "location", "node")}
    if not ID.fullmatch(host["id"]) or not NODE.fullmatch(host["node"]):
        raise ValueError("Invalid server ID or Proxmox node name.")
    url = text(data.get("url"), "API URL", 500).rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("API URL must be an HTTPS origin, e.g. https://pve.example.net:8006.")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Invalid API port.") from None
    host["url"] = url
    token_id = text(data.get("token_id"), "Token ID", 250)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", token_id):
        raise ValueError("Token ID must use user@realm!token format.")
    host["token_id"] = token_id
    secret = data.get("token_secret", "")
    if not isinstance(secret, str):
        raise ValueError("Token secret must be text.")
    if not secret and previous:
        if previous["url"] != url or previous["token_id"] != token_id:
            raise ValueError("Re-enter the token secret when changing its destination or ID.")
        secret = previous["token_secret"]
    host["token_secret"] = text(secret, "Token secret", 500)
    ca = data.get("ca_file", "")
    if not isinstance(ca, str) or len(ca) > 500 or (ca and not Path(ca).is_absolute()):
        raise ValueError("CA file must be an absolute path on the monitor server.")
    host["ca_file"] = ca
    labels = data.get("labels", {})
    if not isinstance(labels, dict) or len(labels) > 100:
        raise ValueError("Labels must be an object with at most 100 entries.")
    host["labels"] = {text(k, "Label target", 120): text(v, "Label", 250) for k, v in labels.items()}
    return host


def public_host(host):
    return {k: copy.deepcopy(v) for k, v in host.items() if k != "token_secret"}


class Store:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        source = Path(__file__).resolve().parent.parent
        if self.path.is_relative_to(source):
            raise ValueError("Runtime config must be outside the source checkout.")

    def load(self):
        with LOCK:
            with self.path.open(encoding="utf-8") as handle:
                return json.load(handle)

    def write(self, config):
        with LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".monitor-", dir=self.path.parent)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(config, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
