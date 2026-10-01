# Hub installation diagnostics

## Logs

Open **Settings → Open Hub Logs**. `hub.log` records catalog requests and full
installation exceptions. Logs rotate at 4 MiB and retain three previous files.

Default locations:

- Windows: `%LOCALAPPDATA%\InfernuxHub\Logs\hub.log`
- Linux: `${XDG_DATA_HOME:-$HOME/.local/share}/InfernuxHub/Logs/hub.log`
- If `INFERNUX_DATA_ROOT` is set: `<INFERNUX_DATA_ROOT>/Logs/hub.log`

Include the failed operation, Hub version, OS/architecture, and the corresponding
exception when reporting a problem. Review logs for private local paths before
posting publicly. This logging is available in builds containing this change;
the original 0.4.1 Hub did not write these persistent diagnostics.

## No available engine versions

The catalog combines PyPI and GitHub Releases. A failed request is logged with
its source; when neither source nor a cached catalog is available, the dialog
shows an error rather than claiming no versions exist. Error text is selectable
and rendered literally, including `<urlopen error ...>`.
After correcting the connection, use **Retry** without restarting the Hub.

The exception distinguishes TLS certificate verification, proxy/connection
failures, HTTP failures, and filesystem permissions. Switching KDE/other desktop
environments is not a diagnosis of these failures. Do not disable TLS verification.
The install page also accepts a locally downloaded compatible engine wheel.

## Blender authoring support

This is an optional Blender installation used by the Editor to convert `.blend`
assets, not an engine component or Player dependency. The importer currently
supports Blender 5.2; the Hub-managed release is 5.2.2.

The Editor selects its executable in this order:

1. Explicit Blender path in Editor preferences.
2. Existing Hub-managed executable supplied by `INFERNUX_BLENDER_EXECUTABLE`.
3. The operating system's default `.blend` application.

Windows uses the Shell association API, including the user's default application.
Linux queries `xdg-mime` for `application/x-blender` and resolves the desktop entry
using XDG data-directory precedence. Quoted native executable paths are supported.
Launchers requiring extra arguments (for example `flatpak run ...`) cannot be used
as a native executable: select a native Blender 5.2 executable in preferences.
Clearing the explicit preference restores automatic selection. An explicit invalid
selection is not silently replaced; the import error must be corrected.

Installation distinguishes download from extraction. Windows extraction uses
extended paths, without requiring a system-wide long-path policy change. A verified
download is retained if installation fails, and cleanup errors do not replace the
original failure. Its path is recorded in the log. Downloads live under the shared
resources directory shown in Hub settings, in `Cache/Downloads/Blender`.

For an immediate workaround with an older Hub, extract the official Blender 5.2
archive into a writable, short directory and set its `blender.exe` (Windows) or
native `blender` executable (Linux) in Editor preferences. If extraction still
fails, collect the exact filename/error and check destination permissions and free
space; do not infer the cause solely from the desktop environment.
