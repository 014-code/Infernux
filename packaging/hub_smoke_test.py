"""Exercise the installed Hub UI with explicit, isolated verification storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
import traceback

from PySide6.QtCore import QTimer

from database import ProjectDatabase
from hub_updater import current_hub_version
from hub_utils import HubLaunchContext


def run_startup_probe(window_type) -> int:
    """Show/render every main page and terminate through the normal Qt loop."""
    data = os.environ.get("INFERNUX_DATA_ROOT")
    shared = os.environ.get("INFERNUX_SHARED_DATA_ROOT")
    if not data or not shared:
        raise RuntimeError("Hub --smoke-test requires isolated data and shared storage roots")
    database = ProjectDatabase()
    database.set_setting("automatic_update_checks", "disabled")
    database.close()
    window = window_type(HubLaunchContext.current())
    result = {"version": current_hub_version(), "pages": [], "error": ""}
    code = 0

    def finish():
        window.request_quit()

    def visit(index=0):
        nonlocal code
        try:
            if index == window.pages.count():
                # A second geometry forces resize/layout after the first paint.
                window.resize(window.width() + 31, window.height() + 19)
                if window.grab().isNull():
                    raise RuntimeError("The Hub window did not render")
                QTimer.singleShot(100, finish)
                return
            window.pages.setCurrentIndex(index)
            if not window.isVisible() or window.grab().isNull():
                raise RuntimeError(f"Hub page {index} did not render")
            result["pages"].append(index)
            QTimer.singleShot(100, lambda: visit(index + 1))
        except Exception:
            code = 1
            result["error"] = traceback.format_exc()
            finish()

    window.show()
    QTimer.singleShot(100, visit)
    window.app.exec()
    # The external verifier also requires a clean process exit: a receipt alone
    # is insufficient if native teardown subsequently crashes.
    Path(data, "startup-probe.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return code
