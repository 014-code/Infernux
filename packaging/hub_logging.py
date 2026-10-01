"""Persistent Hub diagnostics in the user's writable data directory."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from hub_utils import get_hub_user_data_dir


def hub_log_path() -> Path:
    return Path(get_hub_user_data_dir()) / "Logs" / "hub.log"


def configure_logging() -> None:
    path = hub_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(handler, RotatingFileHandler) and
           Path(handler.baseFilename) == path.resolve() for handler in root.handlers):
        return
    handler = RotatingFileHandler(path, maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    previous_hook = sys.excepthook

    def report_exception(exc_type, exc_value, traceback):
        root.critical("Unhandled Hub exception", exc_info=(exc_type, exc_value, traceback))
        previous_hook(exc_type, exc_value, traceback)

    sys.excepthook = report_exception
    root.info("Hub started; platform=%s Python=%s", sys.platform, sys.version.split()[0])
