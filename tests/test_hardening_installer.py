# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Hardening of the install script, the helper containers and the start of the Home Assistant app (#357)."""

import hashlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from test_install_script import run_functions, write_compose

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- config.yaml of a new installation ---

# install as root sets owner and mode; here it stands in and records how it was called
RECORD_INSTALL = 'install() { echo "$*" > "LOG"; cat > "${@: -1}"; }\n'


def write_config(folder: Path) -> subprocess.CompletedProcess:
    (folder / "install" / "data").mkdir(parents=True, exist_ok=True)
    code = (RECORD_INSTALL.replace("LOG", str(folder / "install.log"))
            + f'DIR="{folder / "install"}" TZ_NAME=Europe/Berlin EVCC_URL=http://localhost:7070\nwrite_config')
    return run_functions(folder, code)


def test_the_first_config_belongs_to_the_app_and_only_the_app_reads_it(tmp_path):
    done = write_config(tmp_path)
    assert done.returncode == 0, done.stderr
    config = tmp_path / "install" / "data" / "config.yaml"
    assert (tmp_path / "install.log").read_text().split() == ["-m", "600", "-o", "1000", "-g", "1000", "/dev/stdin",
                                                              str(config)]
    assert "timezone: Europe/Berlin" in config.read_text() and "url: http://localhost:7070" in config.read_text()


def test_the_installer_does_not_write_the_config_through_a_link(tmp_path):
    """data/ belongs to the app: a link there must not make root write a file anywhere on the machine."""
    (tmp_path / "install" / "data").mkdir(parents=True)
    target = tmp_path / "elsewhere"
    (tmp_path / "install" / "data" / "config.yaml").symlink_to(target)
    done = write_config(tmp_path)
    assert done.returncode != 0 and "Verweis" in done.stderr
    assert not target.exists() and not (tmp_path / "install.log").exists()


def test_an_existing_config_is_kept(tmp_path):
    (tmp_path / "install" / "data").mkdir(parents=True)
    (tmp_path / "install" / "data" / "config.yaml").write_text("timezone: Europe/Vienna\n")
    done = write_config(tmp_path)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "install" / "data" / "config.yaml").read_text() == "timezone: Europe/Vienna\n"


# --- helpers next to a checked download of a release ---

def sums(**files: str) -> str:
    return "".join(f"{hashlib.sha256(text.encode()).hexdigest()}  {name}\n" for name, text in files.items())


def copy_updater(folder: Path, served: str) -> subprocess.CompletedProcess:
    install = folder / "install"
    install.mkdir(exist_ok=True)
    return run_functions(folder, f"DIR=\"{install}\"\ncurl() {{ printf '{served}'; }}\ncopy_helper updater.sh",
                         {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})  # sha256sum is in /sbin on macOS


def test_a_downloaded_helper_must_match_the_checksums_next_to_the_script(tmp_path):
    """Whoever checked install.sh against SHA256SUMS must not get a helper from the website that was not checked."""
    (tmp_path / "SHA256SUMS").write_text(sums(**{"install.sh": "x", "updater.sh": "helper of the release\n"}))
    (tmp_path / "install").mkdir()
    (tmp_path / "install" / "updater.sh").write_text("old helper\n")

    done = copy_updater(tmp_path, "something else\\n")
    assert done.returncode != 0 and "SHA256SUMS" in done.stderr
    assert (tmp_path / "install" / "updater.sh").read_text() == "old helper\n"
    assert not (tmp_path / "install" / "updater.sh.new").exists()

    done = copy_updater(tmp_path, "helper of the release\\n")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "install" / "updater.sh").read_text() == "helper of the release\n"
    assert mode(tmp_path / "install" / "updater.sh") == 0o755


def test_a_helper_missing_from_the_checksums_is_refused(tmp_path):
    (tmp_path / "SHA256SUMS").write_text(sums(**{"install.sh": "x"}))
    done = copy_updater(tmp_path, "helper\\n")
    assert done.returncode != 0 and "SHA256SUMS" in done.stderr
    assert not (tmp_path / "install" / "updater.sh").exists()


def test_a_helper_next_to_the_script_is_checked_as_well(tmp_path):
    (tmp_path / "SHA256SUMS").write_text(sums(**{"updater.sh": "helper of the release\n"}))
    (tmp_path / "updater.sh").write_text("changed helper\n")
    done = copy_updater(tmp_path, "")
    assert done.returncode != 0 and "SHA256SUMS" in done.stderr
    assert not (tmp_path / "install" / "updater.sh").exists()


