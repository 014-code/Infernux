"""Staging directories whose contents will become a permanent installation."""

from pathlib import Path
import os
import stat
import sys
import tempfile
import uuid


def clear_windows_readonly_tree(root: Path) -> None:
    """Clear DOS read-only attributes on an owned installation tree once.

    This does not grant access or change ACLs. Reparse points are excluded so
    installing or repairing Hub never changes files outside its own tree.
    """
    if sys.platform != "win32":
        return
    pending = [Path(root)]
    while pending:
        path = pending.pop()
        info = path.stat(follow_symlinks=False)
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            continue
        if attributes & stat.FILE_ATTRIBUTE_READONLY:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                pending.extend(Path(entry.path) for entry in entries)


def create_publication_directory(parent: Path, prefix: str) -> Path:
    if sys.platform != "win32":
        return Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    # Python 3.13's mkdtemp uses a protected OWNER RIGHTS ACL. With an
    # elevated token its owner is Administrators, so renaming the directory
    # into place leaves ordinary users unable to access the installation.
    # A permanent tree must inherit its destination parent's permissions.
    directory = parent / (prefix + uuid.uuid4().hex)
    directory.mkdir(mode=0o777)
    return directory
