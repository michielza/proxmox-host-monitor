import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request, session
from werkzeug.security import check_password_hash

from monitor.config import ConfigError, Store, public_host, validate_host
from monitor.certificates import CertificateTrust, connection_changed
from monitor.proxmox import APIError, collect, demo_sample

DEMO = {"id": "demo-server", "name": "Server 01", "provider": "Example Provider",
        "location": "Example Location", "node": "demo-node", "demo": True,
        "labels": {"server": "Demo", "cpu": "24 cores", "ram": "ECC",
                   "storage:local-lvm": "SSD"}}


def create_app(config_path=None):
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    store = Store(config_path or os.environ.get("PHM_CONFIG", "~/.config/proxmox-host-monitor/config.json"))
    config = store.load()
    app.config.update(
        SECRET_KEY=config["session_secret"], MAX_CONTENT_LENGTH=65536,
        SESSION_COOKIE_NAME="phm_session", SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get("PHM_LOCAL_HTTP") != "1",
        SESSION_COOKIE_SAMESITE="Strict", PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    app.extensions["store"] = store
    trust = CertificateTrust(store, config["session_secret"])
    attempts = defaultdict(deque)
    attempt_lock = threading.Lock()

    @app.errorhandler(ConfigError)
    def config_unavailable(error):
        return jsonify(error=str(error)), 503

    def version():
        return hashlib.sha256(store.load()["access_key_hash"].encode()).hexdigest()

    def authenticated():
        current = session.get("version")
        return isinstance(current, str) and hmac.compare_digest(current, version())

    def protected(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not authenticated():
                session.clear()
                return jsonify(error="Access key required."), 401
            if request.method != "GET":
                token = request.headers.get("X-CSRF-Token", "")
                if not hmac.compare_digest(token, session.get("csrf", "")) or not token:
                    return jsonify(error="Invalid request token. Reload and try again."), 403
            return fn(*args, **kwargs)
        return wrapper

    @app.before_request
    def request_checks():
        # Local HTTP is an explicit SSH-tunnel development mode, never public HTTP.
        if not request.is_secure:
            loopback = request.remote_addr in ("127.0.0.1", "::1")
            local_host = urlsplit(request.host_url).hostname in ("localhost", "127.0.0.1", "::1")
            if app.config["SESSION_COOKIE_SECURE"] or not loopback or not local_host:
                return jsonify(error="HTTPS is required. Local HTTP is only for an SSH tunnel."), 400
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return jsonify(error="Cross-origin request rejected."), 403
            if request.method != "DELETE" and not request.is_json:
                return jsonify(error="JSON request required."), 415

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/session")
    def session_status():
        if not authenticated():
            return jsonify(authenticated=False), 401
        return jsonify(authenticated=True, csrf=session["csrf"])

    @app.post("/api/login")
    def login():
        body = request.get_json(silent=True)
        key = body.get("key") if isinstance(body, dict) else None
        if not isinstance(key, str) or len(key) > 512:
            return jsonify(error="Enter your access key."), 400
        now = time.monotonic()
        with attempt_lock:
            for bucket in list(attempts):
                while attempts[bucket] and attempts[bucket][0] < now - 600:
                    attempts[bucket].popleft()
                if not attempts[bucket]:
                    del attempts[bucket]
            address = request.remote_addr
            if len(attempts[address]) >= 8 or len(attempts["global"]) >= 60:
                response = jsonify(error="Too many attempts. Try again in ten minutes.")
                response.headers["Retry-After"] = "600"
                return response, 429
            attempts[address].append(now)
            attempts["global"].append(now)
        current = store.load()
        if not check_password_hash(current["access_key_hash"], key):
            return jsonify(error="Incorrect access key."), 401
        session.clear()
        session.permanent = True
        session["version"] = hashlib.sha256(current["access_key_hash"].encode()).hexdigest()
        session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(csrf=session["csrf"])

    @app.post("/api/logout")
    @protected
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/hosts")
    @protected
    def hosts():
        current = store.load()
        rows = [public_host(h) for h in current["hosts"]]
        if current.get("demo"):
            rows.append(DEMO)
        return jsonify(hosts=rows, refresh_seconds=10)

    @app.get("/api/hosts/<host_id>/stats")
    @protected
    def stats(host_id):
        current = store.load()
        if host_id == DEMO["id"] and current.get("demo"):
            return jsonify(demo_sample(DEMO))
        host = next((h for h in current["hosts"] if h["id"] == host_id), None)
        if not host:
            return jsonify(error="Server not found."), 404
        try:
            return jsonify(collect(host))
        except APIError as exc:
            return jsonify(error=str(exc)), 502
        except (TypeError, KeyError, AttributeError):
            return jsonify(error="Unexpected Proxmox response. Check the node and API version."), 502

    def candidate(data):
        if not isinstance(data, dict):
            raise ValueError("A server object is required.")
        if data.get("id") == DEMO["id"]:
            raise ValueError("The demo server cannot be edited.")
        current = store.load()
        old = next((h for h in current["hosts"] if h["id"] == data.get("id")), None)
        if not old and len(current["hosts"]) >= 200:
            raise ValueError("Limit of 200 servers reached.")
        # Trust paths are backend-owned. Clients choose a trust method instead.
        host = validate_host(dict(data, ca_file=old.get("ca_file", "") if old else ""), old)
        choice = trust.choice(data, host, old, session["csrf"])
        return host, old, choice

    def verify_connection(host, choice):
        with trust.connection(host, choice) as connection:
            result = collect(connection)
        if result.get("errors"):
            raise APIError("Connection check failed: " + "; ".join(result["errors"].values()))

    @app.post("/api/connection/certificate")
    @protected
    def inspect_certificate():
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise ValueError("An API URL is required.")
            return jsonify(trust.inspection(data.get("url"), session["csrf"]))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/connection/test")
    @protected
    def test_connection():
        try:
            host, _, choice = candidate(request.get_json(silent=True))
            verify_connection(host, choice)
            return jsonify(ok=True, message="TLS verified. All four read-only API checks passed. Save to keep this connection.")
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except APIError as exc:
            return jsonify(ok=False, error=str(exc), kind=exc.kind)
        except (TypeError, KeyError, AttributeError):
            return jsonify(ok=False, error="Unexpected Proxmox response. Check the node and API version.")

    @app.post("/api/hosts")
    @protected
    def save_host():
        try:
            host, old, choice = candidate(request.get_json(silent=True))
            if connection_changed(host, old, choice):
                verify_connection(host, choice)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except APIError as exc:
            return jsonify(error=str(exc), kind=exc.kind), 502
        except (TypeError, KeyError, AttributeError):
            return jsonify(error="Unexpected Proxmox response. Check the node and API version."), 502
        with store.transaction():
            current = store.load()
            latest = next((h for h in current["hosts"] if h["id"] == host["id"]), None)
            if latest != old:
                return jsonify(error="This server changed while testing. Reload its settings and try again."), 409
            if not old and len(current["hosts"]) >= 200:
                return jsonify(error="Limit of 200 servers reached."), 400
            try:
                host["ca_file"] = trust.persist(choice)
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            current["hosts"] = [h for h in current["hosts"] if h["id"] != host["id"]] + [host]
            store.write(current)
        return jsonify(host=public_host(host))

    @app.delete("/api/hosts/<host_id>")
    @protected
    def delete_host(host_id):
        with store.transaction():
            current = store.load()
            old_count = len(current["hosts"])
            current["hosts"] = [h for h in current["hosts"] if h["id"] != host_id]
            if len(current["hosts"]) == old_count:
                return jsonify(error="Server not found."), 404
            store.write(current)
        return jsonify(ok=True)

    return app
