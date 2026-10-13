"""Launch the actual frozen Hub with a clean environment before publication."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def verify_startup(hub: Path, expected_version: str, *, output: Path) -> dict:
    executable = hub.resolve() / ("Infernux Hub.exe" if os.name == "nt" else "Infernux Hub")
    if not executable.is_file():
        raise FileNotFoundError(executable)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="infernux-hub-startup-") as temporary:
        root = Path(temporary)
        env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            env.pop(key, None)
        if os.name == "nt":
            # A developer's Qt/Python DLLs must not repair a broken distribution.
            windows = Path(os.environ["SystemRoot"])
            env["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
            env["QT_QPA_PLATFORM"] = "windows"
        else:
            env["QT_QPA_PLATFORM"] = "xcb"
        env["INFERNUX_DATA_ROOT"] = str(root / "user")
        env["INFERNUX_SHARED_DATA_ROOT"] = str(root / "shared")
        env["PYTHONFAULTHANDLER"] = "1"
        try:
            result = subprocess.run(
                [str(executable), "--smoke-test"], cwd=root, env=env,
                capture_output=True, timeout=60,
            )
        finally:
            # Retain crash evidence even when the process exits before a receipt.
            logs = root / "user/Logs"
            if logs.is_dir():
                for log in logs.glob("*.log"):
                    (output / log.name).write_bytes(log.read_bytes())
        (output / "stdout.txt").write_bytes(result.stdout)
        (output / "stderr.txt").write_bytes(result.stderr)
        if result.returncode != 0:
            raise RuntimeError(f"Packaged Hub exited with {result.returncode}; diagnostics: {output}")
        receipt = json.loads((root / "user/startup-probe.json").read_text(encoding="utf-8"))
        if receipt != {"version": expected_version, "pages": [0, 1, 2, 3], "error": ""}:
            raise RuntimeError(f"Packaged Hub did not complete startup: {receipt}")
        (output / "startup-probe.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(f"Packaged Hub {expected_version}: four pages rendered, resized, and exited cleanly")
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify_startup(args.hub, args.version, output=args.output)
