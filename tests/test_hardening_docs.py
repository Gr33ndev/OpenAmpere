# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The documented commands match what the release workflow and the install script really do (#359)."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["README.md", "README.de.md"])
def test_the_ha_app_image_check_names_the_attested_image(name):
    """The release workflow attests the app image as <image>-ha-app; the README shows how to check it."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "subject-name: ${{ steps.image.outputs.name }}-ha-app" in workflow
    assert "gh attestation verify oci://ghcr.io/gr33ndev/openampere-ha-app:<version> --owner Gr33ndev" in \
        (ROOT / name).read_text()


@pytest.mark.parametrize("name", ["README.md", "README.de.md"])
def test_the_manual_install_protects_the_data_folder_like_the_install_script(name):
    assert 'chmod 700 "$DIR/data"' in (ROOT / "scripts" / "install.sh").read_text()
    assert "mkdir -p data && sudo chown 1000:1000 data && sudo chmod 700 data" in (ROOT / name).read_text()
