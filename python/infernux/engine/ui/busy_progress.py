"""Independent desktop progress presentation for synchronous Editor work."""

import os
from pathlib import Path
import subprocess
import sys


class BusyProgress:
    def __init__(self):
        from infernux.lib import native_dir

        executable = Path(native_dir) / ("InfernuxEditorProgress.exe" if sys.platform == "win32"
                                         else "InfernuxEditorProgress")
        self._process = subprocess.Popen(
            [str(executable)], stdin=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def update(self, progress: float, message: str) -> None:
        self._process.stdin.write(f"{progress}\t{message}\n")
        self._process.stdin.flush()

    def close(self) -> None:
        self._process.stdin.close()
        self._process.wait(timeout=5)
