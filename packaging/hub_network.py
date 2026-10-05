"""Use the Linux host's certificate store in portable Hub distributions."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import ssl
import sys


_PEM_HEADER = b"-----BEGIN "


def load_ca_certificate(context: ssl.SSLContext, ca_file: str) -> None:
    """Add the certificate file at ``ca_file`` to ``context`` as a trust root.

    ``SSLContext.load_verify_locations`` only reads PEM through ``cafile``. A
    ``.cer``/``.crt`` exported from the Windows certificate manager is usually
    DER, so DER bytes go through ``cadata`` instead, which accepts a
    concatenated chain as well. Both formats therefore work; a file that is
    neither raises the normal certificate error (``SSLError`` or ``ValueError``)
    rather than leaving the context silently without the extra trust root.
    """
    raw = Path(ca_file).read_bytes()
    if _PEM_HEADER in raw:
        context.load_verify_locations(cafile=str(ca_file))
    else:
        context.load_verify_locations(cadata=raw)


def create_download_ssl_context(extra_ca_file: str | None = None) -> ssl.SSLContext:
    """Create a verified context and optionally add a user CA to its trust roots."""
    context = ssl.create_default_context()
    if extra_ca_file:
        load_ca_certificate(context, str(Path(extra_ca_file).expanduser()))
    return context


def configure_system_certificates() -> None:
    if not sys.platform.startswith("linux"):
        return
    # Explicit administrator/proxy trust settings always take precedence.
    if "SSL_CERT_FILE" in os.environ or "SSL_CERT_DIR" in os.environ:
        return
    paths = ssl.get_default_verify_paths()
    if paths.cafile or paths.capath:
        return
    # Packaged OpenSSL can retain the build machine's conda prefix. Resolve
    # the distribution trust store instead; certificate verification stays on.
    for path in (
        Path("/etc/ssl/certs/ca-certificates.crt"),  # Debian / Ubuntu / Alpine
        Path("/etc/pki/tls/certs/ca-bundle.crt"),  # Fedora / RHEL
        Path("/etc/ssl/ca-bundle.pem"),  # openSUSE
        Path("/etc/ssl/cert.pem"),  # Arch
    ):
        if path.is_file():
            os.environ["SSL_CERT_FILE"] = str(path)
            logging.getLogger(__name__).info("Using system TLS certificates: %s", path)
            return
    logging.getLogger(__name__).error(
        "No system TLS certificate bundle found. Install your distribution's "
        "ca-certificates package or set SSL_CERT_FILE to your trusted CA bundle."
    )
