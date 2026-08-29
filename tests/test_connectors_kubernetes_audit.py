"""Regression tests for how the Kubernetes connector establishes TLS trust.

The connector used to set ``verify_ssl`` to ``os.path.exists(ca.crt)`` -- the
boolean ``True`` -- which tells httpx to trust the certifi *public* roots. The
kube-apiserver is signed by the cluster's own CA, so every in-cluster call died
in the handshake. These tests pin the fix at the level that actually matters:
the ssl.SSLContext the connector's HTTP client ends up using must contain the
cluster CA, and the out-of-cluster path must never disable verification while a
bearer token is in flight.
"""

from __future__ import annotations

import datetime
import logging
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.connectors import kubernetes as k8s_module
from app.connectors.kubernetes import KubernetesConnector

CA_COMMON_NAME = "omnipotent-audit-test-cluster-ca"


def _write_cluster_ca(path) -> str:
    """Write a self-signed CA that is deliberately absent from any public bundle."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
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
    """Reach the ssl context httpx built from ``config.verify_ssl``."""
    return connector.client._transport._pool._ssl_context


def _trusts_cluster_ca(context: ssl.SSLContext) -> bool:
    return any(
        CA_COMMON_NAME in dict(part for rdn in cert["subject"] for part in rdn).values()
        for cert in context.get_ca_certs()
    )


@pytest.fixture
def in_cluster(tmp_path, monkeypatch):
    """Shim the projected ServiceAccount volume into a temp dir."""
    token_path = tmp_path / "token"
    token_path.write_text("sa-token-value\n")
    ca_path = _write_cluster_ca(tmp_path / "ca.crt")
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(token_path))
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_CA_PATH", ca_path)
    monkeypatch.delenv("KUBE_CA_BUNDLE", raising=False)
    return ca_path


@pytest.mark.asyncio
async def test_in_cluster_client_trusts_the_cluster_ca(in_cluster):
    """The live httpx client must carry the cluster CA, not just the public roots."""
    connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl == in_cluster
        # verify=True would leave certifi's bundle in place, which is exactly the
        # bug: the private cluster CA would be nowhere in the trust store.
        assert connector.config.verify_ssl is not True
        assert _trusts_cluster_ca(_ssl_context_of(connector))
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_in_cluster_still_reads_the_service_account_token(in_cluster):
    connector = KubernetesConnector()
    try:
        assert connector.config.api_key == "sa-token-value"
        assert connector.config.url == "https://kubernetes.default.svc"
        assert connector.client.headers["Authorization"] == "Bearer sa-token-value"
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_kube_ca_bundle_overrides_the_mounted_bundle(in_cluster, tmp_path, monkeypatch):
    """An operator can correct a rotated cluster CA without rebuilding the image."""
    override = _write_cluster_ca(tmp_path / "override.crt")
    monkeypatch.setenv("KUBE_CA_BUNDLE", override)
    connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl == override
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_missing_cluster_ca_is_reported_and_verification_stays_on(
    tmp_path, monkeypatch, caplog
):
    """A token without its CA is a broken mount; say so instead of failing silently."""
    token_path = tmp_path / "token"
    token_path.write_text("sa-token-value")
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(token_path))
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_CA_PATH", str(tmp_path / "absent.crt"))
    monkeypatch.delenv("KUBE_CA_BUNDLE", raising=False)

    with caplog.at_level(logging.WARNING, logger="omnipotent.connectors.kubernetes"):
        connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl is True
        assert "cluster CA bundle" in caplog.text
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_unreadable_kube_ca_bundle_is_reported(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(tmp_path / "no-token"))
    monkeypatch.setenv("KUBE_CA_BUNDLE", str(tmp_path / "nope.crt"))

    with caplog.at_level(logging.WARNING, logger="omnipotent.connectors.kubernetes"):
        connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl is True
        assert "KUBE_CA_BUNDLE" in caplog.text
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_out_of_cluster_never_disables_verification(tmp_path, monkeypatch):
    """Sending a bearer token over an unverified channel leaks a cluster credential."""
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(tmp_path / "no-token"))
    monkeypatch.delenv("KUBE_CA_BUNDLE", raising=False)
    monkeypatch.setenv("KUBE_TOKEN", "operator-token")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")

    connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl is not False
        assert connector.config.url == "https://10.0.0.1:6443"
        context = _ssl_context_of(connector)
        assert context.verify_mode is ssl.CERT_REQUIRED
        assert context.check_hostname is True
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_out_of_cluster_uses_kube_ca_bundle_when_present(tmp_path, monkeypatch):
    ca_path = _write_cluster_ca(tmp_path / "private-ca.crt")
    monkeypatch.setattr(k8s_module, "SERVICEACCOUNT_TOKEN_PATH", str(tmp_path / "no-token"))
    monkeypatch.setenv("KUBE_CA_BUNDLE", ca_path)

    connector = KubernetesConnector()
    try:
        assert connector.config.verify_ssl == ca_path
        assert _trusts_cluster_ca(_ssl_context_of(connector))
    finally:
        await connector.close()