# --- evcc ---

@pytest.mark.parametrize("evcc", ["yes", "no"])
def test_the_evcc_folder_is_only_for_root(tmp_path, evcc):
    """evcc keeps its database with the passwords of wallbox and vehicle there; Docker would create it readable."""
    write_compose(tmp_path, evcc, "no")
    assert (tmp_path / "evcc").exists() is (evcc == "yes")
    if evcc == "yes":
        assert mode(tmp_path / "evcc") == 0o700


def test_the_report_asks_for_the_evcc_password_right_away():
    """Until someone sets it, anyone in the home network can set the password of evcc and change it."""
    script = (ROOT / "scripts" / "install.sh").read_text()
    block = script[script.index('if grep -q "^  evcc:" docker-compose.yml'):]
    block = block[:block.index("\n  fi\n")]
    assert "Passwort für evcc festlegen" in block


# --- the login link of Tailscale ---

FAKE_TAILSCALE = """#!/bin/sh
case "$*" in
  *" serve "*) exit 1 ;;
  *"status --json"*) echo '{"BackendState": "NeedsLogin", "AuthURL": "https://login.example/a"}' ;;
  *" login "*) echo "To authenticate, visit: https://login.example/a" ;;
  *) exit 1 ;;
esac
"""


def test_only_the_app_can_read_the_pending_login_link(tmp_path):
    """Whoever opens the login link first adds the machine to their tailnet: not readable by other users of it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tailscaled").write_text("#!/bin/sh\nsleep 2\n")
    (bin_dir / "tailscale").write_text(FAKE_TAILSCALE)
    (bin_dir / "chown").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/chown.log"\n')
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    state = tmp_path / "remote"
    (state / "inbox").mkdir(parents=True)
    (state / "inbox" / "request").write_text("login")
    for old in ("status.json", "login.log"):  # from an older version of the helper
        (state / old).write_text("old\n")
        (state / old).chmod(0o644)
    done = subprocess.run(["sh", str(ROOT / "scripts" / "tailscale.sh")], capture_output=True, text=True, timeout=30,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin", "OPENAMPERE_TAILSCALE_MODE": "userspace",
                               "OPENAMPERE_REMOTE_DIR": str(state)})
    assert done.returncode == 1, done.stderr  # the stand-in tailscaled ends after 2 s: the helper ends, too

    assert "AuthURL" in (state / "status.json").read_text() and mode(state / "status.json") == 0o600
    assert f"1000:1000 {state}/status.json.tmp" in (tmp_path / "chown.log").read_text().splitlines()
    assert "https://login.example/a" in (state / "login.log").read_text() and mode(state / "login.log") == 0o600


# --- start of the Home Assistant app ---

def run_start(tmp_path: Path, *args: str, chown_works: bool = True) -> tuple[subprocess.CompletedProcess, str, str]:
    """Runs ha-app/start.sh with stand-ins; returns the result, the chown calls and the command setpriv started."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "chown").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/chown.log"\nexit {0 if chown_works else 1}\n')
    (bin_dir / "setpriv").write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/setpriv.log"\n')
    (bin_dir / "openampere").write_text("#!/bin/sh\necho /config/custom_components/openampere\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    done = subprocess.run(["sh", str(ROOT / "ha-app" / "start.sh"), *args], capture_output=True, text=True,
                          timeout=30, env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    logs = [tmp_path / "chown.log", tmp_path / "setpriv.log"]
    return done, *(log.read_text() if log.exists() else "" for log in logs)


@pytest.mark.parametrize("command", ["ha-backup-copy", "ha-backup-done"])
def test_a_backup_of_home_assistant_does_not_hand_over_the_data_folder(tmp_path, command):
    """The backup commands run as root through the start script; a failing chown -R there aborted the backup."""
    done, chowned, started = run_start(tmp_path, command, chown_works=False)
    assert done.returncode == 0, done.stderr
    assert chowned == "" and started.split()[-2:] == ["openampere", command]


def test_a_normal_start_hands_the_data_folder_to_the_app_user(tmp_path):
    done, chowned, started = run_start(tmp_path)
    assert done.returncode == 0, done.stderr
    assert chowned.split() == ["-R", "openampere:openampere", "/data"] and started.split()[-1] == "openampere"


def test_a_normal_start_stops_when_the_data_folder_cannot_be_handed_over(tmp_path):
    done, _, started = run_start(tmp_path, chown_works=False)
    assert done.returncode != 0 and started == ""
