"""Own certificate for the HTTPS port (#76).

OpenAmpere runs in home networks without a domain, so no public certificate authority can sign for it. It creates
a self-signed certificate once and keeps it next to the database. Apps such as the Home Assistant integration pin
its SHA-256 fingerprint (it is part of the connection code): a different certificate, e.g. from someone listening
in the network, is refused. Host names are therefore not checked and the certificate does not need any.
"""

from __future__ import annotations

import datetime
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CERT_FILE = "tls-cert.pem"
KEY_FILE = "tls-key.pem"
VALID_YEARS = 20


@dataclass
class Certificate:
    cert_path: Path
    key_path: Path
    fingerprint: str  # SHA-256 of the DER certificate, hex


def fingerprint(cert_pem: bytes) -> str:
    der = x509.load_pem_x509_certificate(cert_pem).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def ensure_certificate(folder: str | Path) -> Certificate:
    """The certificate in folder; created on the first call."""
    folder = Path(folder)
    cert_path, key_path = folder / CERT_FILE, folder / KEY_FILE
    if not (cert_path.is_file() and key_path.is_file()):
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpenAmpere")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=365 * VALID_YEARS))
                .sign(key, hashes.SHA256()))
        folder.mkdir(parents=True, exist_ok=True)
        key_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption())
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # private key: owner only
        with os.fdopen(fd, "wb") as f:
            f.write(key_bytes)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return Certificate(cert_path, key_path, fingerprint(cert_path.read_bytes()))
