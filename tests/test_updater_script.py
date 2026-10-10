# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""scripts/updater.sh and the update path of scripts/install.sh only start an OpenAmpere image whose build provenance
was confirmed, also when the app is not running."""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
IMAGE = "ghcr.io/gr33ndev/openampere:latest"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# a stand-in for docker: answers from files in $FAKE, every call goes into $FAKE/log
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
  "compose run") exit "$(cat "$FAKE/verify")" ;;
  "image inspect")
    case "$*" in *RepoDigests*) echo "ghcr.io/gr33ndev/openampere@sha256:feed" ;; *) read_file tag || exit 1 ;; esac
    exit 0 ;;
  "image rm" | "image prune") exit 0 ;;
  "tag "*) echo "$2" >"$FAKE/tag"; exit 0 ;;
  "inspect -f")
    [ -n "$4" ] || exit 1
    case "$3" in *State.Running*) echo "true 0 healthy" ;; *Config.Image*) echo "$IMAGE" ;; *) read_file running ;; esac
    exit 0 ;;
esac
echo "unexpected docker call: $*" >&2
exit 2
"""


@pytest.fixture
def fake(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"docker": FAKE_DOCKER, "cosign": 'exit "$(cat "$FAKE/cosign")"\n', "apk": "exit 0\n"}.items():
        tool = bin_dir / name
        tool.write_text(body if body.startswith("#!") else "#!/bin/sh\n" + body)
        tool.chmod(0o755)
    state = tmp_path / "fake"
    state.mkdir()
    (state / "log").write_text("")
    return tmp_path


def setup(folder: Path, *, running: str, before: str, pulled: str, cosign: int = 0, verify: int = 0) -> Path:
    state = folder / "fake"
    (state / "container").write_text("c0ffee" if running else "")
    (state / "running").write_text(running)
    (state / "tag").write_text(before)
    (state / "pulled").write_text(pulled)
    (state / "cosign").write_text(str(cosign))
    (state / "verify").write_text(str(verify))
    return state


def env(folder: Path) -> dict:
    return {"PATH": f"{folder / 'bin'}:/usr/bin:/bin", "FAKE": str(folder / "fake"), "IMAGE": IMAGE,
            "HOME": str(folder), "OPENAMPERE_DIR": str(folder), "OPENAMPERE_CHECK_AFTER_S": "0"}


def run_update(folder: Path) -> tuple[str, str]:
    functions = folder / "updater-functions.sh"
    functions.write_text((SCRIPTS / "updater.sh").read_text().replace('\nmain "$@"\n', "\n"))
    done = subprocess.run(["sh", "-c", f'. "{functions}"\nupdate'], capture_output=True, text=True, timeout=20, env=env(folder))
    assert done.returncode == 0, done.stderr
    return (folder / "fake" / "log").read_text(), (folder / "data" / "update" / "status.json").read_text()


def test_an_unconfirmed_image_is_not_started_when_the_app_is_not_running(fake):
    state = setup(fake, running="", before="sha256:old", pulled="sha256:evil", cosign=1)
    log, status = run_update(fake)
    assert "compose up" not in log
    assert '"failed"' in status and "nicht als echt bestätigt" in status
    assert (state / "tag").read_text().strip() == "sha256:old"  # the tag is back on the version before


def test_an_image_that_was_never_confirmed_is_checked_before_it_is_started(fake):
    """Nothing new to download, but the app does not run: the image on disk is checked, too."""
    setup(fake, running="", before="sha256:left", pulled="sha256:left", cosign=1)
    log, _ = run_update(fake)
    assert "compose up" not in log and f"image rm {IMAGE}" in log


def test_a_confirmed_image_is_started_when_the_app_is_not_running(fake):
    setup(fake, running="", before="sha256:old", pulled="sha256:new", cosign=0)
    log, status = run_update(fake)
    assert "compose up" in log and '"done"' in status


def test_an_unconfirmed_update_keeps_the_running_version(fake):
    state = setup(fake, running="sha256:old", before="sha256:old", pulled="sha256:evil", cosign=1)
    log, _ = run_update(fake)
    assert "compose up" not in log and f"tag sha256:old {IMAGE}" in log
    assert (state / "tag").read_text().strip() == "sha256:old"


def test_the_running_version_is_not_checked_again(fake):
    setup(fake, running="sha256:old", before="sha256:old", pulled="sha256:old", cosign=1)
    log, status = run_update(fake)
    assert "compose up" in log and "schon auf dem neuesten Stand" in status


@pytest.mark.parametrize(("cosign", "expected"), [(0, 0), (1, 1)])
def test_the_check_alone_for_the_installer(fake, cosign, expected):
    setup(fake, running="", before="", pulled="", cosign=cosign)
    done = subprocess.run(["sh", str(SCRIPTS / "updater.sh"), "verify", IMAGE], capture_output=True, text=True, timeout=20,
                          env=env(fake))
    assert done.returncode == expected, done.stderr


@pytest.mark.parametrize(("verify", "started"), [(1, False), (0, True)])
def test_the_installer_starts_only_a_confirmed_image(fake, verify, started):
    setup(fake, running="sha256:old", before="sha256:old", pulled="sha256:new", verify=verify)
    functions = fake / "install-functions.sh"
    functions.write_text((SCRIPTS / "install.sh").read_text().replace('\nmain "$@"\n', "\n"))
    code = f'source "{functions}"\nSUDO="" DOCKER=docker DIR="{fake}"\nstart_and_report'
    done = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=20, env={**env(fake), "OPENAMPERE_YES": "1"})
    log = (fake / "fake" / "log").read_text()
    assert ("compose up" in log) is started
    if not started:
        assert done.returncode == 1 and "nicht als echt bestätigt" in done.stderr
        assert f"tag sha256:old {IMAGE}" in log
