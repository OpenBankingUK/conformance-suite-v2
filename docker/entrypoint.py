"""Container entrypoint: prepares runtime state, then execs the real command.

Runs as PID 1 inside the hardened runtime image (no shell, non-root UID/GID
65532). Responsibilities, in order:

1. Detect whether ``/data`` (or ``$CONFORMANCE_DATA_DIR``) is present and
   writable — true only when the operator mounted a bind mount or named
   volume there — and, if so, pre-create its ``results``, ``logs``, and
   ``sessions`` subdirectories.
2. Resolve the Django secret key: reuse a previously persisted key under
   ``/data``, generate and persist a new one, or (when ``/data`` is not
   writable) generate an ephemeral one that is not written anywhere. An
   ephemeral key only invalidates browser sessions on restart; it never
   affects certificates or exported results.
3. Fill in safe defaults for ``DJANGO_ALLOWED_HOSTS`` and
   ``DJANGO_SESSION_FILE_PATH`` when the operator has not set them.
4. Generate or reuse local-only TLS material when the default server command
   requests it, covering the legacy ``0.0.0.0`` callback host.
5. ``exec`` into the requested command (the image's default ``CMD``, or
   whatever command the operator overrides it with, e.g. the existing
   headless CLI) so signals and exit codes reach it directly.

This module intentionally avoids any dependency on a shell: the runtime image
does not have one.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sys
from collections.abc import MutableMapping
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_DATA_DIR = Path("/data")
DATA_SUBDIRECTORIES = ("results", "logs", "sessions")
SECRET_KEY_FILENAME = "django-secret-key"  # noqa: S105 - a filename constant, not a credential value.  # pragma: allowlist secret
SECRET_KEY_BYTES = 64
PERSISTED_TLS_DIRECTORY = "tls"
RUNTIME_TLS_DIRECTORY = Path(
    "/tmp/conformance-suite-tls"  # noqa: S108 - dedicated path inside the container's private tmpfs.
)
TLS_CERTIFICATE_FILENAME = "localhost-certificate.pem"
TLS_PRIVATE_KEY_FILENAME = "localhost-private-key.pem"  # noqa: S105 - a filename constant, not a credential value.  # pragma: allowlist secret
TLS_CERTIFICATE_PATH = RUNTIME_TLS_DIRECTORY / TLS_CERTIFICATE_FILENAME
TLS_PRIVATE_KEY_PATH = RUNTIME_TLS_DIRECTORY / TLS_PRIVATE_KEY_FILENAME
TLS_VALIDITY_DAYS = 365

DATA_DIR_ENV = "CONFORMANCE_DATA_DIR"
DJANGO_SECRET_KEY_ENV = "DJANGO_SECRET_KEY"  # noqa: S105 - the env var *name*, not a credential value.  # pragma: allowlist secret
DJANGO_ALLOWED_HOSTS_ENV = "DJANGO_ALLOWED_HOSTS"
DJANGO_SESSION_FILE_PATH_ENV = "DJANGO_SESSION_FILE_PATH"

DEFAULT_ALLOWED_HOSTS = "127.0.0.1,localhost,0.0.0.0"
"""Safe default matching the documented localhost-only publish profile."""


def prepare_data_dir(data_dir: Path) -> bool:
    """Ensure the persistent data directory and its subpaths exist.

    Args:
        data_dir: Candidate persistent data root (``/data`` by default).

    Returns:
        ``True`` if ``data_dir`` is present, a directory, and writable
        (persistent mode). ``False`` if it could not be created or written to
        — most likely because no bind mount or named volume was provided and
        the root filesystem is read-only (ephemeral mode).

    Raises:
        RuntimeError: If ``data_dir`` exists but is not a directory. That is
            a genuine misconfiguration, not a valid ephemeral run.
    """
    if data_dir.exists() and not data_dir.is_dir():
        raise RuntimeError(f"{data_dir} exists but is not a directory.")

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    if not os.access(data_dir, os.W_OK):
        return False

    try:
        for subdirectory in DATA_SUBDIRECTORIES:
            (data_dir / subdirectory).mkdir(exist_ok=True)
    except OSError:
        return False

    return True


def resolve_secret_key(data_dir: Path | None) -> str:
    """Resolve the Django secret key, persisting a new one when possible.

    Args:
        data_dir: Writable persistent data root, or ``None`` when running in
            ephemeral mode (no persistence available).

    Returns:
        The secret key to use for this run: read from a previously persisted
        file, freshly generated and persisted, or freshly generated and kept
        only in memory when ``data_dir`` is ``None``.
    """
    if data_dir is None:
        return secrets.token_urlsafe(SECRET_KEY_BYTES)

    key_path = data_dir / SECRET_KEY_FILENAME
    try:
        existing_key = key_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing_key = ""

    if existing_key:
        return existing_key

    return _write_new_secret_key(key_path)


def _write_new_secret_key(key_path: Path) -> str:
    """Atomically create a new, mode-0600 secret key file.

    Args:
        key_path: Destination path for the persisted key.

    Returns:
        The newly generated secret key.
    """
    new_key = secrets.token_urlsafe(SECRET_KEY_BYTES)
    temporary_path = key_path.with_name(f"{key_path.name}.tmp")
    file_descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(new_key)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, key_path)
    return new_key


def prepare_environment(
    environ: MutableMapping[str, str],
    *,
    data_dir_root: Path = DEFAULT_DATA_DIR,
) -> Path | None:
    """Populate the process environment with resolved runtime configuration.

    Mutates ``environ`` in place so an ``exec``-replaced child process
    inherits the result. Never overrides a variable the operator already set.

    Args:
        environ: The environment mapping to update (``os.environ`` in
            production; injectable for tests).
        data_dir_root: Persistent data root to prepare, overridable via
            ``CONFORMANCE_DATA_DIR`` for tests and non-default layouts.

    Returns:
        The writable data directory, or ``None`` in ephemeral mode.
    """
    data_dir_candidate = Path(environ.get(DATA_DIR_ENV, str(data_dir_root)))
    data_dir = data_dir_candidate if prepare_data_dir(data_dir_candidate) else None

    if DJANGO_SECRET_KEY_ENV not in environ:
        environ[DJANGO_SECRET_KEY_ENV] = resolve_secret_key(data_dir)

    if DJANGO_ALLOWED_HOSTS_ENV not in environ:
        environ[DJANGO_ALLOWED_HOSTS_ENV] = DEFAULT_ALLOWED_HOSTS

    if data_dir is not None:
        environ[DATA_DIR_ENV] = str(data_dir)
        if DJANGO_SESSION_FILE_PATH_ENV not in environ:
            environ[DJANGO_SESSION_FILE_PATH_ENV] = str(data_dir / "sessions")
    return data_dir


def prepare_local_tls(data_dir: Path | None) -> None:
    """Prepare a self-signed certificate for the local HTTPS listener.

    The certificate is persisted under ``/data`` when available so browsers
    see a stable identity across container restarts. Runtime copies always
    live under the writable ``/tmp`` mount required by the hardened profile.

    Args:
        data_dir: Writable persistent data root, or ``None`` in ephemeral mode.
    """
    source_directory = data_dir / PERSISTED_TLS_DIRECTORY if data_dir is not None else RUNTIME_TLS_DIRECTORY
    source_directory.mkdir(parents=True, exist_ok=True)
    certificate_path = source_directory / TLS_CERTIFICATE_FILENAME
    private_key_path = source_directory / TLS_PRIVATE_KEY_FILENAME

    if not _local_tls_material_is_valid(certificate_path, private_key_path):
        _generate_local_tls_material(certificate_path, private_key_path)

    if source_directory != RUNTIME_TLS_DIRECTORY:
        RUNTIME_TLS_DIRECTORY.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(certificate_path, TLS_CERTIFICATE_PATH)
        shutil.copyfile(private_key_path, TLS_PRIVATE_KEY_PATH)
        TLS_CERTIFICATE_PATH.chmod(0o644)
        TLS_PRIVATE_KEY_PATH.chmod(0o600)


def _local_tls_material_is_valid(certificate_path: Path, private_key_path: Path) -> bool:
    """Return whether persisted TLS material is valid and unexpired."""
    try:
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
        subject_alternative_names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        certificate_public_key = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        private_public_key = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except FileNotFoundError, OSError, ValueError, x509.ExtensionNotFound:
        return False
    required_ip_addresses = {
        ip_address("0.0.0.0"),  # noqa: S104 - certificate SAN, not a bind target.
        ip_address("127.0.0.1"),
        ip_address("::1"),
    }
    return (
        certificate.not_valid_before_utc <= datetime.now(UTC)
        and certificate.not_valid_after_utc > datetime.now(UTC) + timedelta(days=1)
        and certificate_public_key == private_public_key
        and "localhost" in subject_alternative_names.get_values_for_type(x509.DNSName)
        and required_ip_addresses.issubset(subject_alternative_names.get_values_for_type(x509.IPAddress))
    )


def _generate_local_tls_material(certificate_path: Path, private_key_path: Path) -> None:
    """Generate local-only TLS material covering the supported browser hosts."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Open Banking UK Conformance Suite Local")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=TLS_VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ip_address("127.0.0.1")),
                    x509.IPAddress(
                        ip_address("0.0.0.0")  # noqa: S104 - certificate SAN, not a bind target.
                    ),
                    x509.IPAddress(ip_address("::1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    _write_private_file(
        private_key_path,
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    _write_private_file(certificate_path, certificate.public_bytes(serialization.Encoding.PEM), mode=0o644)


def _write_private_file(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    """Atomically write generated TLS material with an explicit file mode."""
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    file_descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(content)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    os.chmod(temporary_path, mode)
    os.replace(temporary_path, path)


def main(argv: list[str] | None = None) -> int:
    """Prepare the runtime environment, then ``exec`` into the requested command.

    Args:
        argv: Command and arguments to exec into (the image's ``CMD``, or an
            operator override). Defaults to ``sys.argv[1:]``.

    Returns:
        Never returns on success — ``exec`` replaces this process. Returns
        ``1`` only if no command was supplied at all.
    """
    command = sys.argv[1:] if argv is None else argv
    if not command:
        sys.stderr.write("error: no command supplied to the container entrypoint.\n")
        return 1

    data_dir = prepare_environment(os.environ)
    command_uses_local_tls = str(TLS_CERTIFICATE_PATH) in command or str(TLS_PRIVATE_KEY_PATH) in command
    if command_uses_local_tls:
        if str(TLS_CERTIFICATE_PATH) not in command or str(TLS_PRIVATE_KEY_PATH) not in command:
            raise RuntimeError("The local TLS certificate and private key must be configured together.")
        prepare_local_tls(data_dir)
    os.execvp(command[0], command)  # noqa: S606 - trusted, image-supplied CMD/operator override, not user input.


if __name__ == "__main__":
    sys.exit(main())
