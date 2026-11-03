#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Builds the Python packages the Android app (#567) needs and Chaquopy's package index does not have, or only in old
# versions: pydantic-core (Rust, through FastAPI), cryptography (Rust and OpenSSL, #571) and cffi (C, which
# cryptography needs), each in the version of requirements.lock for each ABI of the app, against Chaquopy's Python
# for Android. cryptography links the OpenSSL that Chaquopy's Python brings along, cffi the libffi of Chaquopy's
# package chaquopy-libffi. The wheels go into android/wheels/, where the Gradle build finds them.
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

pinned() {  # the block of a package in requirements.lock: its line with the version and its --hash lines
  awk -v name="$1==" 'index($0, name) == 1 { found = 1; print; next } found && /^[^ ]/ { exit } found' \
    "$ROOT/requirements.lock"
}

# SHA-256 of Chaquopy's Python for each ABI (Maven Central publishes only SHA-1 and MD5 for them)
target_sha256() {
  case $1 in
    arm64-v8a) echo 399b58d04898f2c7d5f0044e141bdf3818552e538e5f36d06d7fe0bebc298582 ;;
    armeabi-v7a) echo 7516b90fcb8a5a2df70e5f9899aab7e63fa43efa0455c3ba7b846ae54d70ee2c ;;
    x86_64) echo a479a9c74ca9e900f30b6089ffd23a10d74fe7a9f427d967bceddf8f3fe206f1 ;;
  esac
}

# Chaquopy's build of libffi for each ABI: the file the app installs, so its hash is the one pinned in
# android/app/build.gradle.kts
libffi_wheel() {
  case $1 in
    arm64-v8a) echo chaquopy_libffi-3.3-3-py3-none-android_24_arm64_v8a.whl ;;
    armeabi-v7a) echo chaquopy_libffi-3.3-2-py3-none-android_16_armeabi_v7a.whl ;;
    x86_64) echo chaquopy_libffi-3.3-3-py3-none-android_24_x86_64.whl ;;
  esac
}

check() {  # file, then the hashes it may have; a download that matches none is removed
  local sum
  sum=$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1")
  for allowed in "${@:2}"; do [ "$sum" = "$allowed" ] && return 0; done
  rm -f "$1"
  echo "$1: unexpected SHA-256 $sum" >&2
  exit 1
}

source_archive() {  # the source of a package; it must be the file requirements.lock pins (its hashes include the
  # one of the source archive)
  local file
  file="$(echo "$1" | tr - _)-$(version "$1").tar.gz"
  [ -f "$file" ] || tools/bin/pip -q download --no-deps --no-binary :all: "$1==$(version "$1")" -d . >&2
  # shellcheck disable=SC2046
  check "$file" $(pinned "$1" | sed -n 's/.*--hash=sha256:\([0-9a-f]*\).*/\1/p')
  echo "$file"
}

