#!/usr/bin/env bash
# Build the AgentCore Runtime package.
#
# Two differences from scripts/build_lambda.sh, both of which cost a failed
# deploy to discover:
#
#   1. AgentCore Runtime is ARM64. An x86_64 artifact is accepted by the API and
#      then fails asynchronously with "Your artifact contains binary files that
#      are incompatible with Linux ARM64" — so the wheels must be aarch64.
#   2. It carries bedrock-agentcore and agentcore_app.py, and does NOT carry the
#      web stack; the scheduled arm has no browser.
#
# Same trick as the Lambda build for resolving dependencies: markers are
# evaluated for the target rather than the build machine, then installed with
# --no-deps. See build_lambda.sh for why that is necessary.
#
# Usage:  bash scripts/build_agentcore.sh
# Output: build-ac/agent.zip
set -euo pipefail

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"   # .venv/bin/python on macOS/Linux
TARGET_PY="3.12"
PLATFORM="manylinux2014_aarch64"               # ARM64 — see note 1 above

rm -rf build-ac
mkdir -p build-ac/pkg

echo "→ resolving the dependency closure for linux-arm64/py${TARGET_PY}"
"$PYTHON" - > build-ac/closure.txt <<'PYEOF'
import importlib.metadata as md
from packaging.requirements import Requirement

ROOTS = ["strands-agents", "bedrock-agentcore", "httpx",
         "beautifulsoup4", "pydantic", "boto3"]
ENV = {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
       "python_version": "3.12", "python_full_version": "3.12.0",
       "platform_machine": "aarch64", "implementation_name": "cpython", "extra": ""}

seen, stack = {}, list(ROOTS)
while stack:
    name = stack.pop()
    key = name.lower().replace("_", "-")
    if key in seen:
        continue
    try:
        dist = md.distribution(name)
    except md.PackageNotFoundError:
        continue
    seen[key] = dist.version
    for raw in (dist.requires or []):
        req = Requirement(raw)
        if req.marker and not req.marker.evaluate(ENV):
            continue
        stack.append(req.name)

for name, version in sorted(seen.items()):
    print(f"{name}=={version}")
PYEOF
echo "  $(wc -l < build-ac/closure.txt) packages"

echo "→ downloading aarch64 wheels"
"$PYTHON" -m pip install -q --target build-ac/pkg --no-deps \
  --platform "$PLATFORM" --implementation cp --python-version "$TARGET_PY" \
  --only-binary=:all: -r build-ac/closure.txt

# Fail loudly here rather than three minutes into a CREATE_FAILED.
if find build-ac/pkg -name "*x86_64*.so" | grep -q .; then
  echo "✗ x86_64 objects in an ARM64 artifact — the runtime would reject this" >&2
  exit 1
fi

echo "→ adding application code"
cp -r src agentcore_app.py build-ac/pkg/
find build-ac/pkg -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find build-ac/pkg -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null || true

echo "→ zipping"
"$PYTHON" -c "
import shutil, os
os.chdir('build-ac/pkg')
shutil.make_archive('../agent', 'zip', '.')
"

echo "✓ build-ac/agent.zip  ($(du -m build-ac/agent.zip | cut -f1) MB)"
