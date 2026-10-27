import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from werkzeug.security import generate_password_hash

from monitor.app import create_app
from monitor.config import Store, validate_host
from monitor.proxmox import APIError, collect, get

KEY = "a-test-access-key-that-is-long-enough"


def host():
    return {"id": "server-01", "name": "Server 01", "provider": "Example Provider",
            "location": "Example Location", "node": "pve-01", "url": "https://pve.example.net:8006",
            "token_id": "monitor@pve!dashboard", "token_secret": "test-token-secret", "labels": {}}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"
        self.store = Store(self.path)
        self.store.write({"hosts": [validate_host(host())], "demo": True,
                          "session_secret": "test-signing-secret",
                          "access_key_hash": generate_password_hash(KEY, method="pbkdf2:sha256:1000")})
        self.env = patch.dict(os.environ, {"PHM_LOCAL_HTTP": "1"})
        self.env.start()
        self.app = create_app(self.path)
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def login(self):
        result = self.client.post("/api/login", json={"key": KEY})
        self.assertEqual(result.status_code, 200)
        return {"X-CSRF-Token": result.json["csrf"]}

    def test_auth_gate_and_no_secret_in_host_list(self):
        self.assertEqual(self.client.get("/api/hosts").status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"key": "wrong"}).status_code, 401)
        self.login()
        result = self.client.get("/api/hosts")
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("test-token-secret", result.text)
        self.assertNotIn("access_key_hash", result.text)

    def test_secure_mode_and_cookie(self):
        with patch.dict(os.environ, {"PHM_LOCAL_HTTP": "0"}):
            app = create_app(self.path)
        client = app.test_client()
        self.assertEqual(client.get("/").status_code, 400)
        result = client.post("/api/login", json={"key": KEY}, base_url="https://monitor.example.net")
        self.assertEqual(result.status_code, 200)
        self.assertIn("Secure", result.headers["Set-Cookie"])
        self.assertIn("HttpOnly", result.headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", result.headers["Set-Cookie"])

    def test_local_http_rejects_nonlocal_host(self):
        self.assertEqual(self.client.get("/", base_url="http://monitor.example.net").status_code, 400)

    def test_csrf_and_cross_origin(self):
        headers = self.login()
        self.assertEqual(self.client.post("/api/hosts", json=host()).status_code, 403)
        headers["Origin"] = "https://other.example.net"
        self.assertEqual(self.client.post("/api/hosts", json=host(), headers=headers).status_code, 403)

    def test_rotation_invalidates_existing_session(self):
        self.login()
        config = self.store.load()
        config["access_key_hash"] = generate_password_hash("another-test-key", method="pbkdf2:sha256:1000")
        self.store.write(config)
        self.assertEqual(self.client.get("/api/hosts").status_code, 401)

    def test_login_rate_limit(self):
        for _ in range(8):
            self.client.post("/api/login", json={"key": "bad"})
        result = self.client.post("/api/login", json={"key": KEY})
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers["Retry-After"], "600")

    def test_save_retains_secret_and_delete_only_monitor_entry(self):
        headers = self.login()
        modified = host()
        modified["token_secret"] = ""
        modified["labels"] = {"ram": "ECC", "storage:local-lvm": "SSD"}
        result = self.client.post("/api/hosts", json=modified, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("token_secret", result.text)
        self.assertEqual(self.store.load()["hosts"][0]["token_secret"], "test-token-secret")
        self.assertEqual(self.client.delete("/api/hosts/server-01", headers=headers).status_code, 200)
        self.assertEqual(self.store.load()["hosts"], [])

    def test_secret_is_not_forwarded_to_changed_destination(self):
        headers = self.login()
        modified = host()
        modified.update(url="https://another.example.net:8006", token_secret="")
        self.assertEqual(self.client.post("/api/hosts", json=modified, headers=headers).status_code, 400)

    def test_api_get_only_verified_tls_no_redirects_no_environment_proxy(self):
        with patch("monitor.proxmox.requests.Session") as factory:
            client = factory.return_value.__enter__.return_value
            client.get.return_value.status_code = 200
            client.get.return_value.json.return_value = {"data": {"cpu": .1}}
            self.assertEqual(get(host(), "/nodes/pve-01/status"), {"cpu": .1})
            self.assertFalse(client.trust_env)
            options = client.get.call_args.kwargs
            self.assertIs(options["verify"], True)
            self.assertFalse(options["allow_redirects"])
            with self.assertRaises(APIError):
                get(host(), "/nodes/pve-01/reboot")
            client.get.return_value.status_code = 302
            with self.assertRaises(APIError):
                get(host(), "/nodes/pve-01/status")

    def test_partial_api_failure_templates_and_other_nodes(self):
        def api_reply(_, endpoint):
            if endpoint.endswith("/status"):
                return {"cpu": .2, "cpuinfo": {}, "memory": {"total": 100, "used": 40}}
            if endpoint.endswith("/storage"):
                raise APIError("Storage denied")
            if endpoint.endswith("/network"):
                return []
            return [{"node": "pve-01", "type": "qemu", "status": "running"},
                    {"node": "pve-01", "type": "qemu", "status": "stopped"},
                    {"node": "pve-01", "type": "qemu", "status": "stopped", "template": 1},
                    {"node": "other", "type": "qemu", "status": "running"}]
        with patch("monitor.proxmox.get", side_effect=api_reply), patch("monitor.proxmox.ping", return_value=None):
            data = collect(host())
        self.assertEqual(data["cpu"]["usage"], .2)
        self.assertIsNone(data["storage"])
        self.assertEqual(data["errors"]["storage"], "Storage denied")
        self.assertEqual(data["guests"]["qemu"]["total"], 2)
        self.assertIsNone(data["memory"]["available"])

    def test_demo_does_not_touch_proxmox(self):
        self.login()
        with patch("monitor.app.collect") as collector:
            result = self.client.get("/api/hosts/demo-server/stats")
        self.assertTrue(result.json["demo"])
        collector.assert_not_called()

    def test_config_private_and_invalid_connections_rejected(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        for invalid in ("http://pve.example.net", "https://user:secret@pve.example.net", "https://pve.example.net/path"):
            data = host(); data["url"] = invalid
            with self.assertRaises(ValueError):
                validate_host(data)


if __name__ == "__main__":
    unittest.main()
