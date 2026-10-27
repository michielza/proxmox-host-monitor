import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from werkzeug.security import generate_password_hash

from monitor.app import create_app
from monitor.config import ConfigError, Store, validate_host
from monitor.proxmox import APIError, collect, get
import manage

KEY = "a-test-access-key-that-is-long-enough"


def host():
    return {"id": "server-01", "name": "Server 01", "provider": "Example Provider",
            "location": "Example Location", "node": "pve-01", "url": "https://pve.example.net:8006",
            "token_id": "monitor@pve!dashboard", "token_secret": "test-token-secret", "labels": {}}


def process_edit(path, started, acquired):
    store = Store(path)
    started.set()
    with store.transaction():
        acquired.set()
        config = store.load()
        config["hosts"][0]["labels"] = {"ram": "ECC"}
        store.write(config)


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
        for invalid in ("http://pve.example.net", "https://user:secret@pve.example.net", "https://pve.example.net/path"):
            data = host(); data["url"] = invalid
            with self.assertRaises(ValueError):
                validate_host(data)

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions; Windows uses ACLs")
    def test_config_file_is_private_on_posix(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_config_transactions_serialize_independent_processes(self):
        context = multiprocessing.get_context("spawn")
        started, acquired = context.Event(), context.Event()
        child = context.Process(target=process_edit, args=(str(self.path), started, acquired))
        try:
            with self.store.transaction():
                child.start()
                self.assertTrue(started.wait(10), "Child did not start")
                self.assertFalse(acquired.wait(.25), "Other process entered a locked transaction")
                config = self.store.load()
                config["access_key_hash"] = "rotated-test-hash"
                self.store.write(config)
            child.join(10)
            self.assertEqual(child.exitcode, 0)
            saved = self.store.load()
            self.assertEqual(saved["access_key_hash"], "rotated-test-hash")
            self.assertEqual(saved["hosts"][0]["labels"], {"ram": "ECC"})
        finally:
            if child.pid is not None:
                if child.is_alive():
                    child.terminate()
                child.join(5)

    def test_rotation_keeps_edits_made_during_hash_generation(self):
        def hash_while_another_writer_saves(_):
            with self.store.transaction():
                config = self.store.load()
                config["hosts"][0]["labels"] = {"cpu": "New label"}
                self.store.write(config)
            return "rotated-test-hash"
        with patch("sys.argv", ["manage.py", "rotate-key", "--config", str(self.path)]), \
                patch("manage.getpass", return_value=KEY), \
                patch("manage.generate_password_hash", side_effect=hash_while_another_writer_saves), \
                patch("builtins.print"):
            manage.main()
        config = self.store.load()
        self.assertEqual(config["access_key_hash"], "rotated-test-hash")
        self.assertEqual(config["hosts"][0]["labels"], {"cpu": "New label"})

    def test_corrupt_config_returns_safe_503_and_recovers(self):
        self.login()
        original = self.path.read_text(encoding="utf-8")
        for content in ("invalid json", '{"hosts": 3}'):
            self.path.write_text(content, encoding="utf-8")
            response = self.client.get("/api/hosts")
            self.assertEqual(response.status_code, 503)
            self.assertIn("Configuration is unavailable", response.json["error"])
            self.assertNotIn(str(self.path), response.text)
        self.path.write_text(original, encoding="utf-8")
        self.assertEqual(self.client.get("/api/hosts").status_code, 200)

    def test_missing_config_returns_safe_503(self):
        self.login()
        self.path.unlink()
        self.assertEqual(self.client.get("/api/session").status_code, 503)

    def test_ipv6_loopback_and_hostname_confusion(self):
        response = self.client.get("/", base_url="http://[::1]:8080", environ_overrides={"REMOTE_ADDR": "::1"})
        self.assertEqual(response.status_code, 200)
        for origin in ("http://localhost.example.net", "http://127.0.0.1.example.net", "http://[::2]:8080"):
            self.assertEqual(self.client.get("/", base_url=origin).status_code, 400)

    def test_disable_demo_cli_does_not_prompt_for_key(self):
        with patch("sys.argv", ["manage.py", "disable-demo", "--config", str(self.path)]), \
                patch("manage.getpass") as prompt, patch("builtins.print"):
            manage.main()
        prompt.assert_not_called()
        self.assertFalse(self.store.load()["demo"])
        self.assertEqual(len(self.store.load()["hosts"]), 1)

    def test_rotate_missing_config_has_friendly_error(self):
        self.path.unlink()
        with patch("sys.argv", ["manage.py", "rotate-key", "--config", str(self.path)]), \
                patch("manage.getpass") as prompt, patch("sys.stderr") as stderr:
            with self.assertRaises(SystemExit) as error:
                manage.main()
        self.assertEqual(error.exception.code, 2)
        prompt.assert_not_called()
        self.assertIn("Run init first", "".join(call.args[0] for call in stderr.write.call_args_list))

    def test_session_restore_and_logout(self):
        headers = self.login()
        restored = self.client.get("/api/session")
        self.assertTrue(restored.json["authenticated"])
        self.assertEqual(restored.json["csrf"], headers["X-CSRF-Token"])
        self.assertEqual(self.client.post("/api/logout", json={}, headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/session").status_code, 401)

    def test_host_cap_and_label_limits(self):
        headers = self.login()
        config = self.store.load()
        config["hosts"] = [validate_host(dict(host(), id=f"server-{i}")) for i in range(200)]
        self.store.write(config)
        result = self.client.post("/api/hosts", json=dict(host(), id="over-limit"), headers=headers)
        self.assertEqual(result.status_code, 400)
        for labels in ({f"component-{i}": "Label" for i in range(101)},
                       {"ram": "x" * 251}, {"ram": "bad\nlabel"}):
            with self.assertRaises(ValueError):
                validate_host(dict(host(), labels=labels))

    def test_ping_parsing_and_no_response(self):
        from monitor.proxmox import ping
        target = dict(host(), url="https://192.0.2.1:8006")
        with patch("monitor.ping.platform.system", return_value="Windows"):
            with patch("monitor.ping.subprocess.run", return_value=Mock(returncode=0, stdout="Reply: time=12.4ms TTL=54")) as run:
                self.assertEqual(ping(target), 12.4)
                self.assertEqual(run.call_args.args[0][-1], "192.0.2.1")
                self.assertEqual(run.call_args.args[0][1:5], ["-n", "1", "-w", "2000"])
                self.assertNotIn("shell", run.call_args.kwargs)
            with patch("monitor.ping.subprocess.run", return_value=Mock(returncode=1, stdout="")):
                self.assertIsNone(ping(target))


if __name__ == "__main__":
    unittest.main()
