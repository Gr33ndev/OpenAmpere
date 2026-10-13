# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""GHSA-8893-x8jf-64v6: the update helper and the installer start only an image signed for the stable version in its
label, never an older version than the one running (or than the one the app asked for) and never the image of the
Home Assistant app. An older signed release has known flaws; whoever could serve it must not bring it back."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
IMAGE = "ghcr.io/gr33ndev/openampere:latest"
SIGNER = "https://github.com/Gr33ndev/OpenAmpere/.github/workflows/release.yml@refs/tags/"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# a stand-in for docker: images are ids ("old", "new") with labels from $FAKE/labels-<id>; every call goes into $FAKE/log
FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >>"$FAKE/log"
read_file() { [ -s "$FAKE/$1" ] && cat "$FAKE/$1"; }
case "$1 $2" in
  "compose ps") read_file container; exit 0 ;;
  "compose config")
    case "$*" in *--services*) printf 'openampere\nupdater\n' ;; *--images*) printf '%s\ndocker:cli\n' "$IMAGE" ;; esac
    exit 0 ;;
  "compose pull") cp "$FAKE/pulled" "$FAKE/tag"; exit 0 ;;
  "compose up") echo c0ffee >"$FAKE/container"; exit 0 ;;
  "compose run") # the installer's check: run the updater script like the helper container would
    while [ "$1" != updater ]; do shift; done
    shift
    exec sh "$@" ;;
  "image inspect") # image inspect -f FORMAT REF
    ref=$5
    [ "$ref" = "$IMAGE" ] && ref=$(read_file tag)
    [ -n "$ref" ] || exit 1
    case "$4" in
      *RepoDigests*) echo "ghcr.io/gr33ndev/openampere@sha256:feed" ;;
      *Labels*)
        name=$(printf '%s\n' "$4" | sed 's/.*"\(.*\)".*/\1/')
        [ -f "$FAKE/labels-$ref" ] && sed -n "s/^$name=//p" "$FAKE/labels-$ref" ;;
      *) echo "$ref" ;;
    esac
    exit 0 ;;
  "image rm" | "image prune") exit 0 ;;
  "tag "*) echo "$2" >"$FAKE/tag"; exit 0 ;;
  "inspect -f")
    [ -n "$4" ] || exit 1
    case "$3" in *State.Running*) read_file health ;; *Config.Image*) echo "$IMAGE" ;; *) read_file running ;; esac
    exit 0 ;;
esac
echo "unexpected docker call: $*" >&2
exit 2
"""

# a stand-in for cosign: the attestation was signed by the workflow run of the tag in $FAKE/signer
FAKE_COSIGN = r"""#!/bin/sh
signer=$(cat "$FAKE/signer")
while [ $# -gt 0 ]; do
  case "$1" in
    --certificate-identity) [ "$2" = "$signer" ]; exit $? ;;
    --certificate-identity-regexp) printf '%s\n' "$signer" | grep -Eq -- "$2"; exit $? ;;
  esac
  shift
done
exit 1
"""


@pytest.fixture
def fake(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"docker": FAKE_DOCKER, "cosign": FAKE_COSIGN, "apk": "#!/bin/sh\nexit 0\n"}.items():
        tool = bin_dir / name
        tool.write_text(body)
        tool.chmod(0o755)
    (tmp_path / "fake").mkdir()
    shutil.copy(SCRIPTS / "updater.sh", tmp_path / "updater.sh")
    return tmp_path


def setup(folder: Path, *, new: dict, signed: str, running: str = "old", old: str = "0.26.4",
          health: str = "true 0 healthy") -> Path:
    """running: the id of the image that runs now ("" if none); old/new: the version labels; signed: the tag of the
    workflow run that signed the pulled image."""
    state = folder / "fake"
    (state / "log").write_text("")
    (state / "container").write_text("c0ffee" if running else "")
    (state / "running").write_text(running)
    (state / "tag").write_text(running)
    (state / "pulled").write_text("new")
    (state / "health").write_text(health)
    (state / "signer").write_text(SIGNER + signed)
    (state / "labels-old").write_text(f"org.opencontainers.image.version={old}\n")
    (state / "labels-new").write_text("".join(f"{key}={value}\n" for key, value in new.items()))
    return state


def stable(version: str) -> dict:
    return {"org.opencontainers.image.version": version}


def env(folder: Path) -> dict:
    return {"PATH": f"{folder / 'bin'}:/usr/bin:/bin", "FAKE": str(folder / "fake"), "IMAGE": IMAGE,
            "HOME": str(folder), "OPENAMPERE_DIR": str(folder), "OPENAMPERE_CHECK_AFTER_S": "0"}


def run_update(folder: Path, to: str | None = None) -> tuple[str, str]:
    """One pass of the helper's loop with a request from the app, like updates.py writes it."""
    inbox = folder / "update" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "request").write_text(json.dumps({"ts": 1, "from": "0.26.4", "to": to, "reason": "manual"}))
    functions = folder / "updater-loop.sh"
    functions.write_text((SCRIPTS / "updater.sh").read_text().replace("sleep 5 &", "exit 0 #"))
    done = subprocess.run(["sh", str(functions)], capture_output=True, text=True, timeout=20, env=env(folder))
    assert done.returncode == 0, done.stderr
    return (folder / "fake" / "log").read_text(), (folder / "update" / "status.json").read_text()


