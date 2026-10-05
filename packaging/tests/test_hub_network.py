import base64
from types import SimpleNamespace

import pytest
import ssl
import hub_network


# A self-signed EC test CA ("Infernux Hub test CA") in DER form. Public
# certificate material only; no private key is stored here.
_TEST_CA_DER_BASE64 = (
    "MIIBlTCCATugAwIBAgIUaWUUXVFcCQeaNXiWlizcRAoXUrQwCgYIKoZIzj0EAwIwHzEdMBsGA1UE"
    "AwwUSW5mZXJudXggSHViIHRlc3QgQ0EwIBcNMjYxMDA1MDYxNDE2WhgPMjEyNjA5MTEwNjE0MTZa"
    "MB8xHTAbBgNVBAMMFEluZmVybnV4IEh1YiB0ZXN0IENBMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcD"
    "QgAEyxo6gGSyiSDVXpNBiuxrJZ81w6kk6lfbYMHENYlOgBGPnWVJwMWx5kJuVP5++8+PnYFTisae"
    "Q87kU4TwyAjGb6NTMFEwHQYDVR0OBBYEFAu5eVYBGxMDQdc/GnctA0AFjsSrMB8GA1UdIwQYMBaA"
    "FAu5eVYBGxMDQdc/GnctA0AFjsSrMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDSAAwRQIg"
    "PGKydcYSBx2fsHcH0m5CsWycyR5NtLCDCtKvheIi7DwCIQDMUGRcycNfKGBbqKth6Amk/sVEcry0"
    "J0cN1JUu6J7Bkg=="
)


def _test_ca_der() -> bytes:
    return base64.b64decode(_TEST_CA_DER_BASE64)


def _pem_certificate() -> bytes:
    return (
        b"-----BEGIN CERTIFICATE-----\n"
        b"MIIBkTCB+wIJAJ7q\n"
        b"-----END CERTIFICATE-----\n"
    )


@pytest.fixture
def linux_without_build_machine_certificates(monkeypatch):
    monkeypatch.setattr(hub_network.sys, "platform", "linux")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(hub_network.ssl, "get_default_verify_paths", lambda: SimpleNamespace(cafile=None, capath=None))


def test_portable_linux_hub_uses_system_certificates(monkeypatch, linux_without_build_machine_certificates):
    bundle = "/etc/ssl/certs/ca-certificates.crt"
    monkeypatch.setattr(hub_network.Path, "is_file", lambda path: path.as_posix() == bundle)
    hub_network.configure_system_certificates()
    assert hub_network.Path(hub_network.os.environ["SSL_CERT_FILE"]).as_posix() == bundle


@pytest.mark.parametrize("setting", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_explicit_trust_configuration_is_preserved(monkeypatch, linux_without_build_machine_certificates, setting):
    monkeypatch.setenv(setting, "/company/trust")
    hub_network.configure_system_certificates()
    assert hub_network.os.environ[setting] == "/company/trust"


def test_missing_trust_store_remains_an_error(monkeypatch, caplog, linux_without_build_machine_certificates):
    monkeypatch.setattr(hub_network.Path, "is_file", lambda path: False)
    hub_network.configure_system_certificates()
    assert "SSL_CERT_FILE" not in hub_network.os.environ
    assert "ca-certificates" in caplog.text


def test_custom_download_ca_is_added_without_disabling_default_verification(
    monkeypatch, tmp_path
):
    loaded = []

    class _Context:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

        def load_verify_locations(self, *, cafile=None, cadata=None):
            loaded.append((cafile, cadata))

    context = _Context()
    monkeypatch.setattr(hub_network.ssl, "create_default_context", lambda: context)
    ca_file = tmp_path / "proxy-ca.pem"
    ca_file.write_bytes(_pem_certificate())

    result = hub_network.create_download_ssl_context(str(ca_file))

    assert result is context
    assert loaded == [(str(ca_file), None)]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_custom_download_ca_accepts_a_der_certificate(tmp_path):
    """A .cer/.crt export is usually DER, which cafile= cannot read."""
    ca_file = tmp_path / "proxy-ca.cer"
    ca_file.write_bytes(_test_ca_der())
    baseline = ssl.create_default_context().cert_store_stats()

    context = hub_network.create_download_ssl_context(str(ca_file))

    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509"] == baseline["x509"] + 1


def test_certificate_content_that_is_not_a_certificate_is_rejected(tmp_path):
    ca_file = tmp_path / "notes.txt"
    ca_file.write_bytes(b"this is not a certificate")

    with pytest.raises((OSError, ValueError)):
        hub_network.create_download_ssl_context(str(ca_file))


def test_empty_certificate_file_is_rejected(tmp_path):
    ca_file = tmp_path / "empty.pem"
    ca_file.write_bytes(b"")

    with pytest.raises((OSError, ValueError)):
        hub_network.create_download_ssl_context(str(ca_file))


def test_missing_certificate_file_is_reported(tmp_path):
    with pytest.raises(OSError):
        hub_network.create_download_ssl_context(str(tmp_path / "absent.pem"))
