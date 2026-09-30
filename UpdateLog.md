# Infernux v0.4.1 · Worlds, Compute and Live Tools

0.4.1 brings larger worlds, live Python authoring and target-aware computation into the same engine workflow. This release expands multi-scene editing, prefab authoring, model import, rendering and audio, then carries their resource and component lifecycles into exported Players. It also rebuilds key parts of the plugin experience in response to community reports.

Infernux is working toward a **Neural Network-Native Engine (3N)**. Here, “Neural Network” is one concept: neural networks should eventually participate directly in gameplay and simulation. 041 builds the world, compute and tooling foundations for that direction; it does not claim that the full neural inference and tensor runtime is already delivered.

[简体中文更新日志](UpdateLog-zh.md)

**Baseline for comparison:** [`v0.4.0...v0.4.1`](https://github.com/ChenlizheMe/Infernux/compare/v0.4.0...v0.4.1)

### Multi-scene Worlds and Gameplay

- **Author several scenes together.** Open scenes additively, choose the active scene, inspect scene ownership in the Hierarchy, and move objects between scenes without treating the editor document as one global world.
- **Make scene transitions coherent.** Commit scene changes at safe points, update component membership and lifecycle plans, and replace the rendered world together with its UI. Fix transitions that changed interface content while leaving old GameObjects visible.
- **Preserve the right state.** Keep persistent objects across loads, isolate Edit and Play state, and release scene-owned resources and callbacks on unload.
- **Exercise larger scenes.** Expand Labv2 with interactive examples of CPU JIT, models and materials, cameras, lighting, world/screen UI, physics and multi-scene operations. Its XPBD soft-body example (`GPUJelly.py`) is a project script demonstrating the compute, physics-query and mesh APIs, not a built-in engine component or a general-purpose soft-body solver.

### Prefab Authoring

- **Work in isolated Prefab Mode.** Edit prefab contents independently of scene instances, including nested prefabs and variants.
- **Apply and revert precisely.** Track property and structural overrides, including object/component additions, removals and references, against stable source identities and saved baselines.
- **Keep instances synchronized.** Propagate source edits through loaded scenes and variant relationships while preserving authored overrides; retain the same relationships after save and reopen.
- **Undo shared-asset edits.** Connect prefab editing and scene changes to the editor transaction and journal infrastructure instead of leaving shared assets outside Undo/Redo.

### Models, Materials and Animation

- **Import FBX and Blender sources through one model workflow.** Organize settings into Model, Rig, Animation and Materials pages, with explicit Apply and worker-based import rather than repeating expensive work while browsing settings.
- **Preserve source structure and identity.** Keep node-local mesh geometry and hierarchy, stable mesh/subasset references, and external-source synchronization across supported renames and reparenting. Preserve user overrides when the source changes.
- **Control materials and textures.** Extract, find and remap materials; retain embedded textures and external texture references as persistent assets; carry PBR factors, texture sampling and alpha/shadow behavior through import.
- **Control mesh shading.** Add normal/tangent import policies, smoothing-angle and weighted-normal handling, and MikkTSpace tangent generation.
- **Prepare animated assets.** Improve skeleton and skin-weight import, generic/humanoid rig settings, trimmed clips, morph target names and animation asset cooking. Preview imported animation in an isolated GPU-skinned view using the same clip assets.
- **Inspect imported content.** Expose structured model/subasset inventory to Python and MCP, and make imported resources easier to inspect and reuse.

### GUID Resources and Player Content

- **Use one internal asset identity.** Extend GUID references through scenes, prefabs, material shaders, camera target textures, UI assets, effects and build settings. Resolve controlled authoring paths into that identity before runtime delivery.
- **Cook the actual dependency graph.** Include resources reachable from the current documents and package components, preserve required raw runtime files and licenses, and reject uncooked model dependencies instead of relying on editor files being present.
- **Keep project and package access explicit.** Improve package asset indexing and `Application.asset_path` / `Application.package_path` handling, including Windows path aliases.
- **Repair asset command timing.** Run confirmed deletion through the editor command lifecycle after modal interaction; improve DataAsset authoring, inspection and deferred asset operations.

### CPU/GPU Compute and JIT Preparation

- **Integrate compute into the engine.** Move the internal Taichi computation path into the engine and retire the separate integration path; keep Python as the authoring interface for CPU and GPU work.
- **Prepare before interaction.** Connect startup, Play entry, script updates and target builds to warmup discovery. Reuse valid prepared results and report preparation timings rather than recompiling every time Play starts.
- **Strengthen CPU execution.** Improve LLVM specialization, buffer lifetime and alias handling, detached compilation and proven row-local parallel work, with observable compilation/optimization results.
- **Keep GPU work resident.** Improve compute buffers, kernel dispatch, queue ownership and storage bindings; reuse readback ownership and avoid unchanged transfers where supported.
- **Build for the target.** Prepare native GPU payloads for supported targets, including Android. For supported kernels on Web, prepare CPU execution during the build because the native Taichi GPU runtime is unavailable there; projects keep the same authored compute entry points.
- **Make boundaries explicit.** Validate supported kernel and closure contracts during preparation. This is not unrestricted Taichi support in the browser, and Android GPU computation does not require a CPU fallback.

### Physics Queries and Lifecycle

- **Scale scene queries.** Add batched ray work and shared query snapshots with explicit world generations, while reducing repeated compound lookup and body locking.
- **Broaden collider queries.** Improve static mesh closest-point queries and the handling of rotated, scaled and non-convex shapes; invalidate query state when collider cooking replaces geometry.
- **Keep callbacks and tools in the same world.** Tighten contact/impulse delivery and scene ownership, and use consistent scene data for runtime queries and editor inspection.

### Rendering and RenderGraph

- **Align Vulkan and WebGPU contracts.** Correct fullscreen resource binding, storage buffers, reflection/descriptor layouts and resource updates across the two graphics paths. Fix initial scene state that could leave the browser background different from later scene loads.
- **Scope rendering resources to the view.** Improve per-view RenderGraph buffers, camera light lists, shadow resources and deferred normal/geometry coverage, so multiple views do not share the wrong frame state.
- **Extend custom passes.** Support GUID texture imports and compute-buffer inputs in fullscreen passes, and improve material property, custom sampler and texture-update handling.
- **Retire resources in order.** Correct render-resource replacement and teardown, including scene changes and device shutdown, and initialize forward resources before dependent shadow variants.

### Cameras, Text and UI

- **Separate gameplay from observation.** Gameplay camera queries and camera-facing world UI use the game camera. Scene view rendering, navigation and selection rays continue to use the editor view's camera.
- **Author world UI explicitly.** Expose fixed or game-camera-facing orientation, constant screen-size behavior and on-top/occlusion policies. Keep world UI Inspector controls appropriate to world-space content.
- **Make what you see selectable.** Align rendered text, local UI bounds, rectangle handles and click picking; improve enlarged world text clarity and engine-font use.
- **Keep screen UI isolated.** Correct canvas placement and scene ancestry interactions, and improve custom UI material/shader resources and descriptor ownership.
- **Refine physical cameras and Gizmos.** Improve sensor, lens and gate-fit behavior, plus icon scale, tint, billboard orientation and picking.
- **Make editor panels fit.** Shrink the Game view scale slider before hiding FPS, remove excess toolbar spacing, localize layout controls and avoid duplicated visible Hierarchy IDs.

### Audio Runtime

- **Stream without decoding everything up front.** Add file-backed audio streaming with bounded buffering alongside resident PCM clips, and separate audio runtime ownership from editor-only services.
- **Control voices and buses.** Improve voice lifetime, spatial gain/panning, bus/master gain and parameter automation, with output meters and streaming diagnostics.
- **Define the real-time boundary.** Keep audio callback state bounded and publish control changes through explicit handoff. Establish a native output DSP stage; this is not a finished user-facing effects graph.

### Plugins, Components and Hot Reload

- **Retain familiar package roles.** Keep Runtime, Editor and general files, folder-context-menu export to `.inxpkg`, and the repository `package/` + `package.py` workflow.
- **Register Player types before scene construction.** Include packaged and locally authored runtime components in the cooked type registry and run runtime preloads in the proper order. Player startup does not discover host build tools or activate editor-only exporters.
- **Update existing behavior.** Reload Runtime/Editor components and their dependency modules transactionally, replacing behavior on live instances rather than retaining an old `update` implementation after a save. Reject a failed candidate before publishing a mixed module state.
- **Own the whole plugin lifetime.** Track panels, menus, commands, shortcuts, subscriptions and cleanup callbacks through activation, reload, disable and unload, including failed preload cleanup.
- **Load dependencies once when appropriate.** Allow preload to import substantial Python dependencies and manage reversible services; identify native extensions that require an editor restart when their owning package changes.
- **Place tools where authors need them.** Support custom panel placement and arbitrarily nested menu paths. Treat author-provided menu segments as text; use explicit localization keys for engine menus and plugin-owned editor translation catalogs.
- **Teach the complete workflow.** Update the online plugin template and `plugin_pages` tutorials with directory roles, component registration, preload cleanup, localization and File Manager packaging instructions.

### Editor Reliability and Authoring

- **Improve window startup and resizing.** Keep the editor hidden until its loading sequence is ready and fix resize/maximize handling reported on Windows.
- **Make inspection more useful.** Improve read-only/transient Inspector fields, component publication, localized camera/light settings and Console selection, copying and source navigation.
- **Make hot editing observable.** Improve deferred task errors, scene failure reporting, frame/compute diagnostics and MCP inspection so a successful launch is not mistaken for correct simulation.
- **Refresh learning material.** Recheck tutorial parameters and API usage against the implementation, expand hands-on examples, and publish bilingual, versioned release history on the website.

### Builds, Platforms and Hub

- **Give each target its own settings.** Let Windows, Linux, Web and Android exporters declare their build options through a shared editor interface.
- **Present a ready first frame.** Repair initialization in fresh export directories, packed resource startup and scene switching; delay Player presentation until the initial scene is ready to avoid premature black frames.
- **Keep desktop window semantics predictable.** Use borderless desktop mode for fullscreen and client/render-area dimensions for windowed mode, retaining the configured windowed resolution and taskbar behavior.
- **Follow the active Linux window backend.** Enable the Vulkan surface extensions required by SDL's actual X11/Wayland backend without forcing fullscreen or changing Player dimensions. Address dependency nullability warning noise at the dependency boundary.
- **Tighten mobile/browser input and lifecycle.** Improve Android surface pause/resume, native IME input, Back-to-Escape routing, same-frame touch delivery and persistent user-data placement; align browser touch and screen UI event ownership.
- **Ship matching runtime payloads.** Keep native asset/audio ownership consistent across library boundaries and bundle required Python extensions. Use owned dependency forks and checked-in source fixes instead of editing dependency sources during CMake configuration.
- **Improve Hub-managed delivery.** Expand managed Blender/build-tool integration and release catalog synchronization. Keep published versions monotonic and preserve actual audited Linux wheel tags rather than forcing a compatibility label.

### Upgrade Notes and Scope

- **Update engine and platform plugins together.** A precompiled Player payload must match its engine contract. Rebuild exported Players after upgrading rather than copying a new engine library into an old export.
- **Review plugin roles and lifecycle.** Put shipped components/services in Runtime and panels/local web tools/exporters in Editor. Follow the current template for cleanup, translations and `plugin_pages`; archive role directories are lowercase `runtime`/`editor`, while local authored Runtime/Editor folders retain their spelling.
- **Reimport and recook from source.** Preserve your project sources and GUID metadata, then regenerate imported/cooked output with the new engine. The release strengthens the current data path rather than maintaining every experimental intermediate format.
- **Understand compute portability.** Web's supported CPU preparation path differs from native GPU execution. Kernel support is checked at build time; numerical/performance identity across devices is not promised.
- **Keep the roadmap separate.** Full neural model deployment, the tensor data plane, batch worlds and deterministic replay remain future work. The deferred vk-torch delay optimization and a complete theme system are not part of 041.

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
