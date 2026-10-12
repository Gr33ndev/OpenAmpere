# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The files Home Assistant reads for the app (#300) fit the code and the release."""

import re
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

import pytest
import yaml

from openampere import ha_app

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "ha-app" / "config.yaml").read_text())


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_the_supervisor_finds_only_the_app():
    """The Supervisor reads every config.json/yaml/yml outside dot folders and rootfs as an app."""
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    apps = [f for f in files if re.fullmatch(r"config\.(json|ya?ml)", PurePosixPath(f).name)
            and not any(part.startswith(".") or part == "rootfs" for part in PurePosixPath(f).parts)]
    assert apps == ["ha-app/config.yaml"]
    repository = yaml.safe_load((ROOT / "repository.yaml").read_text())
    assert repository["maintainer"] == "Gr33ndev"  # otherwise Home Assistant shows "unknown" (#307)


def test_version_is_the_one_of_the_release():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert CONFIG["version"] == version  # scripts/release.sh sets both
    assert re.search(r'^version: "[^"]+"$', (ROOT / "ha-app" / "config.yaml").read_text(), re.M)  # what it replaces


def test_image_ports_and_backup_fit_the_app():
    assert CONFIG["image"].endswith("/openampere-ha-app")  # the image of the Dockerfile stage ha-app, not of Docker
    assert set(CONFIG["arch"]) == {"aarch64", "amd64"}
    assert CONFIG["ingress"] is True and CONFIG["ingress_port"] == 8080  # the web server inside the container
    assert ha_app.WEB_PORT in CONFIG["ports"]  # read back for the address of direct access
    assert "host_network" not in CONFIG
    assert CONFIG["backup_pre"] == "ha-app-start ha-backup-copy"
    assert CONFIG["backup_post"] == "ha-app-start ha-backup-done"
    excluded = [p for p in CONFIG["backup_exclude"] if PurePosixPath(f"/data/{ha_app.BACKUP_COPY}").match(p)]
    assert excluded == []  # the copy itself must be in the backup
    assert any(PurePosixPath("/data/openampere.db").match(p) for p in CONFIG["backup_exclude"])


def test_port_descriptions_exist_in_every_language():
    for path in (ROOT / "ha-app" / "translations").glob("*.yaml"):
        assert set(yaml.safe_load(path.read_text())["network"]) == set(CONFIG["ports"]), path.name


def test_the_app_can_install_its_integration():
    """App users need no HACS: the image has the integration of its version and the app may write into the config
    folder of Home Assistant (#300)."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert f"COPY custom_components/openampere {ha_app.INTEGRATION_SOURCE}" in dockerfile
    assert {"type": "homeassistant_config", "read_only": False} in CONFIG["map"]
    assert CONFIG["homeassistant_api"] is True  # the notice to restart Home Assistant


def test_the_apparmor_profile_can_be_renamed_by_the_supervisor():
    """Own AppArmor profile: +1 in the security rating (#300). The Supervisor reads the one line starting with
    "profile <name>" and puts the slug of the app in place of the name; more than one such line is refused."""
    lines = (ROOT / "ha-app" / "apparmor.txt").read_text().splitlines()
    assert len([line for line in lines if re.match(r"^profile ([^ ]+).*$", line)]) == 1
    assert "#include <abstractions/base>" in lines[lines.index(next(x for x in lines if x.startswith("profile"))) + 1]


def test_openampere_runs_without_the_config_folder_of_home_assistant():
    """Only the start step as root installs the integration into the config folder of Home Assistant. Everything that
    runs as the app user starts through setpriv (start script, health check) and gets the profile "server", which
    has no access to it: a weakness in OpenAmpere must not reach Home Assistant (#300)."""
    profile = (ROOT / "ha-app" / "apparmor.txt").read_text()
    assert "/usr/bin/setpriv cx -> server," in profile
    server = profile[profile.index("profile server"):]
    assert "deny /homeassistant/ rw," in server and "deny /homeassistant/** rwlkmx," in server
    assert "capability chown" not in server and "capability dac_override" not in server
    assert "exec setpriv " in (ROOT / "ha-app" / "start.sh").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    stage = dockerfile[dockerfile.index("FROM app AS ha-app"):dockerfile.index("# --- Docker installation")]
    assert 'HEALTHCHECK' in stage and 'CMD ["setpriv"' in stage
