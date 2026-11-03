# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Started by ServerService in the app's process (#567): the unchanged server of OpenAmpere, with its data in the
app's private storage."""
import os
import sys

PORT = "8080"  # fixed: the WebView of the app opens it (ServerService.PORT)


def run(files_dir: str) -> None:
    """Runs the server until it stops. The app then starts it again in a new process (also after a restored backup,
    where the other ways run the same command again)."""
    os.environ["OPENAMPERE_ANDROID"] = "1"
    os.environ["OPENAMPERE_CONFIG"] = os.path.join(files_dir, "config.yaml")
    os.environ["OPENAMPERE_STORAGE_PATH"] = os.path.join(files_dir, "openampere.db")
    os.environ["OPENAMPERE_SERVER_PORT"] = PORT
    sys.argv = ["openampere"]
    from openampere.__main__ import main

    main()