NDK="${ANDROID_NDK_HOME:-${ANDROID_HOME:?ANDROID_HOME or ANDROID_NDK_HOME must be set}/ndk/$NDK_VERSION}"
BIN=$(echo "$NDK"/toolchains/llvm/prebuilt/*/bin)
mkdir -p "$WORK" "$OUT"
cd "$WORK"
# maturin, and cffi of requirements.lock for cryptography's build, which runs it on the host to write its C code
if [ ! -x tools/bin/python3.11 ] || ! tools/bin/python -c 'import cffi' 2>/dev/null; then
  rm -rf tools && python3.11 -m venv tools
  { pinned cffi; pinned pycparser; } > tools-cffi.txt
  tools/bin/pip -q install --require-hashes -r "$ROOT/android/requirements-build.lock" -r tools-cffi.txt
fi

PYDANTIC_CORE=$(source_archive pydantic-core)
CRYPTOGRAPHY=$(source_archive cryptography)
CFFI=$(source_archive cffi)
CFFI_VERSION=$(version cffi)
# shellcheck disable=SC2046
set -- $(sed -n '/"chaquopy-libffi"/,/))/p' "$ROOT/android/app/build.gradle.kts" | grep -o '[0-9a-f]\{64\}')
LIBFFI_SHA256=("$@")
[ ${#LIBFFI_SHA256[@]} = 3 ] || { echo "no hashes of chaquopy-libffi in android/app/build.gradle.kts" >&2; exit 1; }

rm -rf built && mkdir built
for abi in $ABIS; do
  case $abi in
    arm64-v8a) rust=aarch64-linux-android; clang=aarch64-linux-android$API; width=64 ;;
    armeabi-v7a) rust=armv7-linux-androideabi; clang=armv7a-linux-androideabi$API; width=32 ;;
    x86_64) rust=x86_64-linux-android; clang=x86_64-linux-android$API; width=64 ;;
    *) echo "unknown ABI $abi" >&2; exit 1 ;;
  esac
  zip="target-$PYTHON_TARGET-$abi.zip"
  [ -f "$zip" ] || curl -fsSLO "https://repo.maven.apache.org/maven2/com/chaquo/python/target/$PYTHON_TARGET/$zip"
  check "$zip" "$(target_sha256 "$abi")"
  # its libraries, the headers of Python and of its OpenSSL
  rm -rf "python-$abi" && mkdir "python-$abi" && unzip -q "$zip" "jniLibs/*" "include/*" -d "python-$abi"
  python="$WORK/python-$abi"
  # PyO3 reads the target Python from this file: Chaquopy's Python has no _sysconfigdata to read it from
  cat > "pyo3-$abi.txt" <<CONFIG
implementation=CPython
version=3.11
shared=true
abi3=false
lib_name=python3.11
lib_dir=$python/jniLibs/$abi
pointer_width=$width
build_flags=
suppress_build_script_link_lines=false
CONFIG
  rustup target add "$rust" >/dev/null
  upper=$(echo "$rust" | tr 'a-z-' 'A-Z_')
  lower=$(echo "$rust" | tr '-' '_')
  cc="$BIN/$clang-clang"
  rust_env=("CARGO_TARGET_${upper}_LINKER=$cc" "CC_$lower=$cc" "AR_$lower=$BIN/llvm-ar"
    PYO3_CONFIG_FILE="$WORK/pyo3-$abi.txt" ANDROID_API_LEVEL=$API)

  rm -rf src && mkdir src && tar xzf "$PYDANTIC_CORE" -C src --strip-components=1
  (cd src && env "${rust_env[@]}" ../tools/bin/maturin build --release --target "$rust" -i python3.11 --out "$WORK/built")

  # cryptography: against the OpenSSL of Chaquopy's Python, so the app has only one OpenSSL. Linked by the names of
  # its libraries (libssl_python.so, as in CPython's Android build): the libssl_chaquopy.so Chaquopy's own builds
  # link are empty stubs pointing at these, which the linker (--as-needed) would drop. Its build script finds the
  # target's Python headers next to PYO3_CROSS_LIB_DIR and writes its C code with the host's cffi (PYO3_PYTHON).
  rm -rf src && mkdir src && tar xzf "$CRYPTOGRAPHY" -C src --strip-components=1
  (cd src && env "${rust_env[@]}" PYO3_PYTHON="$WORK/tools/bin/python" PYO3_CROSS_LIB_DIR="$python/lib/python3.11" \
     OPENSSL_INCLUDE_DIR="$python/include" OPENSSL_LIB_DIR="$python/jniLibs/$abi" OPENSSL_LIBS=ssl_python:crypto_python \
     ../tools/bin/maturin build --release --target "$rust" -i "$WORK/tools/bin/python" --out "$WORK/built")

  # cffi: one C module, compiled as its setup.py would on Linux, and its Python package into a wheel of its own
  libffi="$(libffi_wheel "$abi")"
  [ -f "$libffi" ] || curl -fsSLO "https://chaquo.com/pypi-13.1/chaquopy-libffi/$libffi"
  check "$libffi" "${LIBFFI_SHA256[@]}"
  rm -rf "libffi-$abi" src && unzip -q "$libffi" -d "libffi-$abi" && mkdir src
  tar xzf "$CFFI" -C src --strip-components=1
  "$cc" -shared -fPIC -O2 -DNDEBUG -DFFI_BUILDING=1 -DUSE__THREAD -DHAVE_SYNC_SYNCHRONIZE \
    -I"$python/include/python3.11" -I"libffi-$abi/chaquopy/include" src/src/c/_cffi_backend.c \
    -L"$python/jniLibs/$abi" -L"libffi-$abi/chaquopy/lib" -lpython3.11 -lffi -o src/_cffi_backend.so
  python3 - src "$WORK/built/cffi-$CFFI_VERSION-cp311-cp311-android_${API}_$(echo "$abi" | tr - _).whl" <<'PYTHON'
import base64, hashlib, sys, zipfile
from pathlib import Path

src, wheel = Path(sys.argv[1]), Path(sys.argv[2])
info = src / "src" / "cffi.egg-info"
name, version = wheel.name.split("-")[:2]
dist = f"{name}-{version}.dist-info"
tag = "-".join(wheel.stem.split("-")[2:])
files = {"_cffi_backend.so": (src / "_cffi_backend.so").read_bytes()}
files |= {f"cffi/{f.name}": f.read_bytes() for f in sorted((src / "src" / "cffi").iterdir())
          if f.suffix in (".py", ".h")}
files |= {
    f"{dist}/METADATA": (src / "PKG-INFO").read_bytes(),
    f"{dist}/WHEEL": f"Wheel-Version: 1.0\nGenerator: scripts/android-wheels.sh\nRoot-Is-Purelib: false\nTag: {tag}\n"
    .encode(),
    f"{dist}/entry_points.txt": (info / "entry_points.txt").read_bytes(),
    f"{dist}/top_level.txt": (info / "top_level.txt").read_bytes(),
    f"{dist}/licenses/LICENSE": (src / "LICENSE").read_bytes(),
    f"{dist}/licenses/AUTHORS": (src / "AUTHORS").read_bytes(),
}
digest = lambda data: base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
record = "".join(f"{path},sha256={digest(data)},{len(data)}\n" for path, data in files.items()) + f"{dist}/RECORD,,\n"
with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as out:
    for path, data in [*files.items(), (f"{dist}/RECORD", record.encode())]:
        out.writestr(zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
PYTHON
done

# Chaquopy's Python loads extension modules only as <name>.so (like its own wheels), not with the suffix maturin
# gives them (<name>.cpython-311-<arch>-android.so, <name>.abi3.so): renamed inside the wheel, RECORD with it. The
# type stubs go: their folders (_pydantic_core/, cryptography/hazmat/bindings/_rust/) would hide the modules
# _pydantic_core.so and _rust.so from Chaquopy's importer.
python3 - "$WORK/built" "$OUT" <<'PYTHON'
import re, sys, zipfile
from pathlib import Path

rename = lambda name: re.sub(r"\.(cpython-[^/]*|abi3)\.so$", ".so", name)
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
