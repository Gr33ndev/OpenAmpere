# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""GHSA-6r67-5vwf-5x8x: in userspace mode Tailscale hands every connection from the tailnet to 127.0.0.1 of the
machine. With a TUN device it gets its own network interface; where that does not work, it falls back."""

import shutil
import subprocess
from pathlib import Path

import pytest

from openampere.remote import Remote
from openampere.runtime import Runtime
from openampere.storage import Storage

from test_install_script import write_compose

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.mark.parametrize(("tun", "host_tailscale", "mode"), [
    ("/dev/null", False, "kernel"),  # a character device stands in for /dev/net/tun
    ("/nonexistent/tun", False, "userspace"),  # NAS, LXC without TUN device
    ("/dev/null", True, "userspace"),  # Tailscale already runs on the machine: its interface would collide
])
def test_tailscale_gets_its_own_interface_where_the_machine_can(tmp_path, monkeypatch, tun, host_tailscale, mode):
    net = tmp_path / "net"
    net.mkdir()
    if host_tailscale:
        (net / "tailscale0").mkdir()
    monkeypatch.setenv("OPENAMPERE_TUN_DEVICE", tun)
    monkeypatch.setenv("OPENAMPERE_SYS_NET", str(net))
    service = write_compose(tmp_path, "no", "yes")["services"]["tailscale"]
    assert service["environment"]["OPENAMPERE_TAILSCALE_MODE"] == mode
    assert ("devices" in service and "NET_ADMIN" in service["cap_add"]) is (mode == "kernel")


def run_helper(tmp_path: Path, mode: str, kernel_works: bool) -> tuple[list[str], str]:
    """Runs scripts/tailscale.sh with a stand-in tailscaled; returns its --tun flags and the mode file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fail = "" if kernel_works else 'case "$1" in --tun=oa-tailscale) exit 1 ;; esac\n'
    (bin_dir / "tailscaled").write_text(f'#!/bin/sh\necho "$1" >> "{tmp_path}/args"\n{fail}sleep 2\n')
    (bin_dir / "tailscale").write_text("#!/bin/sh\nexit 1\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    done = subprocess.run(["sh", str(SCRIPTS / "tailscale.sh")], capture_output=True, text=True, timeout=30,
                          env={"PATH": f"{bin_dir}:/usr/bin:/bin", "OPENAMPERE_TAILSCALE_MODE": mode,
                               "OPENAMPERE_TUN_CHECK_S": "1", "OPENAMPERE_REMOTE_DIR": str(tmp_path / "remote")})
    assert done.returncode == 1  # the stand-in tailscaled ends after 2 s: the helper ends, too
    return (tmp_path / "args").read_text().split(), (tmp_path / "remote" / "mode").read_text().strip()


@pytest.mark.parametrize(("mode", "kernel_works", "flags", "running"), [
    ("kernel", True, ["--tun=oa-tailscale"], "kernel"),
    ("userspace", True, ["--tun=userspace-networking"], "userspace"),
    # the TUN device exists but cannot be used (rootless container, SELinux): userspace instead of no remote access
    ("kernel", False, ["--tun=oa-tailscale", "--tun=userspace-networking"], "userspace"),
])
def test_the_helper_starts_tailscale_in_the_chosen_mode_or_falls_back(tmp_path, mode, kernel_works, flags, running):
    assert run_helper(tmp_path, mode, kernel_works) == (flags, running)


@pytest.mark.parametrize(("written", "shown"), [("kernel", "kernel"), ("userspace", "userspace"), (None, None),
                                                ("something", None)])
def test_the_app_shows_the_mode_tailscale_runs_in(tmp_path, monkeypatch, written, shown):
    runtime = Runtime({}, Storage(tmp_path / "data" / "t.db"))
    monkeypatch.setattr(runtime.config.storage, "path", str(tmp_path / "data" / "t.db"))
    remote = Remote(runtime)
    remote.folder.mkdir(parents=True, exist_ok=True)
    if written:
        (remote.folder / "mode").write_text(written + "\n")
    assert remote.mode() == shown
    runtime.storage.close()