def refused(folder: Path, log: str, status: str) -> bool:
    started = "compose up" in log
    back = (folder / "fake" / "tag").read_text().strip() == "old"
    return not started and back and '"failed"' in status and "nicht als echt bestätigt" in status


def test_an_older_signed_release_is_refused(fake):
    setup(fake, new=stable("0.20.0"), signed="v0.20.0")
    assert refused(fake, *run_update(fake))


@pytest.mark.parametrize("version", ["0.27.0-rc.1", "latest", "", "0.27", "v0.27.0"])
def test_a_pre_release_or_an_odd_version_label_is_refused(fake, version):
    setup(fake, new=stable(version), signed=f"v{version}")
    assert refused(fake, *run_update(fake))


def test_the_signature_must_be_for_the_version_in_the_label(fake):
    """An old release relabelled as new: its attestation names the tag it was built from."""
    setup(fake, new=stable("0.27.0"), signed="v0.20.0")
    assert refused(fake, *run_update(fake))


def test_the_image_of_the_home_assistant_app_is_refused(fake):
    setup(fake, new={**stable("0.27.0"), "io.hass.type": "app", "io.hass.version": "0.27.0"}, signed="v0.27.0")
    assert refused(fake, *run_update(fake))


def test_an_image_older_than_the_requested_version_is_refused(fake):
    setup(fake, new=stable("0.26.5"), signed="v0.26.5")
    assert refused(fake, *run_update(fake, to="0.27.0"))


@pytest.mark.parametrize(("version", "to"), [("0.27.0", None), ("0.27.0", "0.27.0"), ("0.27.1", "0.27.0"),
                                             ("0.26.4", None), ("1.0.0", "0.26.10"), ("0.27.0", "0.27.0; rm -rf /")])
def test_the_same_or_a_newer_release_is_started(fake, version, to):
    setup(fake, new=stable(version), signed=f"v{version}")
    log, status = run_update(fake, to)
    assert "compose up" in log and '"done"' in status and "Update installiert" in status


def test_versions_are_compared_as_numbers(fake):
    setup(fake, new=stable("0.9.0"), signed="v0.9.0", old="0.10.0")
    assert refused(fake, *run_update(fake))


def test_without_a_running_app_the_image_on_disk_is_the_lower_bound(fake):
    state = setup(fake, new=stable("0.20.0"), signed="v0.20.0", running="")
    (state / "tag").write_text("old")  # the version installed before, not running (e.g. crashed)
    log, status = run_update(fake)
    assert "compose up" not in log and "nicht als echt bestätigt" in status


def test_a_new_version_that_does_not_start_is_rolled_back(fake):
    """The rollback goes to the image that ran before, by its id: no check, no download."""
    setup(fake, new=stable("0.27.0"), signed="v0.27.0", health="false 0")
    log, status = run_update(fake)
    assert f"tag old {IMAGE}" in log and log.rstrip().endswith("compose up -d openampere\nimage rm new")  # #356
    assert "Die bisherige Version läuft wieder" in status


def verify(folder: Path, *args: str) -> int:
    return subprocess.run(["sh", str(SCRIPTS / "updater.sh"), "verify", IMAGE, *args], capture_output=True, text=True,
                          timeout=20, env=env(folder)).returncode


def test_the_check_for_the_installer_takes_the_running_image(fake):
    setup(fake, new=stable("0.20.0"), signed="v0.20.0")
    (fake / "fake" / "tag").write_text("new")
    assert verify(fake, "old") == 1
    assert verify(fake) == 0  # a new installation: no lower bound


def install(folder: Path) -> tuple[subprocess.CompletedProcess, str]:
    functions = folder / "install-functions.sh"
    functions.write_text((SCRIPTS / "install.sh").read_text().replace('\nmain "$@"\n', "\n"))
    code = f'source "{functions}"\nsleep() {{ :; }}\nSUDO="" DOCKER=docker DIR="{folder}"\nstart_and_report'
    done = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=20,
                          env={**env(folder), "OPENAMPERE_YES": "1"})
    return done, (folder / "fake" / "log").read_text()


def test_running_the_installer_again_does_not_install_an_older_release(fake):
    setup(fake, new=stable("0.20.0"), signed="v0.20.0")
    done, log = install(fake)
    assert done.returncode == 1 and "nicht als echt bestätigt" in done.stderr
    assert "compose up" not in log and f"tag old {IMAGE}" in log


def test_a_new_installation_has_no_lower_bound(fake):
    setup(fake, new=stable("0.20.0"), signed="v0.20.0", running="")
    _, log = install(fake)
    assert "compose up" in log
