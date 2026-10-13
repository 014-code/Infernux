"""Staging directories whose contents will become a permanent installation."""

from pathlib import Path
import sys
import tempfile
import uuid


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
