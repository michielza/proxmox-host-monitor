"""Trust lifecycle tests use generated certificates and a loopback TLS server."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
from pathlib import Path
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from itsdangerous import TimestampSigner

from monitor.certificates import CertificateTrust, inspect_peer, parse_pem
from monitor.config import Store
from monitor.proxmox import APIError, get
import test_monitor
from test_monitor import host


def certificate(ca=False, expired=False, address="127.0.0.1", issuer=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA" if ca else "Test node")])
    now = datetime.now(timezone.utc)
    issuer_name = x509.load_pem_x509_certificate(issuer[0].encode()).subject if issuer else name
    signer = serialization.load_pem_private_key(issuer[1], password=None) if issuer else key
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer_name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=2))
            .not_valid_after(now + timedelta(days=-1 if expired else 2))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                        key_encipherment=not ca, data_encipherment=False,
                                        key_agreement=False, key_cert_sign=ca, crl_sign=ca,
                                        encipher_only=None, decipher_only=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]), critical=False)
            .sign(signer, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))


@contextmanager
def tls_server(pem, key):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                pass  # Metadata-only inspection deliberately closes before HTTP.
        def do_GET(self):
            requests.append(dict(self.headers))
            payload = b'{"data": {"cpu": 0.1}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    with tempfile.TemporaryDirectory() as directory:
        cert_path, key_path = Path(directory) / "node.pem", Path(directory) / "node.key"
        cert_path.write_text(pem, encoding="ascii"); key_path.write_bytes(key)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}", str(cert_path), requests
        finally:
            server.shutdown(); server.server_close(); thread.join(5)


class TLSVerificationTests(unittest.TestCase):
    def test_uploaded_ca_verifies_its_issued_node_certificate(self):
        ca = certificate(ca=True)
        pem, key = certificate(issuer=ca)
        with tls_server(pem, key) as (url, _, requests):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ca.pem"
                path.write_text(parse_pem(ca[0], ca_only=True), encoding="ascii")
                self.assertEqual(get(dict(host(), url=url, ca_file=str(path)), "/nodes/pve-01/status"), {"cpu": .1})
                self.assertEqual(len(requests), 1)

    def test_inspection_sends_no_http_or_credentials_and_leaf_trust_verifies(self):
        pem, key = certificate()
        with tls_server(pem, key) as (url, path, requests):
            inspected, details = inspect_peer(url)
            self.assertEqual(inspected, pem)
            self.assertTrue(details["valid_now"])
            self.assertEqual(requests, [])
            target = dict(host(), url=url)
            with self.assertRaises(APIError) as failure:
                get(target, "/nodes/pve-01/status")
            self.assertEqual(failure.exception.kind, "tls_error")
            self.assertEqual(requests, [])
            self.assertEqual(get(dict(target, ca_file=path), "/nodes/pve-01/status"), {"cpu": .1})
            self.assertIn("Authorization", requests[0])

    def test_expired_wrong_identity_and_changed_certificate_block_credentials(self):
        for options in ({"expired": True}, {"address": "127.0.0.2"}, {}):
            pem, key = certificate(**options)
            with tls_server(pem, key) as (url, path, requests):
                if not options:
                    Path(path).write_text(certificate()[0], encoding="ascii")
                with self.assertRaises(APIError):
                    get(dict(host(), url=url, ca_file=path), "/nodes/pve-01/status")
                self.assertEqual(requests, [])


class CertificateRouteTests(unittest.TestCase):
    setUp = test_monitor.MonitorTests.setUp
    tearDown = test_monitor.MonitorTests.tearDown
    login = test_monitor.MonitorTests.login
    def test_inspection_requires_auth_and_csrf(self):
        endpoint = "/api/connection/certificate"
        self.assertEqual(self.client.post(endpoint, json={"url": host()["url"]}).status_code, 401)
        self.login()
        self.assertEqual(self.client.post(endpoint, json={"url": host()["url"]}).status_code, 403)

    def approval(self, headers, pem):
        with patch("monitor.certificates.inspect_peer", return_value=(pem, {"fingerprint": "TEST"})):
            result = self.client.post("/api/connection/certificate", json={"url": host()["url"]}, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json["verified"])
        return dict(host(), trust_mode="certificate", certificate_ticket=result.json["ticket"], trust_confirmed=True)

    def test_approval_bound_to_url_session_expiry_and_confirmation(self):
        headers = self.login()
        data = self.approval(headers, certificate()[0])
        for change in ({"trust_confirmed": False}, {"url": "https://other.example.net"}, {"certificate_ticket": "tampered"}):
            with patch("monitor.app.collect") as collect:
                result = self.client.post("/api/hosts", json=dict(data, **change), headers=headers)
                self.assertEqual(result.status_code, 400)
                collect.assert_not_called()
        with patch.object(TimestampSigner, "get_timestamp", return_value=1):
            trust = CertificateTrust(self.store, "test-signing-secret")
            ticket = trust.signer.dumps({"url": host()["url"], "csrf": headers["X-CSRF-Token"], "pem": certificate()[0]})
        expired = dict(data, certificate_ticket=ticket)
        self.assertEqual(self.client.post("/api/hosts", json=expired, headers=headers).status_code, 400)
        headers = self.login()
        self.assertEqual(self.client.post("/api/hosts", json=data, headers=headers).status_code, 400)

    def test_test_is_stateless_save_persists_and_offline_labels_retain_trust(self):
        headers = self.login()
        data = self.approval(headers, certificate()[0])
        original = self.path.read_bytes()
        def verified(connection):
            self.assertTrue(Path(connection["ca_file"]).exists())
            return {"errors": {}}
        with patch("monitor.app.collect", side_effect=verified):
            result = self.client.post("/api/connection/test", json=data, headers=headers)
            self.assertTrue(result.json["ok"])
            self.assertEqual(self.path.read_bytes(), original)
            self.assertFalse((self.path.parent / "certificates").exists())
            result = self.client.post("/api/hosts", json=data, headers=headers)
            self.assertEqual(result.status_code, 200)
        saved = self.store.load()["hosts"][0]
        path = Path(saved["ca_file"])
        self.assertTrue(path.exists())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.path.parent.glob(".certificate-test-*")), [])
        with patch("monitor.app.collect", side_effect=AssertionError("Offline edit must not connect")):
            edited = dict(host(), token_secret="", labels={"ram": "ECC"}, ca_file="/untrusted/client-path")
            result = self.client.post("/api/hosts", json=edited, headers=headers)
            self.assertEqual(result.status_code, 200)
        self.assertEqual(self.store.load()["hosts"][0]["ca_file"], str(path))

    def test_failures_preserve_saved_config_and_remove_temporary_trust(self):
        headers = self.login()
        data = self.approval(headers, certificate()[0])
        original = self.path.read_bytes()
        with patch("monitor.app.collect", side_effect=APIError("TLS failed", "tls_error")):
            self.assertFalse(self.client.post("/api/connection/test", json=data, headers=headers).json["ok"])
            self.assertEqual(self.client.post("/api/hosts", json=data, headers=headers).status_code, 502)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".certificate-test-*")), [])
        self.assertEqual(self.client.get("/api/session").status_code, 200)

    def test_new_host_requires_verified_all_endpoints_and_concurrent_edit_is_preserved(self):
        headers = self.login()
        data = dict(host(), id="new-server", ca_file="/client-chosen.pem")
        with patch("monitor.app.collect", return_value={"errors": {"storage": "Access denied"}}):
            self.assertEqual(self.client.post("/api/hosts", json=data, headers=headers).status_code, 502)
        def concurrent(connection):
            self.assertEqual(connection["ca_file"], "")
            with self.store.transaction():
                current = self.store.load()
                current["hosts"][0]["labels"] = {"ram": "Changed concurrently"}
                self.store.write(current)
            return {"errors": {}}
        with patch("monitor.app.collect", side_effect=concurrent):
            changed = dict(host(), token_secret="new-token", trust_mode="system")
            self.assertEqual(self.client.post("/api/hosts", json=changed, headers=headers).status_code, 409)
        self.assertEqual(self.store.load()["hosts"][0]["labels"], {"ram": "Changed concurrently"})

    def test_ca_validation_and_private_key_rejection(self):
        ca, key = certificate(ca=True)
        self.assertEqual(parse_pem(ca, ca_only=True), ca)
        for value in ("invalid", certificate()[0], certificate(ca=True, expired=True)[0], ca + key.decode()):
            with self.assertRaises(ValueError):
                parse_pem(value, ca_only=True)

    def test_ca_upload_and_public_trust_new_host_save(self):
        headers = self.login()
        for mode in ("system", "ca"):
            data = dict(host(), id="new-" + mode, trust_mode=mode)
            if mode == "ca":
                data["ca_pem"] = certificate(ca=True)[0]
            with patch("monitor.app.collect", return_value={"errors": {}}) as collector:
                result = self.client.post("/api/hosts", json=data, headers=headers)
                self.assertEqual(result.status_code, 200)
                collector.assert_called_once()
            saved = next(h for h in self.store.load()["hosts"] if h["id"] == data["id"])
            self.assertEqual(bool(saved["ca_file"]), mode == "ca")


if __name__ == "__main__":
    unittest.main()
