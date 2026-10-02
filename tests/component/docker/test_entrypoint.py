"""Component tests for :mod:`docker.entrypoint` (real filesystem behaviour)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography import x509

import docker.entrypoint as entrypoint
from docker.entrypoint import (
    DATA_SUBDIRECTORIES,
    DEFAULT_ALLOWED_HOSTS,
    PERSISTED_TLS_DIRECTORY,
    SECRET_KEY_FILENAME,
    TLS_CERTIFICATE_FILENAME,
    TLS_PRIVATE_KEY_FILENAME,
    main,
    prepare_data_dir,
    prepare_environment,
    prepare_local_tls,
    resolve_secret_key,
)

pytestmark = pytest.mark.component


class TestPrepareDataDir:
    """Behaviour of :func:`prepare_data_dir` across mount states."""

    def test_creates_missing_directory_and_subdirectories(self, tmp_path: Path) -> None:
        """A missing writable parent gets /data and its subdirectories created."""
        data_dir = tmp_path / "data"
        assert prepare_data_dir(data_dir) is True
        for subdirectory in DATA_SUBDIRECTORIES:
            assert (data_dir / subdirectory).is_dir()

    def test_reuses_existing_directory(self, tmp_path: Path) -> None:
        """An already-mounted, already-populated /data is accepted as-is."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        assert prepare_data_dir(data_dir) is True

    def test_rejects_path_that_is_a_file(self, tmp_path: Path) -> None:
        """A misconfigured non-directory /data is a hard error, not ephemeral fallback."""
        data_dir = tmp_path / "data"
        data_dir.write_text("not a directory")
        with pytest.raises(RuntimeError):
            prepare_data_dir(data_dir)

    def test_unwritable_directory_falls_back_to_ephemeral(self, tmp_path: Path) -> None:
        """A read-only /data (root filesystem, no volume mounted) is ephemeral, not fatal."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            assert prepare_data_dir(data_dir) is False
        finally:
            data_dir.chmod(stat.S_IRWXU)  # restore so pytest can clean up tmp_path


class TestResolveSecretKey:
    """Behaviour of :func:`resolve_secret_key` for persisted and ephemeral runs."""

    def test_generates_and_persists_a_new_key(self, tmp_path: Path) -> None:
        """A fresh /data gets a newly generated, mode-0600 secret key file."""
        key = resolve_secret_key(tmp_path)
        key_path = tmp_path / SECRET_KEY_FILENAME
        assert key_path.read_text(encoding="utf-8") == key
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    def test_reuses_persisted_key_on_next_start(self, tmp_path: Path) -> None:
        """A previously persisted key is reused rather than regenerated."""
        first_key = resolve_secret_key(tmp_path)
        second_key = resolve_secret_key(tmp_path)
        assert first_key == second_key

    def test_ephemeral_mode_never_touches_disk(self, tmp_path: Path) -> None:
        """No data_dir means a fresh in-memory-only key with nothing persisted."""
        key_one = resolve_secret_key(None)
        key_two = resolve_secret_key(None)
        assert key_one != key_two
        assert list(tmp_path.iterdir()) == []


class TestPrepareEnvironment:
    """Behaviour of :func:`prepare_environment`."""

    def test_persistent_mode_sets_secret_key_hosts_and_session_path(self, tmp_path: Path) -> None:
        """A writable data root fills in the secret key, hosts, and session path."""
        environ: dict[str, str] = {}
        prepare_environment(environ, data_dir_root=tmp_path / "data")
        assert environ["DJANGO_SECRET_KEY"]
        assert environ["DJANGO_ALLOWED_HOSTS"] == DEFAULT_ALLOWED_HOSTS
        assert environ["CONFORMANCE_DATA_DIR"] == str(tmp_path / "data")
        assert environ["DJANGO_SESSION_FILE_PATH"] == str(tmp_path / "data" / "sessions")

    def test_ephemeral_mode_skips_session_path(self, tmp_path: Path) -> None:
        """An unwritable data root still fills in a key and hosts but not a session path."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            environ: dict[str, str] = {}
            prepare_environment(environ, data_dir_root=data_dir)
        finally:
            data_dir.chmod(stat.S_IRWXU)
        assert environ["DJANGO_SECRET_KEY"]
        assert environ["DJANGO_ALLOWED_HOSTS"] == DEFAULT_ALLOWED_HOSTS
        assert "CONFORMANCE_DATA_DIR" not in environ
        assert "DJANGO_SESSION_FILE_PATH" not in environ

    def test_never_overrides_operator_supplied_values(self, tmp_path: Path) -> None:
        """Explicit operator environment variables are never clobbered."""
        environ = {
            "DJANGO_SECRET_KEY": "operator-supplied-key",  # pragma: allowlist secret
            "DJANGO_ALLOWED_HOSTS": "example.internal",
            "DJANGO_SESSION_FILE_PATH": "/custom/sessions",
        }
        prepare_environment(environ, data_dir_root=tmp_path / "data")
        assert environ["DJANGO_SECRET_KEY"] == "operator-supplied-key"  # noqa: S105 - dict key name, not a credential.  # pragma: allowlist secret
        assert environ["DJANGO_ALLOWED_HOSTS"] == "example.internal"
        assert environ["DJANGO_SESSION_FILE_PATH"] == "/custom/sessions"

    def test_honours_data_dir_env_override(self, tmp_path: Path) -> None:
        """CONFORMANCE_DATA_DIR overrides the default /data root, e.g. for tests."""
        custom_dir = tmp_path / "custom-data"
        environ = {"CONFORMANCE_DATA_DIR": str(custom_dir)}
        prepare_environment(environ, data_dir_root=tmp_path / "unused")
        assert custom_dir.is_dir()
        assert environ["DJANGO_SESSION_FILE_PATH"] == str(custom_dir / "sessions")


