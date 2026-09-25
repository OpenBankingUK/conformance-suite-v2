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
4. ``exec`` into the requested command (the image's default ``CMD``, or
   whatever command the operator overrides it with, e.g. the existing
   headless CLI) so signals and exit codes reach it directly.

This module intentionally avoids any dependency on a shell: the runtime image
does not have one.
"""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import MutableMapping
from pathlib import Path

DEFAULT_DATA_DIR = Path("/data")
DATA_SUBDIRECTORIES = ("results", "logs", "sessions")
SECRET_KEY_FILENAME = "django-secret-key"  # noqa: S105 - a filename constant, not a credential value.  # pragma: allowlist secret
SECRET_KEY_BYTES = 64

DATA_DIR_ENV = "CONFORMANCE_DATA_DIR"
DJANGO_SECRET_KEY_ENV = "DJANGO_SECRET_KEY"  # noqa: S105 - the env var *name*, not a credential value.  # pragma: allowlist secret
DJANGO_ALLOWED_HOSTS_ENV = "DJANGO_ALLOWED_HOSTS"
DJANGO_SESSION_FILE_PATH_ENV = "DJANGO_SESSION_FILE_PATH"

DEFAULT_ALLOWED_HOSTS = "127.0.0.1,localhost"
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


def prepare_environment(environ: MutableMapping[str, str], *, data_dir_root: Path = DEFAULT_DATA_DIR) -> None:
    """Populate the process environment with resolved runtime configuration.

    Mutates ``environ`` in place so an ``exec``-replaced child process
    inherits the result. Never overrides a variable the operator already set.

    Args:
        environ: The environment mapping to update (``os.environ`` in
            production; injectable for tests).
        data_dir_root: Persistent data root to prepare, overridable via
            ``CONFORMANCE_DATA_DIR`` for tests and non-default layouts.
    """
    data_dir_candidate = Path(environ.get(DATA_DIR_ENV, str(data_dir_root)))
    data_dir = data_dir_candidate if prepare_data_dir(data_dir_candidate) else None

    if DJANGO_SECRET_KEY_ENV not in environ:
        environ[DJANGO_SECRET_KEY_ENV] = resolve_secret_key(data_dir)

    if DJANGO_ALLOWED_HOSTS_ENV not in environ:
        environ[DJANGO_ALLOWED_HOSTS_ENV] = DEFAULT_ALLOWED_HOSTS

    if data_dir is not None and DJANGO_SESSION_FILE_PATH_ENV not in environ:
        environ[DJANGO_SESSION_FILE_PATH_ENV] = str(data_dir / "sessions")


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

    prepare_environment(os.environ)
    os.execvp(command[0], command)  # noqa: S606 - trusted, image-supplied CMD/operator override, not user input.


if __name__ == "__main__":
    sys.exit(main())
