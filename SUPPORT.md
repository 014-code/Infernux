# Support

## Platform support

The [support matrix](docs/platform-support.json) is the shared source for the
English/Chinese README tables and wheel OS classifiers. A classifier identifies
a host build target, not a claim that every device configuration has been tested.

**0.4.2** includes Windows and Linux Editors and Players, plus Android and Web
Players. macOS, native iOS and a Headless
Player package are not supported. Headless is a Windows/Linux host mode;
iPhone/iPad browser testing concerns the Web Player, not a native iOS export.

For the exact commit `9011d80c026c56acadd02961b4b90e040d16e9e2`, the
[desktop acceptance jobs](https://github.com/ChenlizheMe/Infernux/actions/runs/38339783763)
passed both host suites and built their wheel/Hub distributions, and the
[Player acceptance jobs](https://github.com/ChenlizheMe/Infernux/actions/runs/38339783763)
passed Windows, Linux, Android, Web build and Web browser jobs.
The source run's R2 publication failed on a cached 404;
[publication recovery](https://github.com/ChenlizheMe/Infernux/actions/runs/38351978771) completed using
the same original tested distributions. Public PyPI build-2 installations also
passed headless and UI checks on Windows and Linux. These results cover the
linked revision and automated environments, not every physical device,
browser, or display configuration.

The website's [Hub catalog](docs/hub-catalog.json) independently records artifacts
for the current release. Publish the matching Windows and Linux artifacts
before deploying the catalog; CI builds alone do not publish GitHub Releases.

## Documentation first

Start with these resources:

- `README.md` for project scope, setup, and build instructions
- `README-zh.md` for the Chinese overview
- `docs/wiki.html` for the documentation hub
- `docs/wiki/site/` for generated API reference output

## Where to ask what

- Bug report: open a GitHub Issue with reproduction details.
- Feature proposal: open a GitHub Issue describing the workflow gap and proposed direction.
- Open-ended question or design discussion: use the [Infernux community](https://infernux-engine.discourse.group/).
- Security concern: follow `SECURITY.md` and report it privately.

## What helps when asking for support

Include as much of the following as possible:

- engine version or commit hash
- OS and Python version
- compiler / toolchain information
- relevant logs or screenshots
- a minimal reproduction project or script

The easier it is to reproduce your problem, the faster it is to reason about it.
