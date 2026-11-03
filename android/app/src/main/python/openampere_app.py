# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Started by ServerService in the app's server process (#567): the unchanged server of OpenAmpere, with its data in
the app's private storage."""
import os
import sys

PORT = 8080  # fixed: the WebView of the app opens it (ServerService.PORT)


def run(files_dir: str, lang: str | None = None) -> str | None:
    """Runs the server until it stops. Returns None when it stopped as asked (e.g. a restart after a restored backup,
    where the other ways run the same command again), or the reason it could not run, for the owner: the messages of
    SystemExit are German sentences that say what to do, translated into the tablet's language (lang)."""
    os.environ["OPENAMPERE_ANDROID"] = "1"
    os.environ["OPENAMPERE_CONFIG"] = os.path.join(files_dir, "config.yaml")
    os.environ["OPENAMPERE_STORAGE_PATH"] = os.path.join(files_dir, "openampere.db")
    os.environ["OPENAMPERE_SERVER_PORT"] = str(PORT)
    sys.argv = ["openampere"]
    from openampere import android
    from openampere.__main__ import main

    try:
        android.check_port(PORT)
        main()
    except SystemExit as stop:
        return android.stop_reason(stop, lang)
    return None
