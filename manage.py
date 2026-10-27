"""Local operator commands; secrets are entered without terminal echo."""
import argparse
from getpass import getpass
import os
import secrets

from werkzeug.security import generate_password_hash

from monitor.config import LOCK, Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "rotate-key"))
    parser.add_argument("--config", default=os.environ.get("PHM_CONFIG", "~/.config/proxmox-host-monitor/config.json"))
    parser.add_argument("--demo", action="store_true", help="Include an explicitly fictional demo server")
    args = parser.parse_args()
    store = Store(args.config)
    if args.action == "init" and store.path.exists():
        parser.error("Configuration already exists; refusing to overwrite it.")
    key = getpass("Access key (at least 20 characters): ")
    if len(key) < 20 or len(key) > 512 or key != getpass("Repeat access key: "):
        parser.error("Keys must match and contain 20–512 characters.")
    with LOCK:
        config = store.load() if args.action == "rotate-key" else {
            "hosts": [], "demo": args.demo, "session_secret": secrets.token_urlsafe(48),
        }
        config["access_key_hash"] = generate_password_hash(key)
        store.write(config)
    print("Access key updated. Existing sessions are invalidated.")


if __name__ == "__main__":
    main()
