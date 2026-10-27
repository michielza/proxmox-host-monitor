"""Explicit certificate onboarding. Inspection never sends API credentials."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import socket
import ssl
import tempfile
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from itsdangerous import BadData, URLSafeTimedSerializer

from monitor.config import validate_origin


def certificate_details(cert):
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = san.get_values_for_type(x509.DNSName) + [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names = []
    now = datetime.now(timezone.utc)
    return {"subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
            "fingerprint": cert.fingerprint(hashes.SHA256()).hex(":").upper(),
            "not_before": cert.not_valid_before_utc.isoformat(),
            "not_after": cert.not_valid_after_utc.isoformat(), "names": names,
            "valid_now": cert.not_valid_before_utc <= now <= cert.not_valid_after_utc}


def inspect_peer(url):
    """Unverified TLS handshake for display ONLY: no HTTP and no token header.

    Nothing from this function becomes trusted until an authenticated operator
    verifies the fingerprint, explicitly approves, and verified API reads pass.
    """
    target = urlsplit(validate_origin(url))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((target.hostname, target.port or 443), timeout=5) as tcp:
            with context.wrap_socket(tcp, server_hostname=target.hostname) as tls:
                der = tls.getpeercert(binary_form=True)
        cert = x509.load_der_x509_certificate(der)
    except (OSError, ValueError):
        raise ValueError("Could not retrieve the server certificate. Check the API address and connectivity.") from None
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii"), certificate_details(cert)


def parse_pem(value, ca_only=False):
    if not isinstance(value, str) or not value or len(value) > 32768 or "PRIVATE KEY" in value:
        raise ValueError("Supply a PEM certificate file up to 32 KiB, without any private key.")
    try:
        certs = x509.load_pem_x509_certificates(value.encode("ascii"))
        if not 1 <= len(certs) <= 8:
            raise ValueError()
        for cert in certs:
            if not certificate_details(cert)["valid_now"]:
                raise ValueError()
            if ca_only and not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError()
    except (ValueError, UnicodeError, x509.ExtensionNotFound):
        raise ValueError("Invalid or expired certificate. CA uploads must contain valid CA certificates.") from None
    return "".join(cert.public_bytes(serialization.Encoding.PEM).decode("ascii") for cert in certs)


class CertificateTrust:
    def __init__(self, store, secret):
        self.store = store
        self.signer = URLSafeTimedSerializer(secret, salt="certificate-approval-v1")

    def inspection(self, url, csrf):
        url = validate_origin(url)
        pem, details = inspect_peer(url)
        ticket = self.signer.dumps({"url": url, "csrf": csrf, "pem": pem})
        return {"certificate": details, "ticket": ticket, "verified": False}

    def choice(self, data, host, previous, csrf):
        mode = data.get("trust_mode", "existing" if previous else "system")
        if mode == "existing":
            if not previous:
                raise ValueError("There is no saved certificate trust for this server.")
            return {"mode": mode, "path": previous.get("ca_file", ""), "pem": None}
        if mode == "system":
            return {"mode": mode, "path": "", "pem": None}
        if mode == "ca":
            pem = parse_pem(data.get("ca_pem"), ca_only=True)
            return {"mode": mode, "path": "", "pem": pem}
        if mode == "certificate":
            if data.get("trust_confirmed") is not True:
                raise ValueError("Verify the fingerprint and explicitly approve the certificate first.")
            try:
                payload = self.signer.loads(data.get("certificate_ticket", ""), max_age=600)
                if payload["url"] != host["url"] or payload["csrf"] != csrf:
                    raise ValueError()
                pem = parse_pem(payload["pem"])
            except (BadData, KeyError, TypeError, ValueError):
                raise ValueError("Certificate approval expired or belongs to another connection. Inspect it again.") from None
            return {"mode": mode, "path": "", "pem": pem}
        raise ValueError("Choose saved trust, public trust, a CA upload, or an approved node certificate.")

    @contextmanager
    def connection(self, host, choice):
        candidate = dict(host, ca_file=choice["path"])
        if not choice["pem"]:
            yield candidate
            return
        self.store.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, path = tempfile.mkstemp(prefix=".certificate-test-", dir=self.store.path.parent)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(choice["pem"])
            candidate["ca_file"] = path
            yield candidate
        finally:
            os.unlink(path)

    def persist(self, choice):
        if not choice["pem"]:
            return choice["path"]
        directory = self.store.path.parent / "certificates"
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / (hashlib.sha256(choice["pem"].encode("ascii")).hexdigest() + ".pem")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.read_text(encoding="ascii") != choice["pem"]:
                raise ValueError("Saved certificate content did not match its identifier.") from None
        else:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(choice["pem"])
                handle.flush()
                os.fsync(handle.fileno())
        return str(path)


def connection_changed(host, previous, choice):
    return (not previous or choice["pem"] is not None
            or choice["path"] != previous.get("ca_file", "")
            or any(host.get(key) != previous.get(key) for key in ("url", "node", "token_id", "token_secret")))
