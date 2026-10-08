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
