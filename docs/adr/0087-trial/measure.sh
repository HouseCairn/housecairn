#!/usr/bin/env bash
# Measurements of ADR 0087 (distroless final image, study): the published image's
# Dockerfile against the trial Dockerfile of this directory, on linux/amd64.
#
# Usage, from anywhere in the repository, on a machine with Docker, curl and jq:
#
#   GRYPE=<path to grype> SYFT=<path to syft> docs/adr/0087-trial/measure.sh
#
# Use the Grype release pinned in .github/workflows/image.yml, so that the
# vulnerability counts compare with those of the `Container image` check. Every
# image is scanned by the same Grype binary, on the same database, with the same
# options (none, then --only-fixed). Nothing is pushed or published; the images
# are tagged hc-trial:* locally. Results go to $OUT (default trial-results/)
# and to $GITHUB_STEP_SUMMARY when it is set.
set -euo pipefail

cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
GRYPE=${GRYPE:?set GRYPE to the grype binary}
SYFT=${SYFT:?set SYFT to the syft binary}
OUT=${OUT:-trial-results}
RUNS=${RUNS:-5}
SUMMARY=${GITHUB_STEP_SUMMARY:-/dev/null}
mkdir -p "$OUT"
export GRYPE_CHECK_FOR_APP_UPDATE=false SYFT_CHECK_FOR_APP_UPDATE=false

# Base images, by digest (read from their registries on 2026-11-18).
SLIM=python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
DISTROLESS=gcr.io/distroless/python3-debian13:nonroot@sha256:6c4cb3bfa4b555429dd19541796a6ba6b30c84675b460bd0bd5072f7d5bdae71
DISTROLESS_DEBUG=gcr.io/distroless/python3-debian13:debug-nonroot@sha256:b017cf600ab1c2f9299b7e58865d5e87be452c31371763b9b6ae7e7af0fafcd4
CHAINGUARD=cgr.dev/chainguard/python:latest@sha256:d914788ef74519450a69bc5cce6a1cc747dbefb95a3d431daa4ff707767fea8a
BUSYBOX=busybox:1.37.0@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec

say() { printf '%s\n' "$*"; printf '%s\n' "$*" >>"$SUMMARY"; }
retry() { .github/scripts/retry.sh "$@"; }
now_ms() { date +%s%3N; }

say "## ADR 0087 trial measurements (linux/amd64)"
say ""
say "- Docker $(docker version -f '{{.Server.Version}}'), kernel $(uname -r), $(nproc) CPUs"
say "- $("$GRYPE" version 2>/dev/null | grep -m1 '^Version' | tr -s ' ')  (grype); $("$SYFT" version 2>/dev/null | grep -m1 '^Version' | tr -s ' ') (syft)"

# --- Pull the bases, build the three images ---------------------------------
for ref in "$SLIM" "$DISTROLESS" "$DISTROLESS_DEBUG" "$CHAINGUARD" "$BUSYBOX"; do
  retry docker pull -q "$ref" >/dev/null
done

build() { # name, then docker build arguments
  local name=$1 started
  shift
  started=$(now_ms)
  docker build --progress=plain --build-arg VERSION=trial -t "hc-trial:$name" "$@" . >"$OUT/build-$name.log" 2>&1 \
    || { tail -n 60 "$OUT/build-$name.log"; return 1; }
  echo "$(($(now_ms) - started))" >"$OUT/build-$name.ms"
}
build current
build distroless -f docs/adr/0087-trial/Dockerfile
build distroless-debug -f docs/adr/0087-trial/Dockerfile --build-arg "BASE=$DISTROLESS_DEBUG"

# image name -> reference; the Python of each (for the bare bases, its path).
declare -A REF=(
  [current]=hc-trial:current [distroless]=hc-trial:distroless [distroless-debug]=hc-trial:distroless-debug
  [base-slim]=$SLIM [base-distroless]=$DISTROLESS [base-chainguard]=$CHAINGUARD
)
declare -A PY=(
  [current]=python [distroless]=python [distroless-debug]=python
  [base-slim]=/usr/local/bin/python3.13 [base-distroless]=/usr/bin/python3.13 [base-chainguard]=/usr/bin/python
)
ORDER=(current distroless distroless-debug base-slim base-distroless base-chainguard)

