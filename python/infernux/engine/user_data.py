"""Authoritative per-user storage root for Editor and shared engine state."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from .path_utils import resolved_path


DATA_ROOT_ENV = "INFERNUX_DATA_ROOT"


def get_infernux_data_root(environ: Mapping[str, str] | None = None) -> str:
    """Return the Hub-owned data root used by source and installed Editors."""

    values = os.environ if environ is None else environ
    configured = values.get(DATA_ROOT_ENV, "").strip()
    if configured:
        return resolved_path(os.path.expandvars(os.path.expanduser(configured)))
    if os.name == "nt":
        local_app_data = values.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise RuntimeError("Infernux requires LOCALAPPDATA on Windows")
        return resolved_path(os.path.join(local_app_data, "InfernuxHub"))
    xdg_data_home = values.get("XDG_DATA_HOME", "").strip()
    base = (
        Path(xdg_data_home).expanduser()
        if xdg_data_home
        else Path.home() / ".local" / "share"
    )
    return resolved_path(base / "InfernuxHub")


def get_infernux_shared_data_root(environ: Mapping[str, str] | None = None) -> str:
    """Resolve shared installs from explicit configuration or the Hub's local record."""
    values = os.environ if environ is None else environ
    configured = values.get("INFERNUX_SHARED_DATA_ROOT", "").strip()
    if configured:
        return resolved_path(os.path.expandvars(os.path.expanduser(configured)))
    user_root = get_infernux_data_root(values)
    record = Path(user_root) / "hub-shared-root.json"
    try:
        with record.open(encoding="utf-8") as source:
            configured = json.load(source)["shared_data_root"]
    except FileNotFoundError:
        return user_root
    if not isinstance(configured, str) or not Path(configured).is_absolute():
        raise ValueError(f"Invalid Hub shared data root in {record}")
    return resolved_path(configured)


def get_project_editor_layout_root(project_root: str | os.PathLike[str]) -> str:
    """Return the machine-local Editor layout directory owned by a project."""

    return resolved_path(os.path.join(project_root, "Cache", "Editor", "Layout"))


__all__ = [
    "DATA_ROOT_ENV",
    "get_infernux_data_root",
    "get_infernux_shared_data_root",
    "get_project_editor_layout_root",
]
