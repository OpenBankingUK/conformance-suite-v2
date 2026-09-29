#!/usr/bin/env bash
# Hardened-profile smoke test for a candidate container image, run by
# candidate-image CI against each built platform digest before it can be
# scanned, promoted, or published. Exercises exactly the documented hardened
# profile: non-root UID/GID 65532, no shell, immutable root filesystem,
# health/home endpoints, secret-key persistence across restart, writable
# sessions/results/logs under a named volume, a read-only-but-readable
# /certs mount, and a working CMD override for the existing CLI.
#
# Usage: scripts/docker_smoke_test.sh <image-ref>
#   image-ref: a pullable image reference, e.g.
#              docker.io/openbanking/conformance-suite-v2-candidates@sha256:...
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <image-ref>" >&2
  exit 2
fi

IMAGE_REF="$1"
VOLUME_NAME="smoke-test-data-$$"
CERTS_DIR="$(mktemp -d)"
CONTAINER_NAME="smoke-test-$$"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME_NAME" >/dev/null 2>&1 || true
  rm -rf "$CERTS_DIR"
}
trap cleanup EXIT

fail() {
  echo "SMOKE TEST FAILED: $1" >&2
  docker logs "$CONTAINER_NAME" 2>&1 || true
  exit 1
}

echo "==> Checking image runs as non-root UID/GID 65532"
IMAGE_USER="$(docker inspect --format='{{.Config.User}}' "$IMAGE_REF")"
[[ "$IMAGE_USER" == "65532:65532" ]] || fail "expected image User=65532:65532, got '$IMAGE_USER'"

echo "==> Checking the runtime image has no shell"
if docker run --rm --entrypoint /bin/sh "$IMAGE_REF" -c true >/dev/null 2>&1; then
  fail "expected no shell in the runtime image, but /bin/sh executed successfully"
fi

echo "==> Creating persistent volume and starting the container under the hardened profile"
docker volume create "$VOLUME_NAME" >/dev/null
docker run -d --name "$CONTAINER_NAME" \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v "$VOLUME_NAME":/data \
  -p 127.0.0.1:8443:8443 \
  "$IMAGE_REF" >/dev/null

echo "==> Waiting for the health endpoint"
HEALTHY=0
for _ in $(seq 1 15); do
  if curl -ksf https://127.0.0.1:8443/health/ >/dev/null; then
    HEALTHY=1
    break
  fi
  sleep 2
done
[[ "$HEALTHY" -eq 1 ]] || fail "container never became healthy"

echo "==> Checking the browser home endpoint"
curl -ksf -o /dev/null https://127.0.0.1:8443/ || fail "home endpoint did not return 200"

echo "==> Checking the legacy PSU callback reaches the localhost-published container"
CALLBACK_STATUS="$(curl --noproxy '*' -ks -o /dev/null -w '%{http_code}' \
  https://0.0.0.0:8443/conformancesuite/callback)"
[[ "$CALLBACK_STATUS" == "200" ]] || fail "legacy callback returned HTTP $CALLBACK_STATUS instead of 200"

echo "==> Checking the read-only root filesystem blocks writes outside /data and /tmp"
if docker exec "$CONTAINER_NAME" python3 -c "open('/app/should-fail', 'w').write('x')" >/dev/null 2>&1; then
  fail "expected write to /app to fail under --read-only"
fi

echo "==> Checking /data/sessions, /data/results, and /data/logs are writable"
docker exec "$CONTAINER_NAME" python3 -c "
import os
for path in ('/data/sessions', '/data/results', '/data/logs'):
    assert os.path.isdir(path), f'{path} is not a directory'
    assert os.access(path, os.W_OK), f'{path} is not writable'
" || fail "expected /data subdirectories to exist and be writable"

echo "==> Capturing the persisted secret key"
FIRST_KEY="$(docker exec "$CONTAINER_NAME" python3 -c "print(open('/data/django-secret-key').read())")"
[[ -n "$FIRST_KEY" ]] || fail "no secret key was persisted under /data"
FIRST_CERTIFICATE="$(docker exec "$CONTAINER_NAME" python3 -c \
  "print(open('/data/tls/localhost-certificate.pem').read())")"
[[ -n "$FIRST_CERTIFICATE" ]] || fail "no local TLS certificate was persisted under /data"

echo "==> Restarting the container and checking runtime identity persists"
docker restart "$CONTAINER_NAME" >/dev/null
HEALTHY=0
for _ in $(seq 1 15); do
  if curl -ksf https://127.0.0.1:8443/health/ >/dev/null; then
    HEALTHY=1
    break
  fi
  sleep 2
done
[[ "$HEALTHY" -eq 1 ]] || fail "container never became healthy again after restart"
SECOND_KEY="$(docker exec "$CONTAINER_NAME" python3 -c "print(open('/data/django-secret-key').read())")"
[[ "$FIRST_KEY" == "$SECOND_KEY" ]] || fail "secret key changed across restart"
SECOND_CERTIFICATE="$(docker exec "$CONTAINER_NAME" python3 -c \
  "print(open('/data/tls/localhost-certificate.pem').read())")"
[[ "$FIRST_CERTIFICATE" == "$SECOND_CERTIFICATE" ]] || fail "local TLS certificate changed across restart"

echo "==> Stopping the container before the /certs and CLI-override checks"
docker rm -f "$CONTAINER_NAME" >/dev/null

echo "==> Checking the optional /certs mount is readable but not writable"
echo "dummy-ca-bundle" >"$CERTS_DIR/ca-bundle.pem"
# The container's non-root UID/GID 65532 needs read access to the bind mount.
chmod 755 "$CERTS_DIR"
chmod 644 "$CERTS_DIR/ca-bundle.pem"
docker run --rm \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v "$VOLUME_NAME":/data \
  -v "$CERTS_DIR":/certs:ro \
  --entrypoint python3 \
  "$IMAGE_REF" -c "
import pathlib
assert pathlib.Path('/certs/ca-bundle.pem').read_text() == 'dummy-ca-bundle\n'
try:
    pathlib.Path('/certs/should-fail').write_text('x')
except OSError:
    pass
else:
    raise SystemExit('expected write to /certs to fail')
" || fail "expected /certs to be readable but not writable"

echo "==> Checking the CLI command override works"
docker run --rm \
  --read-only --cap-drop=ALL --security-opt=no-new-privileges \
  --tmpfs /tmp:size=64m,mode=1777 \
  -v "$VOLUME_NAME":/data \
  "$IMAGE_REF" python3 main.py --help >/dev/null || fail "CLI command override did not run successfully"

echo "All smoke tests passed for $IMAGE_REF"
