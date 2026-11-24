"""Real process signals stop CLI work and release the loopback callback port.

Subprocess execution is justified because OS signal delivery and process exit
are under test. HTTP is mocked at the transport boundary; the only live socket
is the CLI's ephemeral loopback HTTPS callback listener.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.run_config import RUN_READY_SECURITY_ENVIRONMENT

pytestmark = pytest.mark.component

_PROCESS = """
import contextlib
import json
import os
import sys
import time
from pathlib import Path
import httpx
import main
from conformance import cli
from conformance.cli_callback import psu_callback_listener
from conformance.executor import run_manifest
from conformance.manifest import Manifest, ManifestRequest, ManifestStep
from tests.support.executor_psu import psu_manual_step

directory, phase = sys.argv[1:]
os.chdir(directory)
port = 0

def wait(seconds=60):
    print(json.dumps({"ready": True, "port": port}), flush=True)
    time.sleep(seconds)

if phase == "loading":
    main._main = lambda argv: wait()
elif phase == "preparation":
    cli.prepare_test_plan_for_run = lambda *args, **kwargs: wait()
else:
    if phase == "listener":
        import ssl
        def interrupted_wrap(context, sock, **kwargs):
            global port
            port = sock.getsockname()[1]
            wait()
        ssl.SSLContext.wrap_socket = interrupted_wrap

    @contextlib.contextmanager
    def listener(config, plan, *, listen):
        global port
        with psu_callback_listener(config.oauth.redirect_uri,
             session_store=cli.auth_session_store, listen="127.0.0.1:0") as address:
            port = address[1]
            yield
    cli._callback_listener_for = listener

    def handler(request):
        with Path("http-requests").open("a") as record:
            record.write("request\\n")
        wait(0.5)
        Path("request-finished").write_text("finished")
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    original_close = client.close
    def close():
        if phase == "http":
            assert Path("request-finished").exists(), "Client closed before the worker finished"
        original_close()
        Path("client-closed").write_text("closed")
    client.close = close
    cli.build_json_http_client = lambda **kwargs: client

    def execute(plan, **kwargs):
        if phase == "http":
            step = ManifestStep(id="waiting", name="HTTP request",
                request=ManifestRequest(method="GET", url="https://mock.example.com"), assertions=())
        else:
            step = psu_manual_step()
            store = kwargs["auth_session_store"]
            original_get = store.get
            def get(*args, **kw):
                print(json.dumps({"ready": True, "port": port}), flush=True)
                store.get = original_get
                return original_get(*args, **kw)
            store.get = get
        next_step = ManifestStep(id="must-not-run", name="After cancellation",
            request=ManifestRequest(method="GET", url="https://mock.example.com/next"), assertions=())
        return run_manifest(Manifest(schema_version="v1", name="Signal fixture", steps=(step, next_step)),
            client=kwargs["client"], execution_logger=kwargs["execution_logger"],
            auth_session_store=kwargs["auth_session_store"], run_id=kwargs["run_id"])
    cli.run_compiled_test_plan = execute

raise SystemExit(main.main(["--test-plan", "plan.json"]))
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT/SIGTERM process delivery is under test")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("phase", ["loading", "preparation", "listener", "http", "psu"])
def test_signal_cancels_cli_and_preserves_previous_evidence(tmp_path: Path, sig: signal.Signals, phase: str) -> None:
    plan = {
        "schemaVersion": "1.0",
        "specification": {
            "family": "OBL_READ_WRITE",
            "version": "3.1.11",
            "openApiDocumentUpdate": "Release-5",
            "profile": "FAPI1_ADVANCED",
        },
        "executionMode": "development",
        "securityEnvironment": {
            **RUN_READY_SECURITY_ENVIRONMENT,
            "discoveryUrl": "https://mock.example.com/.well-known/openid-configuration",
            "resourceBaseUrl": "https://mock.example.com",
        },
        "resourceGroups": [{"id": "AIS", "endpoints": [{"method": "GET", "path": "/open-banking/v3.1/aisp/accounts"}]}],
        "businessTestData": {},
        "metadata": {},
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    result = output / "test-results.json"
    log = output / "execution-log.ndjson"
    result.write_text('{"previous": true}\n', encoding="utf-8")
    log.write_text('{"previous": true}\n', encoding="utf-8")

    with subprocess.Popen(  # noqa: S603 - fixed local Python executable, trusted offline harness, no shell.
        [sys.executable, "-c", _PROCESS, str(tmp_path), phase],
        cwd=Path(__file__).parents[3],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=10), "CLI never reached the cancellation fixture"
            ready = json.loads(process.stdout.readline())
            assert ready["ready"]
            process.send_signal(sig)
            stdout, stderr = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    assert process.returncode == 128 + sig
    assert stderr.count("Cancelled") == 1
    assert "Traceback" not in stderr
    assert "Conformance run" not in stdout
    assert "\033" not in stderr
    assert result.read_text() == '{"previous": true}\n'
    assert log.read_text() == '{"previous": true}\n'
    assert sorted(path.name for path in output.iterdir()) == ["execution-log.ndjson", "test-results.json"]
    if phase in ("http", "psu"):
        assert (tmp_path / "client-closed").read_text() == "closed"
        assert 'Step "' in stderr
    if phase == "psu":
        assert "Open this URL to authorise:" in stderr
        assert not (tmp_path / "http-requests").exists()
    if phase == "http":
        assert (tmp_path / "http-requests").read_text() == "request\n"
    if phase in ("listener", "http", "psu"):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", ready["port"]))
