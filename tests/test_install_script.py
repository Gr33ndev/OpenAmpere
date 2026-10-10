"""The docker-compose.yml that scripts/install.sh writes for an installation."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def write_compose(folder: Path, evcc: str, tailscale: str) -> dict:
    """Run write_files of the installer (without main and without downloading the helpers)."""
    functions = folder / "functions.sh"
    functions.write_text(SCRIPT.read_text().replace('\nmain "$@"\n', "\n"))
    code = f"""
source "{functions}"
SUDO="" DIR="{folder}" COMPOSE="{folder}/docker-compose.yml"
PORT=8080 TLS_PORT=8443 TZ_NAME=Europe/Berlin EVCC_CONTAINER={evcc} TAILSCALE={tailscale}
copy_helper() {{ :; }}
chown() {{ :; }}  # the folder belongs to the user of the image only on a real installation
write_files
"""
    done = subprocess.run(["bash", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return yaml.safe_load((folder / "docker-compose.yml").read_text())


@pytest.mark.parametrize(("evcc", "tailscale"), [("no", "no"), ("yes", "yes")])
def test_every_service_limits_its_log(tmp_path, evcc, tailscale):
    """Docker's default log grows without limit and fills SD cards over time (#168)."""
    services = write_compose(tmp_path, evcc, tailscale)["services"]
    expected = {"openampere", "updater"} | ({"evcc"} if evcc == "yes" else set()) | (
        {"tailscale"} if tailscale == "yes" else set())
    assert set(services) == expected
    for name, service in services.items():
        assert service["logging"] == {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}, name


def run_functions(folder: Path, code: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Runs bash code with the installer's functions loaded (without main), not interactive."""
    functions = folder / "functions.sh"
    functions.write_text(SCRIPT.read_text().replace('\nmain "$@"\n', "\n"))
    return subprocess.run(["bash", "-c", f'source "{functions}"\nSUDO=""\n{code}'], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(folder / "home"), "OPENAMPERE_YES": "1", **(env or {})})


@pytest.mark.parametrize(("given", "expected"), [("/srv/openampere/", "/srv/openampere"), ("~/openampere", "HOME/openampere"),
                                                 ("~", "HOME")])
def test_the_install_folder_is_an_absolute_path(tmp_path, given, expected):
    """#222: "~" is not expanded in an answer; compose needs an absolute path for the helpers."""
    done = run_functions(tmp_path, 'DOCKER=true\ninstall_dir', {"OPENAMPERE_DIR": given})
    assert done.returncode == 0, done.stderr
    assert done.stdout == expected.replace("HOME", str(tmp_path / "home"))


def test_a_relative_install_folder_is_refused_with_a_hint(tmp_path):
    done = run_functions(tmp_path, 'DOCKER=true\ninstall_dir', {"OPENAMPERE_DIR": "openampere"})
    assert done.returncode != 0 and "vollständigen Pfad" in done.stderr


def test_an_earlier_installation_in_another_folder_is_found(tmp_path):
    """#222: a second run must update that installation, not set up a second one in /opt/openampere."""
    earlier = tmp_path / "srv" / "oa"
    earlier.mkdir(parents=True)
    (earlier / "docker-compose.yml").write_text("services: {}\n")
    done = run_functions(tmp_path, f'docker() {{ echo "{earlier}"; }}\nDOCKER=docker\ninstall_dir')
    assert done.returncode == 0, done.stderr
    assert done.stdout == str(earlier)


def test_a_failed_download_keeps_the_helper_that_is_there(tmp_path):
    """#222: an empty or cut updater.sh made the update helper crash on every start."""
    install = tmp_path / "install"  # not next to the script copy: copy_helper downloads
    install.mkdir()
    (install / "updater.sh").write_text("old helper\n")
    done = run_functions(tmp_path, f'DIR="{install}"\ncurl() {{ printf "half"; return 22; }}\ncopy_helper updater.sh')
    assert done.returncode != 0 and "ließ sich nicht herunterladen" in done.stderr
    assert (install / "updater.sh").read_text() == "old helper\n"
    assert not (install / "updater.sh.new").exists()

    done = run_functions(tmp_path, f'DIR="{install}"\ncurl() {{ printf "new helper\\n"; }}\ncopy_helper updater.sh')
    assert done.returncode == 0, done.stderr
    assert (install / "updater.sh").read_text() == "new helper\n"
