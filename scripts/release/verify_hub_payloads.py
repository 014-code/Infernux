"""Check that installer input and update archive contain the same signed Hub."""
import argparse
import hashlib
from pathlib import Path
import zipfile


def verify(hub: Path, update: Path, installer_payload: Path) -> None:
    name = 'Infernux Hub.exe'
    expected = hashlib.sha256((hub / name).read_bytes()).digest()
    for path in (update, installer_payload):
        with zipfile.ZipFile(path) as archive:
            if archive.namelist().count(name) != 1:
                raise ValueError(f'Expected exactly one Hub executable in {path}')
            with archive.open(name) as stream:
                actual = hashlib.file_digest(stream, 'sha256').digest()
            if actual != expected:
                raise ValueError(f'Hub executable differs in {path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub', type=Path, required=True)
    parser.add_argument('--update', type=Path, required=True)
    parser.add_argument('--installer-payload', type=Path, required=True)
    args = parser.parse_args()
    verify(args.hub, args.update, args.installer_payload)
    print('Installer input and update archive contain the identical Hub executable')
