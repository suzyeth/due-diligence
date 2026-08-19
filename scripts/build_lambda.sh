#!/usr/bin/env bash
# Build the Lambda deployment package.
#
# Why this is not just `pip install -r requirements.txt -t build/`:
#
# strands-agents depends on mcp, which requires pywin32 when sys_platform is
# win32. pip evaluates that marker against the machine running pip, not against
# --platform, so resolving on Windows for a Linux target is impossible. The way
# out is to resolve the dependency graph ourselves with the markers set for the
# target, then install that closure with --no-deps so pip never re-resolves.
#
# Usage:  bash scripts/build_lambda.sh
# Output: build/function.zip
set -euo pipefail

PYTHON="${PYTHON:-.venv/Scripts/python.exe}"   # .venv/bin/python on macOS/Linux
TARGET_PY="3.12"                               # must match the Lambda runtime
PLATFORM="manylinux2014_x86_64"

rm -rf build
mkdir -p build/pkg

echo "→ resolving the dependency closure for linux/py${TARGET_PY}"
"$PYTHON" - > build/closure.txt <<'PYEOF'
import importlib.metadata as md
from packaging.requirements import Requirement

ROOTS = ["strands-agents", "fastapi", "jinja2", "mangum", "httpx",
         "beautifulsoup4", "pydantic", "boto3"]
# Markers resolved for the TARGET, which is the entire point of this script.
ENV = {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
       "python_version": "3.12", "python_full_version": "3.12.0",
       "platform_machine": "x86_64", "implementation_name": "cpython", "extra": ""}

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
echo "  $(wc -l < build/closure.txt) packages"

echo "→ downloading linux wheels"
"$PYTHON" -m pip install -q --target build/pkg --no-deps \
  --platform "$PLATFORM" --implementation cp --python-version "$TARGET_PY" \
  --only-binary=:all: -r build/closure.txt

echo "→ adding application code"
cp -r src web lambda_handler.py build/pkg/
find build/pkg -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find build/pkg -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null || true

echo "→ zipping"
"$PYTHON" -c "
import shutil, os
os.chdir('build/pkg')
shutil.make_archive('../function', 'zip', '.')
"

echo "✓ build/function.zip  ($(du -m build/function.zip | cut -f1) MB)"
