#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Builds the Python packages the Android app (#567) needs and Chaquopy's package index does not have: pydantic-core
# (Rust, through FastAPI) for each ABI of the app, against Chaquopy's Python for Android. The wheels go into
# android/wheels/, where the Gradle build finds them.
#
# Needs: Rust (rustup), the Android NDK (ANDROID_NDK_HOME, or ANDROID_HOME with ndk/<NDK_VERSION>), python3 and
# python3.11 (maturin prepares the cross build with the host Python of the same version).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_TARGET="3.11.17-0"  # Chaquopy's Python, as in android/app/build.gradle.kts
NDK_VERSION="27.2.12479018"
API=24  # minimum Android version of the app (7.0)
ABIS="${ABIS:-arm64-v8a armeabi-v7a x86_64}"
OUT="$ROOT/android/wheels"
WORK="${WORK:-$ROOT/android/build/wheels}"

version() {  # the pinned version from requirements.lock, so the app has the same as the Docker image
  sed -n "s/^$1==\([^ ]*\).*/\1/p" "$ROOT/requirements.lock" | head -1
}

NDK="${ANDROID_NDK_HOME:-${ANDROID_HOME:?ANDROID_HOME or ANDROID_NDK_HOME must be set}/ndk/$NDK_VERSION}"
BIN=$(echo "$NDK"/toolchains/llvm/prebuilt/*/bin)
mkdir -p "$WORK" "$OUT"
cd "$WORK"
[ -x tools/bin/maturin ] || { python3 -m venv tools && tools/bin/pip -q install "maturin>=1.9,<2"; }

PYDANTIC_CORE=$(version pydantic-core)
[ -f "pydantic_core-$PYDANTIC_CORE.tar.gz" ] || \
  tools/bin/pip -q download --no-deps --no-binary :all: "pydantic-core==$PYDANTIC_CORE" -d .

for abi in $ABIS; do
  case $abi in
    arm64-v8a) rust=aarch64-linux-android; clang=aarch64-linux-android$API; width=64 ;;
    armeabi-v7a) rust=armv7-linux-androideabi; clang=armv7a-linux-androideabi$API; width=32 ;;
    x86_64) rust=x86_64-linux-android; clang=x86_64-linux-android$API; width=64 ;;
    *) echo "unknown ABI $abi" >&2; exit 1 ;;
  esac
  zip="target-$PYTHON_TARGET-$abi.zip"
  [ -f "$zip" ] || curl -fsSLO "https://repo.maven.apache.org/maven2/com/chaquo/python/target/$PYTHON_TARGET/$zip"
  rm -rf "python-$abi" && mkdir "python-$abi" && unzip -q "$zip" "jniLibs/*" -d "python-$abi"
  # PyO3 reads the target Python from this file: Chaquopy's Python has no _sysconfigdata to read it from
  cat > "pyo3-$abi.txt" <<CONFIG
implementation=CPython
version=3.11
shared=true
abi3=false
lib_name=python3.11
lib_dir=$WORK/python-$abi/jniLibs/$abi
pointer_width=$width
build_flags=
suppress_build_script_link_lines=false
CONFIG
  rustup target add "$rust" >/dev/null
  upper=$(echo "$rust" | tr 'a-z-' 'A-Z_')
  lower=$(echo "$rust" | tr '-' '_')
  rm -rf src && mkdir src && tar xzf "pydantic_core-$PYDANTIC_CORE.tar.gz" -C src --strip-components=1
  (cd src && env "CARGO_TARGET_${upper}_LINKER=$BIN/$clang-clang" "CC_$lower=$BIN/$clang-clang" "AR_$lower=$BIN/llvm-ar" \
     PYO3_CONFIG_FILE="$WORK/pyo3-$abi.txt" ANDROID_API_LEVEL=$API \
     ../tools/bin/maturin build --release --target "$rust" -i python3.11 --out "$WORK/built")
done

# Chaquopy's Python loads extension modules only as <name>.so (like its own wheels), not with the suffix maturin
# gives them (<name>.cpython-311-<arch>-android.so): renamed inside the wheel, RECORD with it. The type stubs go:
# their folder _pydantic_core/ would hide the module _pydantic_core.so from Chaquopy's importer.
python3 - "$WORK/built" "$OUT" <<'PYTHON'
import re, sys, zipfile
from pathlib import Path

rename = lambda name: re.sub(r"\.cpython-[^/]*\.so$", ".so", name)
for wheel in Path(sys.argv[1]).glob("*.whl"):
    with zipfile.ZipFile(wheel) as src, zipfile.ZipFile(Path(sys.argv[2]) / wheel.name, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename.endswith(".pyi"):
                continue
            data = src.read(item)
            if item.filename.endswith(".dist-info/RECORD"):
                lines = [line for line in data.decode().split("\n") if not line.split(",", 1)[0].endswith(".pyi")]
                data = "\n".join(rename(line.split(",", 1)[0]) + line[len(line.split(",", 1)[0]):]
                                 for line in lines).encode()
            dst.writestr(rename(item.filename), data)
    wheel.unlink()
PYTHON
ls -l "$OUT"
