# Infernux v0.4.1 · Worlds, Compute and Live Tools

041 brings multi-scene workflows, deeper model import, CPU/GPU computation and a stronger plugin foundation together. It advances the game-engine foundations of a Neural Network-Native Engine (3N): worlds you can author, simulate, inspect and ship from Python.

**Development notes:** these entries describe the 041 branch. Published downloads keep their existing release version until 0.4.1 is released.

[简体中文更新日志](UpdateLog-zh.md)

**Baseline for comparison:** [`v0.4.0...041/prepare_for_my_game_1kg`](https://github.com/ChenlizheMe/Infernux/compare/v0.4.0...041/prepare_for_my_game_1kg)

### Scenes and Gameplay

- Edit multiple scenes in one workspace, choose the active scene, load and unload scenes additively, and move objects across scene boundaries.
- Preserve persistent objects during scene changes and improve isolation between Edit and Play state, scene ownership, lifecycle callbacks and teardown.
- Strengthen scene transitions in exported Players, including resource release and replacement of the rendered world alongside UI.
- Expand Labv2 into a multi-scene playground for materials, models, physics, CPU JIT, GPUJelly, cameras, lighting and UI.

### Models, Animation and Assets

- Expand FBX and Blender import workflows with mesh splitting, material and texture assignment, animation handling and external source synchronization.
- Improve imported subasset identity, material remapping, animation clip workflows, collider generation and model inspection.
- Extend GUID-based resource identity through project and package indexing, asset cooking and packed Player delivery.
- Improve DataAsset authoring and inspection, package asset visibility, and deferred asset commands; asset deletion now follows the editor command lifecycle beyond the confirmation dialog.

### Compute and Simulation

- Integrate CPU/GPU JIT preparation with startup, Play entry, script changes and target builds; reuse valid compiled results and record warmup timing.
- Prepare supported compute work before first scene interaction and carry target-appropriate compiled payloads into Player builds.
- Retain GPU computation on supported native targets, including Android. Prepare supported kernels for CPU execution on Web, where the native Taichi GPU runtime is unavailable.
- Improve GPUJelly simulation, collision behavior and scene lifecycle handling across native and browser paths.
- Keep Jolt physics, collision callbacks, scene queries and Gizmo inspection connected to the same scene data.

### Rendering, Cameras and UI

- Improve Vulkan/WebGPU rendering consistency, including initial scene presentation, camera state, lighting, shadows and resource updates.
- Separate gameplay camera queries from editor camera access. World UI facing policies use the game camera while each view still renders through its own camera.
- Align world text rendering, local selection bounds and click picking; expose fixed and game-camera-facing orientation in the Inspector.
- Improve text clarity when enlarged, engine font use, world UI controls and layout labels; simplify unused width/height strategy controls.
- Make the Game toolbar adapt to narrow panels, reduce the slider before hiding FPS, and remove excess spacing.
- Address duplicate Hierarchy item IDs, editor resize/maximize handling and startup window visibility.

### Plugins and Hot Reload

- Keep Runtime, Editor and general-file authoring roles, with folder-context-menu packaging to .inxpkg and explicit runtime-only Player content.
- Build and publish the Player component type registry before scene construction, including locally authored package components and runtime preloads.
- Refresh Runtime/Editor components and dependency modules transactionally, updating behavior on existing instances instead of leaving old class implementations active.
- Tie panels, commands, shortcuts, subscriptions and other plugin resources to their preload lifetime; clean up on failed activation, reload, disable and unload.
- Add cleanup registration for reversible services and automatic restart requirements for newly loaded native extensions; allow large Python dependencies to load once during preload.
- Support custom panel placement and nested menu paths. Display author-provided menu text literally, with explicit localization for engine menus and plugin-owned translation catalogs.
- Update the online plugin template and in-editor plugin_pages tutorials with package structure, lifecycle, localization, component registration and folder packaging instructions.

### Builds, Players and Platforms

- Let each Windows, Linux, Android and Web exporter declare its own build options through a shared editor interface.
- Improve first launch from a newly exported directory, packed asset initialization and scene switching; remove premature black startup windows and initial black presentation frames.
- Treat desktop fullscreen as borderless desktop mode and retain configured windowed resolution semantics. Windowed dimensions describe the client/render area.
- Enable the Vulkan surface extensions required by the active SDL window backend on Linux, without forcing Player fullscreen or changing configured dimensions.
- Reduce third-party nullability-warning noise at its dependency boundary and keep dependency changes in their owned repositories.
- Update platform runtime payloads and package dependencies together with the engine; remove configure-time source patching from the affected build paths.

### Upgrade Notes

- Update platform plugins with the engine so their packaged native runtime and Player contracts match.
- Use Runtime for shipped components and services; keep panels, local web tools and build exporters in Editor. Player startup does not discover host build tools.
- Follow the current plugin template for preload cleanup, translations and plugin_pages. Package archive role directories use lowercase runtime/editor; existing authored Runtime/Editor folders retain their spelling.
- Neural model deployment, the full tensor data plane, batch worlds and deterministic replay remain roadmap work. The deferred vk-torch delay optimization is not part of this delivery.

---

# Infernux v0.4.0 · Multiplatform Builds and Distribution

0.4.0 adds Windows, Linux, Android, and Web Player builds, expands InxPackage authoring and runtime asset delivery, and puts shared build environments under Hub management.

[简体中文更新日志](UpdateLog-zh.md)

**Baseline for comparison:** [`v0.3.7...v0.4.0`](https://github.com/ChenlizheMe/Infernux/compare/v0.3.7...v0.4.0)

### Multiplatform

- Build Windows and Linux Editors and Players, Android APK/AAB packages, and Web Players from the shared build service.
- Deliver precompiled Players, runtimes, and target tools in the four platform plugins; normal exports require no engine checkout, submodules, CMake, or native engine compilation.
- Run native targets on Vulkan and browser targets on WebGPU, including Python gameplay, rendering, input, UI, audio, and particles.
- Exercise the same MultiPlatform040 project across targets, including button-triggered reads of packaged text assets.
- Add Windows/Linux desktop and platform Player CI builds, plus Web browser acceptance.

### Plugins and Assets

- Package only a repository's `package/` directory using a standalone `package.py`; keep README and language-specific build configuration outside the archive.
- Preserve direct local-folder authoring and generate default package metadata from the output filename when no manifest is supplied.
- Include scripts, materials, shaders, and arbitrary runtime files in plugins, with explicit asset metadata and live refresh under `Packages/`.
- Keep Player assets in `Content.inxpkg` instead of expanding the project's authoring directory tree. Resolve engine asset paths through cooked GUID identities and provide filesystem access for payloads that need it.
- Separate editor-only content from Player payloads and distribute platform build support as optional packages.
- Check compatible GitHub releases and explicitly select plugin updates while preserving GUIDs, enabled state, and user-added files; require consent before replacing local edits.
- Refresh the independent official catalog without upgrading project-pinned packages, and resolve former platform sources to their independent repositories.

### Hub and Build Environments

- Provide Windows and Linux Hub distributions and managed Python 3.13 environments.
- Install Android support from the Hub channel on Windows and Linux, sharing SDK, NDK, JDK, Gradle, and target Python dependencies; supply toolchain paths automatically and require installed support before enabling Android plugin import.
- Reuse downloads through the Hub Library while keeping project build caches inside the project.
- Group engine, Python, and Android installations into tabs under Installs, with background jobs, a compact progress strip, an expandable queue, and system-tray support.
- Keep interrupted Android kit downloads in Hub's shared cache so an explicitly restarted installation resumes the download; remove the download cache after successful installation.

---

# Infernux v0.3.7 · Plugins and Skeletal Animation

0.3.7 adds the InxPackage plugin system, moves MCP out of the engine core, and fixes skeletal animation imported from separate FBX files.

[简体中文更新日志](UpdateLog-zh.md)

**Baseline for comparison:** [`v0.3.6...v0.3.7`](https://github.com/ChenlizheMe/Infernux/compare/v0.3.6...v0.3.7)

### Plugins

- Install from a `.inxpkg`, a local folder, Git, or the official list.
- `Runtime/` ships with the game. `Editor/` stays in the Editor.
- Package docs come from `README.md`, `LICENSE`, and `InxPluginPages/`.
- MCP moved to the default official plugin `infernux/mcp`. New projects include it. You can disable or uninstall it.

### Skeletal Animation

- Retarget animation-only FBX files through exact joint identities while accepting Assimp-generated pivot and helper nodes between mapped joints.
- Reject renamed or structurally incompatible rigs instead of guessing from geometry and silently driving the wrong limbs.

### Editor and Authoring

- Use the Plugins window to install, enable, disable, reload, and uninstall packages with visible progress from download through activation.
- Keep Headless and MCP authoring on the same scene, command, permission, and undo paths used by the Editor.

---

# Infernux v0.3.6 · Unified Hierarchy and Hub Updates

Version 0.3.6 unifies the editor Hierarchy and restores automatic update discovery for packaged Infernux Hub builds.

**Baseline for comparison:** [`v0.3.5...v0.3.6`](https://github.com/ChenlizheMe/Infernux/compare/v0.3.5...v0.3.6)

### Editor

- Removed the separate Hierarchy UI mode. Scene and UI objects now share one tree, one selection model, and one context-menu path.
- Preserved Canvas root placement and UI Screen subtree constraints in the unified Hierarchy.

### Hub Updates

- Restored startup update checks in packaged Nuitka Hub builds.
- Kept the Install Engine Version list on the selected Hub theme instead of inheriting the desktop system palette.
- Added verified full-package update fallback and elevation for protected installation directories.
- Changed the release pipeline to publish independently installable full Hub packages instead of incremental patch assets.
