#!/usr/bin/env bash
# Rebuild PID.fmu from source. Needs a C compiler that can produce a 64-bit
# Windows DLL (this was built and tested with MSYS2 mingw-w64 gcc 15.2.0,
# normally at C:\msys64\mingw64\bin\gcc.exe). Run from Git Bash or WSL.
#
#   bash build.sh
#
# Produces build/PID.dll and PID.fmu (next to this script). Neither is
# committed to git -- *.fmu and *.dll are gitignored project-wide, the same
# convention the rest of this repository's native binaries follow -- so this
# script is how you get them back after a fresh clone.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

CODE_DIR="../../../src/fmpy/c-code"
if [ ! -f "$CODE_DIR/fmi2Functions.h" ]; then
    echo "Cannot find $CODE_DIR/fmi2Functions.h -- run this from examples/agent/pid_demo" >&2
    exit 1
fi

GCC="${GCC:-gcc}"
if ! command -v "$GCC" >/dev/null 2>&1; then
    for candidate in /c/msys64/mingw64/bin/gcc.exe /mingw64/bin/gcc.exe; do
        if [ -x "$candidate" ]; then GCC="$candidate"; break; fi
    done
fi
echo "Using compiler: $(command -v "$GCC" || echo "$GCC")"

rm -rf build
mkdir -p build/binaries/win64 build/sources

"$GCC" -shared -O2 -Wall -Wextra \
    -I "$CODE_DIR" -I src \
    src/pid_fmi2.c src/PID.c \
    -o build/binaries/win64/PID.dll

cp src/PID.c src/PID.h src/pid_fmi2.c src/LICENSE_PID.txt build/sources/
cp modelDescription.xml build/modelDescription.xml

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" -c "" >/dev/null 2>&1; then
            PYTHON="$candidate"; break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "No working Python found on PATH. Set PYTHON=/path/to/python.exe (e.g. your" >&2
    echo "project .venv's python) and run again: PYTHON=.venv/Scripts/python bash build.sh" >&2
    exit 1
fi
echo "Using Python: $PYTHON"

"$PYTHON" - "$PWD/build" "$PWD/PID.fmu" <<'PYEOF'
import sys, zipfile, os
build_dir, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(build_dir):
        for name in files:
            path = os.path.join(root, name)
            z.write(path, os.path.relpath(path, build_dir))
print(f"wrote {out} ({os.path.getsize(out)} bytes)")
PYEOF

echo "Done. Verify with:"
echo "  python -m fmpy.scenario_agent describe --fmu $(dirname "$0")/PID.fmu"