class TestPrepareLocalTls:
    """Behaviour of generated local HTTPS material."""

    def test_generates_persistent_certificate_for_supported_hosts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Generated material covers localhost and the legacy callback host."""
        runtime_directory = tmp_path / "runtime-tls"
        monkeypatch.setattr(entrypoint, "RUNTIME_TLS_DIRECTORY", runtime_directory)
        monkeypatch.setattr(entrypoint, "TLS_CERTIFICATE_PATH", runtime_directory / TLS_CERTIFICATE_FILENAME)
        monkeypatch.setattr(entrypoint, "TLS_PRIVATE_KEY_PATH", runtime_directory / TLS_PRIVATE_KEY_FILENAME)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        prepare_local_tls(data_dir)

        persisted_directory = data_dir / PERSISTED_TLS_DIRECTORY
        certificate = x509.load_pem_x509_certificate((persisted_directory / TLS_CERTIFICATE_FILENAME).read_bytes())
        subject_alternative_names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert "localhost" in subject_alternative_names.get_values_for_type(x509.DNSName)
        assert {str(address) for address in subject_alternative_names.get_values_for_type(x509.IPAddress)} == {
            "0.0.0.0",  # noqa: S104 - expected certificate SAN, not a bind target.
            "127.0.0.1",
            "::1",
        }
        assert (runtime_directory / TLS_CERTIFICATE_FILENAME).is_file()
        assert stat.S_IMODE((runtime_directory / TLS_PRIVATE_KEY_FILENAME).stat().st_mode) == 0o600

    def test_reuses_valid_persistent_certificate(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A named data volume keeps a stable browser certificate across restarts."""
        runtime_directory = tmp_path / "runtime-tls"
        monkeypatch.setattr(entrypoint, "RUNTIME_TLS_DIRECTORY", runtime_directory)
        monkeypatch.setattr(entrypoint, "TLS_CERTIFICATE_PATH", runtime_directory / TLS_CERTIFICATE_FILENAME)
        monkeypatch.setattr(entrypoint, "TLS_PRIVATE_KEY_PATH", runtime_directory / TLS_PRIVATE_KEY_FILENAME)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        prepare_local_tls(data_dir)
        certificate_path = data_dir / PERSISTED_TLS_DIRECTORY / TLS_CERTIFICATE_FILENAME
        first_certificate = certificate_path.read_bytes()
        prepare_local_tls(data_dir)

        assert certificate_path.read_bytes() == first_certificate


class TestMain:
    """Behaviour of the entrypoint's ``main`` function."""

    def test_no_command_is_an_error(self) -> None:
        """Calling the entrypoint with no command to exec into fails cleanly."""
        assert main([]) == 1

    def test_execs_into_the_supplied_command(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The resolved command and arguments are handed to os.execvp verbatim."""
        recorded: dict[str, object] = {}

        def _fake_execvp(file: str, args: list[str]) -> None:
            recorded["file"] = file
            recorded["args"] = args

        monkeypatch.setattr(os, "execvp", _fake_execvp)
        monkeypatch.setenv("CONFORMANCE_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)

        main(["uvicorn", "config.asgi:application"])

        assert recorded["file"] == "uvicorn"
        assert recorded["args"] == ["uvicorn", "config.asgi:application"]
        assert os.environ["DJANGO_SECRET_KEY"]

    def test_rejects_incomplete_local_tls_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A command must not request only one half of the generated TLS pair."""
        monkeypatch.setenv("CONFORMANCE_DATA_DIR", str(tmp_path / "data"))

        with pytest.raises(RuntimeError, match="certificate and private key"):
            main(["uvicorn", "--ssl-certfile", str(entrypoint.TLS_CERTIFICATE_PATH)])
