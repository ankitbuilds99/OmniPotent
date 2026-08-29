"""Follow-ups to the Kubernetes connector's TLS trust fix.

Three things the first pass left behind: a ``KUBE_CA_BUNDLE`` typo threw away a
valid mounted cluster CA instead of falling back to it, ``ConnectorConfig``
still declared ``verify_ssl`` as a plain ``bool`` while the connector fed it a CA
path (which mypy -- a required CI step -- rejected), and the one escape hatch out
of the now-deliberate TLS failure was documented nowhere an operator would look.
"""

from __future__ import annotations

import datetime
import logging
import ssl
import typing
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.connectors import kubernetes as k8s_module
from app.connectors.base import ConnectorConfig
from app.connectors.kubernetes import KubernetesConnector

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_ca(path: Path, common_name: str) -> str:
    """Write a self-signed CA that no public trust store contains."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(path)


def _ssl_context_of(connector: KubernetesConnector) -> ssl.SSLContext:
    return connector.client._transport._pool._ssl_context


def _subject_common_names(context: ssl.SSLContext) -> set[str]:
    return {
        value
        for cert in context.get_ca_certs()
        for rdn in cert["subject"]
        for key, value in rdn
        if key == "commonName"
    }


@pytest.mark.asyncio
async def test_broken_override_falls_back_to_the_mounted_cluster_ca(
    tmp_path, monkeypatch, caplog
):
    """A typo'd KUBE_CA_BUNDLE must not discard a CA bundle that is mounted and valid.

    The override branch used to fire on the variable merely being *set*, so a
    stale path meant an in-cluster pod verified the apiserver against public
    roots -- the exact handshake failure the connector was fixed for, re-armed by
    one wrong environment variable.
    """
    (tmp_path / "token").write_text("sa-token-value\n")
    mounted = _write_ca(tmp_path / "ca.crt", "omnipotent-remainder-mounted-ca")
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(tmp_path / "token"))
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_CA_PATH", mounted)
    monkeypatch.setenv("KUBE_CA_BUNDLE", str(tmp_path / "typo.crt"))

    with caplog.at_level(logging.WARNING, logger="omnipotent.connectors.kubernetes"):
        connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl == mounted
        assert "omnipotent-remainder-mounted-ca" in _subject_common_names(
            _ssl_context_of(connector)
        )
        # The operator still has to hear that their override was ignored, and why.
        assert "typo.crt" in caplog.text
        assert mounted in caplog.text
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_broken_override_without_a_mount_still_verifies_against_public_roots(
    tmp_path, monkeypatch, caplog
):
    """With nothing to fall back to, fail loudly rather than skip verification."""
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(tmp_path / "no-token"))
    monkeypatch.setenv("KUBE_CA_BUNDLE", str(tmp_path / "typo.crt"))

    with caplog.at_level(logging.WARNING, logger="omnipotent.connectors.kubernetes"):
        connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl is True
        context = _ssl_context_of(connector)
        assert context.verify_mode is ssl.CERT_REQUIRED
        assert "no cluster CA bundle" in caplog.text
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_valid_override_still_wins_over_the_mounted_bundle(tmp_path, monkeypatch):
    """Falling back on a broken override must not weaken a working one."""
    (tmp_path / "token").write_text("sa-token-value\n")
    mounted = _write_ca(tmp_path / "ca.crt", "omnipotent-remainder-mounted-ca")
    override = _write_ca(tmp_path / "override.crt", "omnipotent-remainder-override-ca")
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(tmp_path / "token"))
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_CA_PATH", mounted)
    monkeypatch.setenv("KUBE_CA_BUNDLE", override)

    connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl == override
        names = _subject_common_names(_ssl_context_of(connector))
        assert "omnipotent-remainder-override-ca" in names
        assert "omnipotent-remainder-mounted-ca" not in names
    finally:
        await connector.close()


def test_connector_config_declares_the_ca_path_it_actually_carries():
    """``uv run mypy app`` is a required CI step; a ``bool``-only field fails it.

    Nothing validates the dataclass at runtime, so this is not about behaviour --
    it is about the annotation staying honest about the CA-bundle path the
    Kubernetes connector legitimately puts in this field.
    """
    hints = typing.get_type_hints(ConnectorConfig)
    assert hints["verify_ssl"] == bool | str


def test_kube_ca_bundle_is_documented_for_operators():
    """The sole escape hatch from the deliberate TLS failure has to be discoverable."""
    env_example = (REPO_ROOT / ".env.example").read_text()
    assert "KUBE_CA_BUNDLE=" in env_example
    assert "KUBE_TOKEN=" in env_example
