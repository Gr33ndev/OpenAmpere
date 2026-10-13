# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""GHSA-3grw-j32m-8gcj: the docker-compose.yml of the repository (manual installation) uses the same folders for the
root helpers as the one install.sh writes. The helpers run as root, the app owns data/ and could place links there, so
no helper may get a folder below ./data (GHSA-2j3r-6fcx-9m7v)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
INSTALL = ROOT / "scripts" / "install.sh"
# a commented-out line of YAML: a key (`tailscale:`, `image: …`) or a list entry (`- ./data:/data`)
COMMENTED_YAML = re.compile(r"^(\s*)# ( *(?:[a-z_]+:(?: .*)?|- .*))$")


def enabled(text: str) -> dict:
    """The repository compose file with every optional service switched on, as the comments in it tell owners to."""
    lines = [COMMENTED_YAML.sub(r"\1\2", line) for line in text.splitlines()]
    return yaml.safe_load("\n".join(lines))


def install_compose(folder: Path) -> dict:
    """The compose file install.sh writes with evcc and Tailscale (without main and without downloading helpers)."""
    functions = folder / "functions.sh"
    functions.write_text(INSTALL.read_text().replace('\nmain "$@"\n', "\n"))
    code = f"""
source "{functions}"
SUDO="" DIR="{folder}" COMPOSE="{folder}/docker-compose.yml"
PORT=8080 TLS_PORT=8443 TZ_NAME=Europe/Berlin EVCC_CONTAINER=yes TAILSCALE=yes
copy_helper() {{ :; }}
chown() {{ :; }}
write_files
"""
    done = subprocess.run(["bash", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return yaml.safe_load((folder / "docker-compose.yml").read_text())


def helper_binds(services: dict, data: tuple[str, ...]) -> list[tuple[str, str]]:
    """Bind sources of the services that run as root (all but the app) that lie in the app's data folder."""
    return [(name, volume) for name, service in services.items() if name != "openampere"
            for volume in service.get("volumes", []) if volume.split(":")[0].rstrip("/").startswith(data)]


def test_the_repository_compose_file_keeps_the_helpers_out_of_the_data_folder():
    services = enabled(COMPOSE.read_text())["services"]
    assert {"openampere", "evcc", "tailscale"} <= set(services)
    assert helper_binds(services, ("./data", "data")) == []
    # the same exchange folder as install.sh: root-owned ./remote, seen by the app as /data/remote
    assert "./remote:/remote" in services["tailscale"]["volumes"]
    assert "./remote:/data/remote" in services["openampere"]["volumes"]


def test_the_repository_compose_file_says_how_to_create_the_exchange_folder():
    """Docker would create ./remote for root without an inbox, then the app could not ask the helper anything."""
    text = COMPOSE.read_text()
    assert "sudo mkdir -p remote/inbox" in text
    assert "sudo chown 1000:1000 remote/inbox" in text


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_installer_and_the_repository_use_the_same_helper_folders(tmp_path):
    installed = install_compose(tmp_path)["services"]
    assert helper_binds(installed, ("./data", f"{tmp_path}/data")) == []
    repository = enabled(COMPOSE.read_text())["services"]
    for name in ("evcc", "tailscale"):
        folders = {volume for volume in installed[name]["volumes"] if volume.startswith("./") and ".sh:" not in volume}
        assert folders <= set(repository[name]["volumes"]), name
    assert "./remote:/data/remote" in installed["openampere"]["volumes"]
