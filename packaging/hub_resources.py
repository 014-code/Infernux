"""Resource paths for Infernux Hub (launcher).

Resolves to packaging/resources/ whether running from source or
from a Nuitka standalone bundle.
"""

import os
from hub_utils import get_bundle_dir


def _resource_dir() -> str:
    return os.path.join(get_bundle_dir(), "resources")


RESOURCE_DIR = _resource_dir()
ICON_PATH = os.path.join(RESOURCE_DIR, "icon.png")
FONT_PATH = os.path.join(RESOURCE_DIR, "PingFangSC-Regular.ttf")