# --- Python, venv, certificates, time zones, locale ---------------------------
PYPROBE='
import locale, os, ssl, sqlite3, sys, sysconfig, zlib, zoneinfo
print("python", sys.version.split()[0], sys.executable, "->", os.path.realpath(sys.executable))
cfg = os.path.join(sys.prefix, "pyvenv.cfg")
print("venv", open(cfg).read().replace("\n", "; ") if os.path.exists(cfg) else "none")
print("openssl", ssl.OPENSSL_VERSION, "| sqlite", sqlite3.sqlite_version, "| zlib", zlib.ZLIB_RUNTIME_VERSION)
paths = ssl.get_default_verify_paths()
print("certs", os.environ.get("SSL_CERT_FILE"), paths.openssl_cafile, os.path.exists("/etc/ssl/certs/ca-certificates.crt"))
try:
    print("tz", zoneinfo.TZPATH, os.path.exists("/usr/share/zoneinfo/Europe/Paris"), zoneinfo.ZoneInfo("Europe/Paris").key)
except Exception as exc:
    print("tz error", zoneinfo.TZPATH, exc)
try:
    print("locale", os.environ.get("LANG"), locale.setlocale(locale.LC_ALL, ""), locale.getpreferredencoding())
except locale.Error as exc:
    print("locale error", exc)
try:
    print("locale fr_FR.UTF-8", locale.setlocale(locale.LC_ALL, "fr_FR.UTF-8"))
except locale.Error as exc:
    print("locale fr_FR.UTF-8 unavailable:", exc)
'
say ""
say "### Interpreter and run-time files"
for name in "${ORDER[@]}"; do
  say ""
  say "**$name** (\`${REF[$name]}\`)"
  say '```'
  docker run --rm --network none --user 10001:10001 --entrypoint "${PY[$name]}" "${REF[$name]}" -c "$PYPROBE" 2>&1 \
    | tee -a "$SUMMARY" | tee "$OUT/python-$name.txt"
  say '```'
done
say ""
say "/data in the distroless trial image (mode, owner):"
docker run --rm --network none --entrypoint python hc-trial:distroless -c \
  'import os; s = os.stat("/data"); print(oct(s.st_mode), s.st_uid, s.st_gid)' | tee -a "$SUMMARY"

