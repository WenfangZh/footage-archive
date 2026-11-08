#!/usr/bin/env bash
# Runs the security scanners of SPEC.md §12 (M10): pip-audit, Bandit, Semgrep
# and gitleaks, each at a pinned version. The tools are installed into
# var/security-tools/ (gitignored), outside the archive's virtualenv, so that
# none of them enters the requirements files. Only the tools themselves are
# pinned, not the packages they pull in. Once installed, every tool runs even
# when an earlier one fails, and the script fails if any of them found
# something; a failed install (no network, a wrong checksum) stops it at once.
#
# Usage: scripts/security_scan.sh   (from anywhere; needs python3 and network)
set -euo pipefail

cd "$(dirname "$0")/.."

PIP_AUDIT_VERSION=2.10.1
BANDIT_VERSION=1.9.4
SEMGREP_VERSION=1.181.1
GITLEAKS_VERSION=8.30.1
GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb

# Published vulnerabilities with no fixed release yet, which would otherwise
# block every merge. One entry per line, with why it is accepted and a date to
# look again, e.g.:
#   GHSA-xxxx-xxxx-xxxx  # Django: unreachable, we don't use X; look again 2026-12-01
PIP_AUDIT_IGNORE=(
)

# Left out of every scan: code we didn't write and can't annotate.
#   */vendor: third-party JavaScript copied in (listed with hashes in each
#   folder's README, THIRD_PARTY_NOTICES.md); minified, so a finding there
#   can't be silenced where it is raised.
# Left out of Bandit, and of Semgrep by its default ignore list (which also
# skips *.min.js):
#   tests: asserts and subprocess calls to ffmpeg are how tests work, and no
#   test code runs in the archive.
VENDOR_DIRS=(clips/static/clips/vendor core/static/core/vendor maps/static/maps/vendor)

TOOLS=var/security-tools

# Installs a Python tool into its own virtualenv. A virtualenv can't be moved
# (its scripts name its path), so it is built in place and VERSION is written
# last: a crash halfway leaves no VERSION, and the next run starts again.
install_python_tool() {
    local name=$1 version=$2 dir=$TOOLS/$1
    if [[ -f $dir/VERSION && $(cat "$dir/VERSION") == "$version" ]]; then
        return
    fi
    rm -rf "$dir"
    python3 -m venv "$dir"
    "$dir/bin/pip" install --quiet --disable-pip-version-check "$name==$version"
    echo "$version" > "$dir/VERSION"
}

install_gitleaks() {
    local dir=$TOOLS/gitleaks
    if [[ -f $dir/VERSION && $(cat "$dir/VERSION") == "$GITLEAKS_VERSION" ]]; then
        return
    fi
    local tmp archive=gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz
    rm -rf "$TOOLS"/.gitleaks.*  # left by a run that crashed halfway
    tmp=$(mktemp -d "$TOOLS/.gitleaks.XXXXXX")
    curl -sSfL --proto '=https' --retry 3 -o "$tmp/$archive" \
        "https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION/$archive"
    echo "$GITLEAKS_SHA256  $tmp/$archive" | sha256sum --check --quiet
    tar -xzf "$tmp/$archive" -C "$tmp" gitleaks
    rm "$tmp/$archive"
    echo "$GITLEAKS_VERSION" > "$tmp/VERSION"
    rm -rf "$dir"
    mv "$tmp" "$dir"
}

mkdir -p "$TOOLS"
install_python_tool pip-audit "$PIP_AUDIT_VERSION"
install_python_tool bandit "$BANDIT_VERSION"
install_python_tool semgrep "$SEMGREP_VERSION"
install_gitleaks

failed=()

echo "== pip-audit: published vulnerabilities in the pinned requirements"
ignore_args=()
for id in "${PIP_AUDIT_IGNORE[@]}"; do
    ignore_args+=(--ignore-vuln "$id")
done
"$TOOLS/pip-audit/bin/pip-audit" --progress-spinner off --strict \
    -r requirements-dev.txt "${ignore_args[@]}" || failed+=(pip-audit)

echo "== Bandit: unsafe patterns in the Python code"
bandit_exclude=./tests,./var,./media_test
for d in "${VENDOR_DIRS[@]}"; do
    bandit_exclude+=",./$d"
done
"$TOOLS/bandit/bin/bandit" --quiet --recursive . --exclude "$bandit_exclude" \
    || failed+=(bandit)

echo "== Semgrep: Python and Django rules, metrics off"
# The rules are fetched at each run: their licence allows only internal use,
# so they are never copied into this public repository. Semgrep scans only
# files git tracks, so var/ and media_test/ are left out already.
semgrep_exclude=()
for d in "${VENDOR_DIRS[@]}"; do
    semgrep_exclude+=(--exclude "$d")
done
"$TOOLS/semgrep/bin/semgrep" scan --metrics off --disable-version-check --quiet \
    --error --config p/python --config p/django "${semgrep_exclude[@]}" . \
    || failed+=(semgrep)

echo "== gitleaks: secrets anywhere in the git history"
# --redact keeps any secret found out of the output, and so out of CI logs.
"$TOOLS/gitleaks/gitleaks" git --no-banner --redact --log-opts=--all . \
    || failed+=(gitleaks)

if ((${#failed[@]})); then
    echo "Security scan failed: ${failed[*]}" >&2
    exit 1
fi
echo "Security scan passed."