# --- Size, packages, binaries ------------------------------------------------
say ""
say "### Size, packages, binaries"
say ""
say "| Image | Size (bytes) | Packages (all) | deb | python | binary | other | Files | sh | bash | busybox | apt | dpkg |"
say "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |"
for name in "${ORDER[@]}"; do
  ref=${REF[$name]}
  size=$(docker image inspect -f '{{.Size}}' "$ref")
  "$SYFT" -q "docker:$ref" -o "json=$OUT/syft-$name.json"
  read -r all deb py bin other < <(jq -r '[.artifacts[] | .type] as $t
    | [($t | length), ($t | map(select(. == "deb")) | length), ($t | map(select(. == "python")) | length),
       ($t | map(select(. == "binary")) | length), ($t | map(select(. != "deb" and . != "python" and . != "binary")) | length)]
    | @tsv' "$OUT/syft-$name.json")
  cid=$(docker create "$ref")
  docker export "$cid" | tar -tf - >"$OUT/files-$name.txt"
  docker rm "$cid" >/dev/null
  files=$(wc -l <"$OUT/files-$name.txt")
  has() { grep -qE "^(usr/)?s?bin/$1\$|^busybox/$1\$" "$OUT/files-$name.txt" && echo yes || echo no; }
  say "| $name | $size | $all | $deb | $py | $bin | $other | $files | $(has sh) | $(has bash) | $(has busybox) | $(has apt) | $(has dpkg) |"
  jq -r '.artifacts[] | select(.name | test("^zlib")) | "\(.name) \(.version) (\(.type))"' "$OUT/syft-$name.json" \
    | sed "s/^/  zlib in $name: /" >>"$OUT/zlib.txt"
done
say ""
say '```'
tee -a "$SUMMARY" <"$OUT/zlib.txt"
say '```'

# --- Vulnerabilities ----------------------------------------------------------
"$GRYPE" db update >/dev/null
export GRYPE_DB_AUTO_UPDATE=false
say ""
say "### Vulnerabilities (same grype, same database, default options)"
say ""
say '```'
"$GRYPE" db status 2>&1 | tee -a "$SUMMARY"
say '```'
say ""
say "| Image | All | Critical | High | Medium | Low | Negligible | Unknown | Fixable (--only-fixed) | Fixable high or critical |"
say "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
for name in "${ORDER[@]}"; do
  ref=${REF[$name]}
  "$GRYPE" -q "docker:$ref" -o "json=$OUT/grype-$name.json"
  "$GRYPE" -q "docker:$ref" --only-fixed -o "json=$OUT/grype-fixed-$name.json"
  read -r all c h m l n u < <(jq -r '[.matches[] | .vulnerability.severity] as $s
    | [($s | length)] + (["Critical","High","Medium","Low","Negligible","Unknown"] | map(. as $x | $s | map(select(. == $x)) | length))
    | @tsv' "$OUT/grype-$name.json")
  fixed=$(jq '.matches | length' "$OUT/grype-fixed-$name.json")
  fixed_high=$(jq '[.matches[] | select(.vulnerability.severity == "High" or .vulnerability.severity == "Critical")] | length' \
    "$OUT/grype-fixed-$name.json")
  say "| $name | $all | $c | $h | $m | $l | $n | $u | $fixed | $fixed_high |"
  jq -r --arg n "$name" '.matches[] | "\($n) \(.vulnerability.id) \(.vulnerability.severity) \(.artifact.name) \(.artifact.version) fix=\(.vulnerability.fix.versions | join(","))"' \
    "$OUT/grype-fixed-$name.json" >>"$OUT/fixable.txt"
done
say ""
say "<details><summary>Fixable findings, every image</summary>"
say ""
say '```'
tee -a "$SUMMARY" <"$OUT/fixable.txt"
say '```'
say ""
say "</details>"

# --- Start-up time to /healthz -------------------------------------------------
NET=hc-trial-net
docker network rm "$NET" >/dev/null 2>&1 || true
docker network create "$NET" >/dev/null
GATEWAY=$(docker network inspect -f '{{(index .IPAM.Config 0).Gateway}}' "$NET")
RUN_ARGS=(
  --network "$NET" --read-only --cap-drop ALL --security-opt no-new-privileges
  --tmpfs "/tmp:rw,noexec,nosuid,size=64m" -p 127.0.0.1:18080:8000
  --env-file deploy/production.env.example -e ALLOWED_HOSTS=localhost
  -e CSRF_TRUSTED_ORIGINS=https://localhost -e "HOUSECAIRN_FORWARDED_ALLOW_IPS=$GATEWAY"
)
wait_healthz() { # prints the milliseconds since $1 until /healthz answers 200
  local started=$1 code
  for _ in $(seq 1 1200); do
    code=$(curl -4 -s -o /dev/null -w '%{http_code}' -m 2 -H 'X-Forwarded-Proto: https' \
      http://localhost:18080/healthz || true)
    if [ "$code" = 200 ]; then echo "$(($(now_ms) - started))"; return 0; fi
    sleep 0.1
  done
  echo "timeout"
  return 1
}
say ""
say "### Start-up time to the first 200 of /healthz ($RUNS runs each, ms, from \`docker run\` / \`docker start\`)"
say ""
say "| Image | First start, empty volume | Restart, same volume |"
say "| --- | --- | --- |"
for name in current distroless; do
  first=() again=()
  for _ in $(seq 1 "$RUNS"); do
    docker rm -f hc-trial-start >/dev/null 2>&1 || true
    docker volume rm -f hc-trial-start >/dev/null 2>&1 || true
    docker volume create hc-trial-start >/dev/null
    started=$(now_ms)
    docker run -d --name hc-trial-start -v hc-trial-start:/data "${RUN_ARGS[@]}" "hc-trial:$name" >/dev/null
    first+=("$(wait_healthz "$started" || true)")
    docker stop -t 30 hc-trial-start >/dev/null
    started=$(now_ms)
    docker start hc-trial-start >/dev/null
    again+=("$(wait_healthz "$started" || true)")
    docker rm -f hc-trial-start >/dev/null
  done
  docker volume rm -f hc-trial-start >/dev/null
  say "| $name | ${first[*]} | ${again[*]} |"
done

# --- The smoke test of the published image, replayed unchanged -----------------
say ""
say "### .github/scripts/image-smoke.sh, unchanged"
say ""
for target in current:sqlite distroless:sqlite distroless:postgresql distroless-debug:sqlite distroless-debug:postgresql; do
  name=${target%%:*} db=${target##*:}
  set +e
  .github/scripts/image-smoke.sh "hc-trial:$name" "$db" >"$OUT/smoke-$name-$db.log" 2>&1
  status=$?
  set -e
  passed=$(grep -c '^PASS:' "$OUT/smoke-$name-$db.log" || true)
  last=$(grep -E '^(FAIL:|All smoke checks passed)' "$OUT/smoke-$name-$db.log" | tail -n 1 || true)
  [ -n "$last" ] || last="stopped without a FAIL line: $(grep -v '^PASS:' "$OUT/smoke-$name-$db.log" | grep -v '^\s*$' | tail -n 1)"
  say "- hc-trial:$name, $db: exit $status, $passed PASS lines; last: $last"
done

# --- Debugging without a shell ---------------------------------------------------
say ""
say "### Debugging the distroless trial image"
docker rm -f hc-trial-debug >/dev/null 2>&1 || true
docker volume rm -f hc-trial-debug >/dev/null 2>&1 || true
docker run -d --name hc-trial-debug -v hc-trial-debug:/data "${RUN_ARGS[@]}" hc-trial:distroless >/dev/null
wait_healthz "$(now_ms)" >/dev/null
try() { # title, command...
  local title=$1 output status
  shift
  set +e
  output=$("$@" 2>&1)
  status=$?
  set -e
  say ""
  say "**$title** (exit $status)"
  say '```'
  printf '%s\n' "$output" | head -n 25 | tee -a "$SUMMARY"
  say '```'
}
try "docker exec: a shell" docker exec hc-trial-debug sh -c 'id'
try "docker exec: Python, the entry point's manage subcommand" \
  docker exec hc-trial-debug python -m housecairn.entrypoint manage check
# `docker cp … -` writes a tar stream: unpacked to standard output, so that the
# file's content is printed rather than its tar header.
try "docker cp out of the container" \
  bash -c 'set -o pipefail; docker cp hc-trial-debug:/app/.venv/pyvenv.cfg - | tar -xOf -'
try "Attached busybox: same PID and network namespaces, same user, no capability, read-only" \
  docker run --rm --pid=container:hc-trial-debug --network=container:hc-trial-debug \
  --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges --read-only "$BUSYBOX" \
  sh -c 'ps -o pid,user,args | head -n 8; echo; wget -qO- --header "Host: localhost" http://127.0.0.1:8000/healthz; echo; echo; ls /proc/1/root/app; head -n 1 /proc/1/root/etc/os-release'
try "Attached busybox as root, no capability" \
  docker run --rm --pid=container:hc-trial-debug --network=container:hc-trial-debug \
  --cap-drop ALL --security-opt no-new-privileges --read-only "$BUSYBOX" \
  sh -c 'ls /proc/1/root/app'
docker rm -f hc-trial-debug >/dev/null
docker volume rm -f hc-trial-debug >/dev/null
docker network rm "$NET" >/dev/null

say ""
say "Build times (ms): current $(cat "$OUT/build-current.ms"), distroless $(cat "$OUT/build-distroless.ms"), distroless-debug $(cat "$OUT/build-distroless-debug.ms")"
