from __future__ import annotations

import importlib.machinery
import importlib.util

from test_game_builder import (
    BuildOutputDirectoryError,
    GameBuilder,
    NuitkaBuilder,
    Path,
    RuntimeArtifactError,
    _asset_index_entry,
    _bind_staged_script_to_asset_index,
    _collect_fixture_dependencies,
    _fnv1a64,
    _install_runtime_identity_bindings,
    _make_builder,
    _make_project,
    _player_executable_name,
    _prepare_runtime_catalog_inputs,
    _read_runtime_catalog,
    _scene_with_component,
    _write_asset_index,
    _write_asset_script,
    _write_player_executable,
    _write_scene_material_audio_reachability_fixture,
    artifact_source_hash,
    build_catalog,
    forbidden_player_service_modules,
    game_builder_module,
    json,
    logical_type_for_path,
    nuitka_builder_module,
    payload_kind_for,
    py_compile,
    pytest,
    read_entry,
    read_manifest,
    sys,
    write_pack,
    _native_package_backend as _native_package_backend,
)

def test_source_checkout_shader_keeps_identity_when_wheel_builds_project(
    tmp_path, monkeypatch
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    checkout_resources = (
        tmp_path / "source-checkout" / "python" / "infernux" / "resources"
    )
    installed_resources = (
        tmp_path / "project-venv" / "site-packages" / "infernux" / "resources"
    )
    shader = checkout_resources / "shaders" / "particle_sprite.vert"
    shader.parent.mkdir(parents=True, exist_ok=True)
    shader.write_text(
        'ShaderInfo { Name "Particle Sprite" Capabilities [ParticleSprite] }\n',
        encoding="utf-8",
    )
    installed_resources.mkdir(parents=True, exist_ok=True)
    scene.write_text("{}\n", encoding="utf-8")
    scene_entry = _asset_index_entry(project, scene, "scene-guid", "", "Scene")
    shader_entry = _asset_index_entry(
        project,
        shader,
        "particle-shader-guid",
        "",
        "Shader",
    )
    shader_entry["read_only"] = True
    shader_entry["metadata"]["metadata"]["shader_id"] = {
        "type": "string",
        "value": "Particle Sprite",
    }
    monkeypatch.setattr(
        game_builder_module._resources,
        "get_package_resources_path",
        lambda: str(installed_resources),
    )
    monkeypatch.setattr(
        game_builder_module._resources,
        "resources_path",
        str(installed_resources),
    )
    entries = [scene_entry, shader_entry]
    _write_asset_index(project, entries)
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    selected = builder._collect_library_asset_entries(entries)

    assert set(selected) == {"scene-guid", "particle-shader-guid"}
    builder._copy_cooked_assets(str(tmp_path / "copy" / "Data"))
    assert "particle-shader-guid" in builder._cooked_asset_entries
    builder._cooked_asset_entries = {
        "particle-shader-guid": selected["particle-shader-guid"]
    }
    builder._runtime_artifact_bindings = {}
    final_dir = tmp_path / "dist"
    builder._write_runtime_asset_records(str(final_dir))
    records = json.loads(
        (final_dir / "Data" / "Library" / "RuntimeAssetRecords.json").read_text(
            encoding="utf-8"
        )
    )
    shader_record = next(
        item for item in records["entries"] if item["guid"] == "particle-shader-guid"
    )
    assert shader_record["runtime_path"] == (
        "Library/Resources/shaders/particle_sprite.vert"
    )
    assert shader_record["runtime_artifacts"][0]["runtime_path"] == (
        "infernux/resources/shaders/particle_sprite.vert"
    )


def test_cooked_python_component_keeps_script_and_runtime_guid_identity(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    script = project / "Assets" / "Scripts" / "Mover.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("class Mover:\n    pass\n", encoding="utf-8")
    script_guid = "1234567890abcdef1234567890abcdef"
    scene.write_text(json.dumps(_scene_with_component({}, type_id=f"python:{script_guid}:type-guid:Scripts.Mover:Mover")), encoding="utf-8")
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
            _asset_index_entry(project, script, script_guid, "", "Script"),
        ],
    )
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )
    final_dir = tmp_path / "dist"
    data_dir = final_dir / "Data"

    builder._copy_cooked_assets(str(data_dir))
    assert (data_dir / "Assets" / "Scripts" / "Mover.py").is_file()

    builder._runtime_artifact_bindings = {}
    builder._runtime_artifact_source_paths = set()
    builder._stage_library_runtime_documents(str(data_dir))
    builder._compile_user_scripts(str(final_dir))
    builder._write_runtime_asset_records(str(final_dir))

    assert not (data_dir / "Assets" / "Scripts" / "Mover.py").exists()
    assert (data_dir / "Assets" / "Scripts" / "Mover.pyc").is_file()
    guid_map = json.loads((data_dir / "_script_guid_map.json").read_text(encoding="utf-8"))
    assert guid_map[script_guid].replace("\\", "/") == "Assets/Scripts/Mover.pyc"
    records = json.loads(
        (data_dir / "Library" / "RuntimeAssetRecords.json").read_text(encoding="utf-8")
    )
    script_record = next(item for item in records["entries"] if item["guid"] == script_guid)
    assert script_record["runtime_path"] == "Assets/Scripts/Mover.pyc"
    expected_artifact_id = game_builder_module.runtime_artifact_id(
        "Content.inxpkg", "Assets/Scripts/Mover.pyc"
    )
    assert set(records) == {"$schema", "entries"}
    assert script_record["primary_runtime_artifact_id"] == expected_artifact_id
    assert script_record["runtime_artifact_ids"] == [expected_artifact_id]
    assert builder._runtime_asset_identity_bindings["Assets/Scripts/Mover.pyc"][
        "source_guid"
    ] == script_guid
    assert str(project).replace("\\", "/") not in json.dumps(records, ensure_ascii=False)


def test_runtime_asset_records_preserve_compiled_sprite_metadata(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    texture = project / "Assets" / "Sprites" / "Sheet.png"
    texture.parent.mkdir(parents=True, exist_ok=True)
    texture.write_bytes(b"runtime texture")
    texture_guid = "fedcba0987654321fedcba0987654321"
    entry = _asset_index_entry(project, texture, texture_guid, "", "Texture")
    entry["metadata"]["metadata"].update(
        {
            "width": {"type": "int", "value": 128},
            "height": {"type": "int", "value": 64},
            "texture_type": {"type": "string", "value": "sprite"},
            "sprite_frames": {
                "type": "json_array",
                "value": [
                    {
                        "stable_id": "1" * 32,
                        "name": "Hero",
                        "x": 32,
                        "y": 16,
                        "w": 32,
                        "h": 16,
                        "pivot_x": 0.5,
                        "pivot_y": 0.5,
                    }
                ],
            },
        }
    )
    builder._cooked_asset_entries = {texture_guid: entry}
    builder._runtime_artifact_bindings = {
        f"Library/Artifacts/Textures/{texture_guid}.inxtex": {
            "source_guid": texture_guid,
        }
    }
    builder._runtime_artifact_source_paths = set()
    final_dir = tmp_path / "dist"

    builder._write_runtime_asset_records(str(final_dir))

    records = json.loads(
        (final_dir / "Data" / "Library" / "RuntimeAssetRecords.json").read_text(
            encoding="utf-8"
        )
    )
    metadata = records["entries"][0]["metadata"]["metadata"]
    assert metadata["texture_type"]["value"] == "sprite"
    assert metadata["sprite_frames"]["value"][0]["name"] == "Hero"
    assert not any(final_dir.rglob("*.meta"))


def test_runtime_asset_records_reject_missing_compiled_metadata(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    texture = project / "Assets" / "Broken.png"
    texture.parent.mkdir(parents=True, exist_ok=True)
    texture.write_bytes(b"runtime texture")
    entry = _asset_index_entry(project, texture, "broken-guid", "", "Texture")
    entry["metadata"] = {}
    builder._cooked_asset_entries = {"broken-guid": entry}
    builder._runtime_artifact_bindings = {
        "Library/Artifacts/Textures/broken-guid.inxtex": {
            "source_guid": "broken-guid",
        }
    }
    builder._runtime_artifact_source_paths = set()

    with pytest.raises(RuntimeError, match="Refusing to discard the \\.meta sidecar"):
        builder._write_runtime_asset_records(str(tmp_path / "dist"))


def test_cooked_python_component_includes_imported_project_helper(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    scripts = project / "Assets" / "Scripts" / "Voxel"
    component = scripts / "VoxelContinentGenerator.py"
    helper = scripts / "VoxelPipeline.py"
    unused = scripts / "Unused.py"
    scripts.mkdir(parents=True, exist_ok=True)
    component.write_text(
        "from Scripts.Voxel.VoxelPipeline import register_voxel_effects\n"
        "class VoxelContinentGenerator:\n"
        "    pass\n",
        encoding="utf-8",
    )
    helper.write_text("def register_voxel_effects():\n    return True\n", encoding="utf-8")
    unused.write_text("UNUSED = True\n", encoding="utf-8")
    component_guid = "component-script-guid"
    helper_guid = "helper-script-guid"
    scene.write_text(
        json.dumps(
            {
                "component": (
                    f"python:{component_guid}:type-guid:"
                    "Scripts.Voxel.VoxelContinentGenerator:VoxelContinentGenerator"
                )
            }
        ),
        encoding="utf-8",
    )
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
            _asset_index_entry(project, component, component_guid, "", "Script"),
            _asset_index_entry(project, helper, helper_guid, "", "Script"),
            _asset_index_entry(project, unused, "unused-script-guid", "", "Script"),
        ],
    )
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    selected = builder._collect_library_asset_entries(builder._asset_index_entries())

    assert set(selected) == {
        "scene-guid",
        component_guid,
        helper_guid,
        "unused-script-guid",
    }


def test_cooked_python_component_resolves_relative_helper_import(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    scripts = project / "Assets" / "Scripts" / "Gameplay"
    component = scripts / "Mover.py"
    helper = scripts / "movement.py"
    scripts.mkdir(parents=True, exist_ok=True)
    component.write_text("from . import movement\n", encoding="utf-8")
    helper.write_text("SPEED = 3.0\n", encoding="utf-8")
    component_guid = "relative-component-guid"
    helper_guid = "relative-helper-guid"
    scene.write_text(
        json.dumps(
            {
                "component": (
                    f"python:{component_guid}:type-guid:Scripts.Gameplay.Mover:Mover"
                )
            }
        ),
        encoding="utf-8",
    )
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
            _asset_index_entry(project, component, component_guid, "", "Script"),
            _asset_index_entry(project, helper, helper_guid, "", "Script"),
        ],
    )
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    selected = builder._collect_library_asset_entries(builder._asset_index_entries())

    assert set(selected) == {"scene-guid", component_guid, helper_guid}


def test_cooked_python_component_includes_literal_project_assets(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    script = project / "Assets" / "Scripts" / "Voxel.py"
    material = project / "Assets" / "Materials" / "Voxel.mat"
    cache = project / "Assets" / "Data" / "Voxel.npy"
    unused = project / "Assets" / "Data" / "Unused.npy"
    script.parent.mkdir(parents=True, exist_ok=True)
    material.parent.mkdir(parents=True, exist_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        'MATERIAL = "Assets/Materials/Voxel.mat"\n'
        'CACHE = "Assets/Data/Voxel.npy"\n',
        encoding="utf-8",
    )
    material.write_text("{}", encoding="utf-8")
    cache.write_bytes(b"npy")
    unused.write_bytes(b"unused")
    script_guid = "voxel-script-guid"
    scene.write_text(
        json.dumps(
            {
                "component": (
                    f"python:{script_guid}:type-guid:Scripts.Voxel:Voxel"
                )
            }
        ),
        encoding="utf-8",
    )
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
            _asset_index_entry(project, script, script_guid, "", "Script"),
            _asset_index_entry(project, material, "material-guid", "", "Material"),
            _asset_index_entry(project, cache, "cache-guid", "", "Binary"),
            _asset_index_entry(project, unused, "unused-guid", "", "Binary"),
        ],
    )
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    selected = builder._collect_library_asset_entries(builder._asset_index_entries())

    assert set(selected) == {
        "scene-guid",
        script_guid,
        "material-guid",
        "cache-guid",
        "unused-guid",
    }


def test_complete_assets_cook_does_not_use_script_literals_as_content_roots(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    script = project / "Assets" / "Scripts" / "Broken.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('CACHE = "Assets/Data/Missing.npy"\n', encoding="utf-8")
    script_guid = "broken-script-guid"
    scene.write_text(
        json.dumps(
            {
                "component": (
                    f"python:{script_guid}:type-guid:Scripts.Broken:Broken"
                )
            }
        ),
        encoding="utf-8",
    )
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
            _asset_index_entry(project, script, script_guid, "", "Script"),
        ],
    )
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    selected = builder._collect_library_asset_entries(builder._asset_index_entries())

    assert set(selected) == {"scene-guid", script_guid}


def test_player_type_registry_is_derived_from_script_ast_without_execution(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    script_guid = "1234567890abcdef1234567890abcdef"
    records = builder._runtime_component_type_records(
        "from infernux.components import *\n"
        "class Mover(InxComponent):\n"
        "    def awake(self):\n"
        "        raise RuntimeError('must not execute during build')\n"
        "    def update(self, delta_time):\n"
        "        pass\n"
        "    def _infernux_startup_warmup(self):\n"
        "        return True\n",
        script_guid=script_guid,
        runtime_path="Assets/Scripts/mover.pyc",
    )

    assert len(records) == 1
    assert records[0]["module"] == "Scripts.mover"
    assert records[0]["qualname"] == "Mover"
    assert records[0]["lifecycle"] == ["awake", "update"]
    assert records[0]["startup_warmup"] is True
    assert records[0]["type_id"].startswith(f"python:{script_guid}:")


def test_player_build_seals_startup_warmups_for_target_platform(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    builder._runtime_platform = "android"
    data_dir = tmp_path / "build_output" / "Data"
    registry = data_dir / "Library" / "StartupWarmup.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema": "infernux.startup-warmup",
                "platform": "windows",
                "entries": {
                    "Scripts.Compute:Compute._infernux_startup_warmup": {
                        "platform": "windows",
                        "status": "ready",
                        "source": "C:/author/project/Assets/Scripts/Compute.py",
                        "source_sha256": "obsolete",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    builder._prepare_startup_warmup_registry(str(data_dir))

    document = json.loads(registry.read_text(encoding="utf-8"))
    entry = next(iter(document["entries"].values()))
    assert document["platform"] == "android"
    assert entry["platform"] == "android"
    assert "source" not in entry
    assert "source_sha256" not in entry


def test_player_type_registry_cooks_published_component_semantics(tmp_path):
    from infernux.components import InxComponent, serialized_field
    from infernux.components.component_identity import bind_asset_script_guid

    output_dir = tmp_path / "build_output"
    script_path = output_dir / "Data" / "Assets" / "Scripts" / "mover.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text(
        "from infernux.components import InxComponent, serialized_field\n"
        "class SemanticMover(InxComponent):\n"
        "    speed: float = serialized_field(default=3.0, range=(0.0, 8.0))\n",
        encoding="utf-8",
    )
    script_guid = "1234567890abcdef1234567890abcdef"

    class SemanticMover(InxComponent):
        speed: float = serialized_field(default=3.0, range=(0.0, 8.0))

    SemanticMover.__qualname__ = "SemanticMover"
    type_guid = bind_asset_script_guid(SemanticMover, script_guid)
    builder = _make_builder(tmp_path, output_dir)
    _bind_staged_script_to_asset_index(
        builder,
        output_dir,
        script_path,
        guid=script_guid,
    )

    builder._compile_user_scripts(str(output_dir))

    document = json.loads(
        (output_dir / "Data" / "Library" / "RuntimeTypeRegistry.json").read_text(
            encoding="utf-8"
        )
    )
    semantic = document["types"][0]["semantic"]
    assert semantic["type_guid"] == type_guid
    assert semantic["owner"] == f"script:{script_guid}"
    assert semantic["runtime_profiles"] == ["player"]
    assert semantic["fields"][0]["property_path"] == "SemanticMover.speed"
    assert semantic["fields"][0]["attributes"]["default"] == 3.0
    assert semantic["fields"][0]["attributes"]["range"] == [0.0, 8.0]


@pytest.mark.parametrize("base_expression", ["Parent", "base_module.Parent"])
def test_player_component_inheritance_keeps_cross_script_identity(tmp_path, monkeypatch, base_expression):
    from types import ModuleType

    from infernux.components.component_identity import bind_asset_script_guid
    from infernux.engine.runtime_type_registry import (
        clear_runtime_type_registry,
        install_runtime_type_registry,
    )
    from infernux.lib import _Infernux as native

    package = ModuleType("player_inheritance")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, package.__name__, package)
    sources = {
        "player_inheritance.base": (
            "from infernux import InxComponent, serialized_field\n"
            "class Parent(InxComponent):\n"
            "    speed: float = serialized_field(default=3.0, field_id='motion.speed')\n"
            "    def start(self):\n"
            "        raise AssertionError('cook must not invoke lifecycle methods')\n"
        ),
        "player_inheritance.child": (
            "from player_inheritance.base import Parent\n"
            "import player_inheritance.base as base_module\n"
            f"class Child({base_expression}):\n"
            "    strength: float = 5.0\n"
            "    def update(self, dt):\n"
            "        raise AssertionError('cook must not invoke lifecycle methods')\n"
        ),
    }
    output = tmp_path / "build_output"
    builder = _make_builder(tmp_path, output)
    for index, (name, source) in enumerate(sources.items()):
        module = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        monkeypatch.setattr(package, name.rsplit(".", 1)[1], module, raising=False)
        exec(compile(source, name, "exec"), module.__dict__)
        value_type = module.Parent if index == 0 else module.Child
        guid = f"component-script-{index}"
        bind_asset_script_guid(value_type, guid)
        script = output / "Data" / "Assets" / (name.replace(".", "/") + ".py")
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(source, encoding="utf-8")
        _bind_staged_script_to_asset_index(builder, output, script, guid=guid)

    builder._compile_user_scripts(str(output))
    registry_path = output / "Data" / "Library" / "RuntimeTypeRegistry.json"
    records = {record["qualname"]: record for record in json.loads(registry_path.read_text())["types"]}
    assert set(records) == {"Parent", "Child"}
    parent, child = records["Parent"], records["Child"]
    assert child["script_guid"] == "component-script-1"
    assert child["lifecycle"] == ["update"]
    assert parent["lifecycle"] == ["start"]
    assert child["semantic"]["base_type_guid"] == parent["type_guid"]
    assert [field["attributes"]["field_id"] for field in child["semantic"]["fields"]] == [
        "motion.speed", "strength",
    ]
    try:
        assert install_runtime_type_registry(str(registry_path)) == 2
        descriptor = native._semantic_catalog_snapshot().type_document(child["type_guid"])
        assert descriptor["base_type_guid"] == parent["type_guid"]
    finally:
        clear_runtime_type_registry()


def test_player_type_registry_cooks_published_data_asset_semantics(tmp_path):
    from infernux.components import serialized_field
    from infernux.core.data_asset import DataAsset

    class BalanceConfig(DataAsset):
        __serialized_type_id__ = "tests.player.balance-config"
        gravity: float = serialized_field(default=9.8, range=(0.0, 20.0))

    # Project modules are identified by their stable Assets-relative module
    # path.  Rebind this test class to the same identity a loaded project
    # module would expose; the published SerializableObject registry remains
    # the authority queried by the builder.
    BalanceConfig.__module__ = "Scripts.balance"
    BalanceConfig.__qualname__ = "BalanceConfig"
    output_dir = tmp_path / "build_output"
    script_path = output_dir / "Data" / "Assets" / "Scripts" / "balance.py"
    script_path.parent.mkdir(parents=True)
    script_path.write_text(
        "from infernux import DataAsset, serialized_field\n"
        "class BalanceConfig(DataAsset):\n"
        "    __serialized_type_id__ = 'tests.player.balance-config'\n"
        "    gravity: float = serialized_field(default=9.8, range=(0.0, 20.0))\n",
        encoding="utf-8",
    )
    script_guid = "1234567890abcdef1234567890abcdef"
    builder = _make_builder(tmp_path, output_dir)
    _bind_staged_script_to_asset_index(
        builder,
        output_dir,
        script_path,
        guid=script_guid,
    )

    builder._compile_user_scripts(str(output_dir))
    document = json.loads(
        (output_dir / "Data" / "Library" / "RuntimeTypeRegistry.json").read_text(
            encoding="utf-8"
        )
    )
    records = document["types"]

    assert len(records) == 1
    record = records[0]
    assert record["kind"] == "data"
    assert record["data_asset"] is True
    assert record["type_id"] == "python:data:tests.player.balance-config"
    assert "schema_version" not in record["semantic"]
    assert record["semantic"]["fields"][0]["property_path"] == "BalanceConfig.gravity"
    assert record["semantic"]["fields"][0]["attributes"]["default"] == 9.8


@pytest.mark.parametrize("split_modules", [False, True])
@pytest.mark.parametrize("data_base,explicit_ids", [
    ("SerializableObject", False), ("SerializableObject", True), ("DataAsset", True),
])
def test_player_data_inheritance_survives_cook_and_catalog_publication(
    tmp_path, monkeypatch, split_modules, data_base, explicit_ids,
):
    from types import ModuleType

    from infernux.components import serializable_object
    from infernux.engine.runtime_type_registry import (
        clear_runtime_type_registry,
        install_runtime_type_registry,
    )
    from infernux.lib import _Infernux as native

    monkeypatch.setattr(
        serializable_object, "_SERIALIZABLE_REGISTRY",
        dict(serializable_object._SERIALIZABLE_REGISTRY),
    )
    base_module = "Scripts.player_base"
    child_module = "Scripts.player_derived" if split_modules else base_module
    base_source = (
        f"from infernux import {data_base}, serialized_field\n"
        f"class BaseConfig({data_base}):\n"
        + ("    __serialized_type_id__ = 'tests.player.base'\n" if explicit_ids or data_base == "DataAsset" else "")
        + "    speed: float = serialized_field(default=3.0, field_id='motion.speed')\n"
        "class IntermediateConfig(BaseConfig):\n"
        + ("    __serialized_type_id__ = 'tests.player.middle'\n" if data_base == "DataAsset" else "")
        + "    weight: float = 2.0\n"
    )
    child_source = (
        "class DerivedConfig(IntermediateConfig):\n"
        + ("    __serialized_type_id__ = 'tests.player.derived'\n" if explicit_ids else "")
        + "    strength: float = 5.0\n"
    )
    sources = {base_module: base_source}
    if split_modules:
        sources[child_module] = f"from {base_module} import IntermediateConfig\n" + child_source
    else:
        sources[base_module] += child_source

    output = tmp_path / "build_output"
    builder = _make_builder(tmp_path, output)
    for index, (name, source) in enumerate(sources.items()):
        if name.startswith("Scripts.") and "Scripts" not in sys.modules:
            monkeypatch.setitem(sys.modules, "Scripts", ModuleType("Scripts"))
        module = ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(source, name, "exec"), module.__dict__)
        script = output / "Data" / "Assets" / (name.replace(".", "/") + ".py")
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(source, encoding="utf-8")
        _bind_staged_script_to_asset_index(builder, output, script, guid=f"data-script-{index}")

    builder._compile_user_scripts(str(output))
    registry_path = output / "Data" / "Library" / "RuntimeTypeRegistry.json"
    records = {record["qualname"]: record for record in json.loads(registry_path.read_text())["types"]}
    assert set(records) == {"BaseConfig", "IntermediateConfig", "DerivedConfig"}
    base, intermediate, derived = (records[name] for name in ("BaseConfig", "IntermediateConfig", "DerivedConfig"))
    assert intermediate["semantic"]["base_type_guid"] == base["type_guid"]
    assert derived["semantic"]["base_type_guid"] == intermediate["type_guid"]
    assert "schema_version" not in derived["semantic"]
    assert derived["semantic"]["owner"] == f"script:data-script-{int(split_modules)}"
    assert [field["attributes"]["field_id"] for field in derived["semantic"]["fields"]] == [
        "motion.speed", "weight", "strength",
    ]
    try:
        assert install_runtime_type_registry(str(registry_path)) == 3
        descriptor = native._semantic_catalog_snapshot().type_document(derived["type_guid"])
        assert descriptor["base_type_guid"] == intermediate["type_guid"]
        assert descriptor["fields"] == derived["semantic"]["fields"]
    finally:
        clear_runtime_type_registry()


def test_payload_manifest_rejects_indexed_asset_outside_build_scene_closure(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    sources = _write_scene_material_audio_reachability_fixture(
        builder,
        include_unreachable=True,
    )
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(builder, final_dir)
    write_pack(
        (
            ("Assets/Main.scene", sources["scene"]),
            ("Assets/Materials/Bird.mat", sources["material"]),
            ("Assets/Audio/Wing.wav", sources["audio"]),
            ("Assets/Unused.mat", sources["unreachable"]),
        ),
        data_root / builder._CONTENT_ARCHIVE_FILENAME,
    )
    _install_runtime_identity_bindings(
        builder,
        {
            "Assets/Main.scene": (
                "scene-guid",
                "runtime_loader_requires_serialized_document",
            ),
            "Assets/Materials/Bird.mat": (
                "material-guid",
                "runtime_loader_requires_serialized_document",
            ),
            "Assets/Audio/Wing.wav": (
                "audio-guid",
                "runtime_audio_backend_requires_encoded_stream",
            ),
            "Assets/Unused.mat": (
                "unused-guid",
                "runtime_loader_requires_serialized_document",
            ),
        },
    )

    with pytest.raises(RuntimeError, match="Assets/Unused.mat"):
        builder._write_payload_manifest(str(final_dir))


def test_copy_stage_uses_all_indexed_assets_before_content_pack(tmp_path, monkeypatch):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    sources = _write_scene_material_audio_reachability_fixture(
        builder,
        include_unreachable=True,
    )
    sources["unreachable"].with_suffix(".mat.meta").write_text(
        "{}",
        encoding="utf-8",
    )
    unindexed = Path(builder.project_path) / "Assets" / "Dynamic" / "Runtime.bin"
    unindexed.parent.mkdir(parents=True)
    unindexed.write_bytes(b"unindexed payload")
    final_dir = tmp_path / "dist"

    builder._copy_game_data(str(final_dir))
    builder._write_runtime_asset_records(str(final_dir))

    staged = final_dir / "Data"
    assert (staged / "Assets" / "Main.scene").is_file()
    assert (staged / "Assets" / "Materials" / "Bird.mat").is_file()
    assert (staged / "Assets" / "Audio" / "Wing.wav").is_file()
    assert (staged / "Library" / "Artifacts" / "Document" / "scene-guid.scene").is_file()
    assert (staged / "Library" / "Artifacts" / "Document" / "material-guid.inxdoc").is_file()
    assert (staged / "Library" / "Artifacts" / "Audio" / "audio-guid.wav").read_bytes() == (staged / "Assets/Audio/Wing.wav").read_bytes()
    assert (staged / "Assets" / "Unused.mat").exists()
    assert not (staged / "Assets" / "Unused.mat.meta").exists()
    assert not (staged / "Assets" / "Dynamic" / "Runtime.bin").exists()

    _write_player_executable(final_dir)
    builder._organize_player_layout(str(final_dir))
    data_root = final_dir / "TestGame_Data"
    runtime_source = tmp_path / "runtime.bin"
    runtime_source.write_bytes(b"runtime")
    write_pack(
        (("infernux/resources/runtime.bin", runtime_source),),
        data_root / builder._RUNTIME_ARCHIVE_FILENAME,
    )
    builder._pack_content_archive(str(final_dir))
    (data_root / "BuildManifest.json").write_text(
        json.dumps({"game_name": builder.project_name, "scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    def reject_reopen(_package_path, entry_path):
        raise AssertionError(f"current-build catalog payload reopened from package: {entry_path}")

    monkeypatch.setattr(game_builder_module, "read_entry", reject_reopen)
    builder._write_payload_manifest(str(final_dir))

    content_names = {
        entry["path"]
        for entry in read_manifest(data_root / builder._CONTENT_ARCHIVE_FILENAME)["files"]
    }
    assert {
        "Library/Artifacts/Document/scene-guid.scene",
        "Library/Artifacts/Document/material-guid.inxdoc",
        "Library/Artifacts/Audio/audio-guid.wav",
    } <= content_names
    assert "Assets/Main.scene" not in content_names
    assert "Assets/Materials/Bird.mat" not in content_names
    assert "Assets/Audio/Wing.wav" not in content_names
    assert "Assets/Unused.mat" not in content_names
    assert "Assets/Dynamic/Runtime.bin" not in content_names
    assert (data_root / builder._ASSET_CATALOG_ARCHIVE_FILENAME).is_file()
    catalog = _read_runtime_catalog(data_root, builder)
    assert not {
        artifact["payload_kind"]
        for artifact in catalog["artifacts"]
    }.intersection({"serialized_runtime_document", "direct_runtime_asset"})


@pytest.mark.parametrize("owner", ["Assets/Rendering", "Packages/author/monitor/Runtime"])
def test_render_texture_cook_ships_guid_binary_description_not_authoring_source(tmp_path, owner):
    from infernux.lib import _Infernux as native

    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    source = project / owner / "CameraTarget.rendertexture"
    source.parent.mkdir(parents=True, exist_ok=True)
    desc = native._RenderTextureDesc()
    desc.relative_size = True
    desc.width_scale = desc.height_scale = 0.5
    source.write_text(native._render_texture_description_to_json(desc), encoding="utf-8")
    if owner.startswith("Packages/"):
        (source.parent.parent / "inx_package.json").write_text(
            json.dumps({"reference": "author/monitor", "name": "Monitor", "version": "1.0.0"}),
            encoding="utf-8",
        )
        Path(str(source) + ".meta").write_text(json.dumps({
            "metadata": {"guid": {"type": "string", "value": "e" * 32}}
        }), encoding="utf-8")
    scene = project / "Assets" / "Main.scene"
    guid = "e" * 32
    artifact_path = f"Library/Artifacts/RenderTexture/{guid}.inxrtex"
    artifact = project / artifact_path
    artifact.parent.mkdir(parents=True)
    source_hash = _fnv1a64(source.read_bytes())
    payload = native._encode_render_texture_artifact(desc, source_hash)
    artifact.write_bytes(payload)
    entry = _asset_index_entry(project, source, guid, artifact_path, "RenderTexture")
    _write_asset_index(project, [_asset_index_entry(project, scene, "scene-guid", "", "Scene"), entry])
    final_dir = tmp_path / "dist"
    builder._copy_game_data(str(final_dir))
    builder._write_runtime_asset_records(str(final_dir))
    assert (final_dir / "Data" / artifact_path).read_bytes() == payload
    _write_player_executable(final_dir)
    builder._organize_player_layout(str(final_dir))
    builder._pack_content_archive(str(final_dir))
    data_root = final_dir / "TestGame_Data"
    package = data_root / builder._CONTENT_ARCHIVE_FILENAME
    names = {item["path"] for item in read_manifest(package)["files"]}
    assert artifact_path in names
    assert not any(name.endswith(".rendertexture") for name in names)
    assert not any(name.startswith(("Assets/", "Packages/")) for name in names)
    cooked = native._decode_render_texture_artifact(read_entry(package, artifact_path))
    assert cooked.relative_size and cooked.width_scale == 0.5
    assert artifact_source_hash(artifact) == source_hash
    assert logical_type_for_path(artifact_path) == "render_texture_artifact"
    assert payload_kind_for(logical_type_for_path(artifact_path)) == "compiled_artifact"


@pytest.mark.parametrize(
    ("suffix", "magic"),
    ((".inxmesh", b"INXMESHART"), (".inxskin", b"INXSKINAR")),
)
def test_current_mesh_artifact_requires_its_format_marker(tmp_path, suffix, magic):
    artifact = tmp_path / f"obsolete{suffix}"
    artifact.write_bytes(
        magic
        + b"\x04\x03\x02\x01"
        + (16).to_bytes(4, "little")
        + b"0123456789abcdef"
    )

    with pytest.raises(RuntimeArtifactError, match="current format marker"):
        artifact_source_hash(artifact)


@pytest.mark.parametrize("suffix,resource_type", [
    (".rendertexture", "RenderTexture"), (".jpg", "Texture"), (".png", "Texture"), (".svg", "Texture"),
])
def test_texture_missing_artifact_cannot_ship_source_as_blob(tmp_path, suffix, resource_type):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    source = project / "Assets" / ("Camera" + suffix)
    source.write_text("{}", encoding="utf-8")
    _write_asset_index(project, [
        _asset_index_entry(project, project / "Assets" / "Main.scene", "scene-guid", "", "Scene"),
        _asset_index_entry(project, source, "e" * 32, "", resource_type),
    ])
    with pytest.raises(RuntimeError, match="raw texture source without a compiled artifact"):
        builder._copy_game_data(str(tmp_path / "dist"))


def test_cooked_document_and_audio_paths_are_compiled_artifacts():
    expected = {
        "Library/Artifacts/Document/scene-guid.scene": "scene_artifact",
        "Library/Artifacts/Document/clip-guid.animclip3d": "animation_clip_3d_artifact",
        "Library/Artifacts/Document/timeline-guid.animtimeline": "animation_timeline_artifact",
        "Library/Artifacts/Audio/audio-guid.wav": "audio_artifact",
        "Library/Artifacts/Blob/blob-guid.bin": "project_runtime_blob_artifact",
        "Library/Artifacts/Mesh/mesh-guid.inxmesh": "mesh_artifact",
    }
    for runtime_path, logical_type in expected.items():
        assert logical_type_for_path(runtime_path) == logical_type
        assert payload_kind_for(logical_type) == "compiled_artifact"


@pytest.mark.parametrize("path", ["Assets/Meshes/slope.inxmesh", "Packages/demo/Meshes/slope.inxmesh"])
def test_authored_native_mesh_is_not_classified_as_a_cooked_artifact(path):
    assert logical_type_for_path(path) == "model_source"
    assert payload_kind_for(logical_type_for_path(path)) != "compiled_artifact"


@pytest.mark.parametrize(
    "path",
    [
        "Assets/Models/scene.blend",
        "Assets/Models/scene.fbx",
        "Packages/demo/Models/scene.glb",
    ],
)
def test_interchange_model_sources_share_runtime_model_classification(path):
    assert logical_type_for_path(path) == "model_source"
    assert payload_kind_for(logical_type_for_path(path)) != "compiled_artifact"


def test_button_background_texture_is_a_guid_backed_player_cook_dependency():
    from infernux.core.asset_ref import TextureRef
    from infernux.ui import UIButton

    texture_guid = "button-background-texture-guid"
    button = UIButton()
    button.background_texture = TextureRef(
        guid=texture_guid,
        path_hint="Assets/Textures/Button.png",
    )
    document = button._serialize_fields_document()

    assert "background_texture" in document
    assert "texture_path" not in document
    references = list(GameBuilder._asset_reference_values(
        document, frozenset({texture_guid}),
    ))
    assert references[0] == (texture_guid, "")
    assert {guid for guid, _path in references} == {texture_guid}


@pytest.mark.parametrize("component_type", ["UIText", "UIButton"])
def test_ui_font_chain_is_a_guid_backed_player_cook_dependency(component_type):
    from infernux.core.asset_ref import create_asset_ref
    from infernux.ui import UIButton, UIText

    primary_guid = "ui-primary-font-guid"
    fallback_guid = "ui-fallback-font-guid"
    component = {"UIText": UIText, "UIButton": UIButton}[component_type]()
    component.font = create_asset_ref(
        "Font", guid=primary_guid, path_hint="Assets/Fonts/Primary.ttf"
    )
    component.fallback_fonts = [
        create_asset_ref(
            "Font", guid=fallback_guid, path_hint="Assets/Fonts/Fallback.otf"
        )
    ]

    document = component._serialize_fields_document()
    references = list(
        GameBuilder._asset_reference_values(
            document, frozenset({primary_guid, fallback_guid})
        )
    )

    assert references == [
        (primary_guid, ""),
        (fallback_guid, ""),
    ]
    assert "font_path" not in document
    assert "fallback_font_paths" not in document


def test_cooked_document_catalog_ignores_path_only_asset_reference():
    scene_payload = json.dumps(
        {
            "material": {
                "$type": "asset_ref",
                "guid": "",
                "path_hint": "Assets/Materials/Bird.mat",
            }
        }
    ).encode("utf-8")
    material_payload = b"{}"
    entries = [
        {
            "package": "Content.inxpkg",
            "runtime_path": "Library/Artifacts/Document/scene-guid.scene",
            "bytes": len(scene_payload),
            "payload": scene_payload,
            "asset_binding": {
                "source_guid": "scene-guid",
                "source_path": "Assets/Main.scene",
                "dependencies": [],
            },
        },
        {
            "package": "Content.inxpkg",
            "runtime_path": "Library/Artifacts/Document/material-guid.mat",
            "bytes": len(material_payload),
            "payload": material_payload,
            "asset_binding": {
                "source_guid": "material-guid",
                "source_path": "Assets/Materials/Bird.mat",
                "dependencies": [],
            },
        },
    ]

    catalog = build_catalog(
        entries,
        player_host={"executable": "Game.exe"},
        package_records=[],
    )

    by_path = {artifact["runtime_path"]: artifact for artifact in catalog["artifacts"]}
    scene = by_path["Library/Artifacts/Document/scene-guid.scene"]
    assert scene["dependencies"] == []
    assert scene["unresolved_dependencies"] == []


def test_player_document_rewrite_does_not_interpret_opaque_json_even_with_schema(
    tmp_path,
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    document_path = tmp_path / "RuntimeIndex.json"
    document_path.write_text(
        json.dumps(
            {
                "$schema": "infernux.particle_runtime_index",
                "entries": [
                    {
                        "guid": "1" * 32,
                        "stable_id": "2" * 32,
                        "path_hint": "Assets/VFX/Wind.particlegraph",
                    },
                    {
                        "guid": "1" * 32,
                        "stable_id": "2" * 32,
                        "path_hint": "C:/OldProject/Assets/VFX/Wind.particlegraph",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    original = document_path.read_bytes()
    builder._rewrite_player_document_paths(str(document_path), ".json")
    assert document_path.read_bytes() == original


def test_runtime_catalog_does_not_treat_type_or_stable_ids_as_assets():
    script_guid = "1" * 32
    type_guid = "2" * 32
    particle_guid = "3" * 32
    stable_id = "4" * 32
    script_payload = b"script"
    particle_payload = b"particle"
    type_registry = json.dumps(
        {
            "$schema": "infernux.runtime_type_registry",
            "types": [
                {
                    "script_guid": script_guid,
                    "type_guid": type_guid,
                }
            ],
        }
    ).encode("utf-8")
    particle_index = json.dumps(
        {
            "$schema": "infernux.particle_runtime_index",
            "entries": [
                {
                    "guid": particle_guid,
                    "path_hint": "Assets/VFX/Wind.particlegraph",
                    "stable_id": stable_id,
                }
            ],
        }
    ).encode("utf-8")
    entries = [
        {
            "package": "Content.inxpkg",
            "runtime_path": "Assets/Scripts/Game.pyc",
            "bytes": len(script_payload),
            "asset_binding": {
                "source_guid": script_guid,
                "source_path": "Assets/Scripts/Game.py",
                "dependencies": [],
            },
        },
        {
            "package": "Content.inxpkg",
            "runtime_path": f"Library/Artifacts/Particle/{particle_guid}.inxparticle",
            "bytes": len(particle_payload),
            "asset_binding": {
                "source_guid": particle_guid,
                "source_path": "Assets/VFX/Wind.particlegraph",
                "dependencies": [],
            },
        },
        {
            "package": "Content.inxpkg",
            "runtime_path": "Library/RuntimeTypeRegistry.json",
            "bytes": len(type_registry),
            "payload": type_registry,
        },
        {
            "package": "Content.inxpkg",
            "runtime_path": "Library/Artifacts/Particle/RuntimeIndex.json",
            "bytes": len(particle_index),
            "payload": particle_index,
        },
    ]

    catalog = build_catalog(
        entries,
        player_host={"executable": "Game"},
        package_records=[],
    )

    by_path = {artifact["runtime_path"]: artifact for artifact in catalog["artifacts"]}
    script = by_path["Assets/Scripts/Game.pyc"]
    particle = by_path[
        f"Library/Artifacts/Particle/{particle_guid}.inxparticle"
    ]
    registry = by_path["Library/RuntimeTypeRegistry.json"]
    index = by_path["Library/Artifacts/Particle/RuntimeIndex.json"]
    assert registry["dependencies"] == [script["runtime_artifact_id"]]
    assert registry["unresolved_dependencies"] == []
    assert index["dependencies"] == [particle["runtime_artifact_id"]]
    assert index["unresolved_dependencies"] == []


def test_animclip3d_catalog_depends_on_independent_animation_model():
    clip_guid = "1" * 32
    animation_model_guid = "2" * 32
    clip_payload = json.dumps(
        {
            "name": "Run",
            "source_model_guid": animation_model_guid,
            "source_model_path": "Assets/Animations/Run.fbx",
            "take_name": "Run",
            "bind_pose_bone_names": [],
            "duration_hint": 0.0,
            "events": [],
        }
    ).encode("utf-8")
    mesh_payload = b"mesh"
    entries = [
        {
            "package": "Content.inxpkg",
            "runtime_path": f"Library/Artifacts/Document/{clip_guid}.animclip3d",
            "bytes": len(clip_payload),
            "payload": clip_payload,
            "asset_binding": {
                "source_guid": clip_guid,
                "source_path": "Assets/Animations/Run.animclip3d",
                "dependencies": [],
            },
        },
        {
            "package": "Content.inxpkg",
            "runtime_path": f"Library/Artifacts/Mesh/{animation_model_guid}.inxmesh",
            "bytes": len(mesh_payload),
            "asset_binding": {
                "source_guid": animation_model_guid,
                "source_path": "Assets/Animations/Run.fbx",
                "dependencies": [],
            },
        },
    ]

    catalog = build_catalog(
        entries,
        player_host={"executable": "Game.exe"},
        package_records=[],
    )

    by_path = {artifact["runtime_path"]: artifact for artifact in catalog["artifacts"]}
    model = by_path[f"Library/Artifacts/Mesh/{animation_model_guid}.inxmesh"]
    clip = by_path[f"Library/Artifacts/Document/{clip_guid}.animclip3d"]
    assert clip["dependencies"] == [model["runtime_artifact_id"]]
    assert clip["unresolved_dependencies"] == []


def test_cooked_catalog_discovers_native_and_effect_group_asset_references():
    scene_guid = "11111111111111111111111111111111"
    material_guid = "22222222222222222222222222222222"
    group_guid = "33333333333333333333333333333333"
    effect_guid = "44444444444444444444444444444444"
    payloads = {
        f"Library/Artifacts/Document/{scene_guid}.scene": json.dumps(
            {"materials": [material_guid]}
        ).encode("utf-8"),
        f"Library/Artifacts/Document/{material_guid}.mat": b"{}",
        f"Library/Artifacts/Document/{group_guid}.effectgroup": json.dumps(
            {
                "entries": [
                    {
                        "asset": {"guid": effect_guid}
                    }
                ]
            }
        ).encode("utf-8"),
        f"Library/Artifacts/Document/{effect_guid}.effect": b"{}",
    }
    bindings = {
        scene_guid: "Assets/Main.scene",
        material_guid: "Assets/Materials/Main.mat",
        group_guid: "Assets/Effects/Ink.effectgroup",
        effect_guid: "Assets/Effects/Ink.effect",
    }
    entries = []
    for runtime_path, payload in payloads.items():
        guid = Path(runtime_path).stem
        entries.append(
            {
                "package": "Content.inxpkg",
                "runtime_path": runtime_path,
                "bytes": len(payload),
                "payload": payload,
                "asset_binding": {
                    "source_guid": guid,
                    "source_path": bindings[guid],
                    "dependencies": [],
                },
            }
        )

    catalog = build_catalog(
        entries,
        player_host={"executable": "Game.exe"},
        package_records=[],
    )

    by_path = {artifact["runtime_path"]: artifact for artifact in catalog["artifacts"]}
    scene = by_path[f"Library/Artifacts/Document/{scene_guid}.scene"]
    material = by_path[f"Library/Artifacts/Document/{material_guid}.mat"]
    group = by_path[f"Library/Artifacts/Document/{group_guid}.effectgroup"]
    effect = by_path[f"Library/Artifacts/Document/{effect_guid}.effect"]
    assert scene["dependencies"] == [material["runtime_artifact_id"]]
    assert group["dependencies"] == [effect["runtime_artifact_id"]]


def test_cooked_catalog_keeps_ui_material_shader_and_texture_guids():
    material_guid = "11111111111111111111111111111111"
    vertex_guid = "22222222222222222222222222222222"
    fragment_guid = "33333333333333333333333333333333"
    texture_guid = "44444444444444444444444444444444"
    payloads = {
        f"Library/Artifacts/Document/{material_guid}.mat": json.dumps({
            "shaders": {
                "vertex": {"guid": vertex_guid, "shader_id": "UI/Vertex"},
                "fragment": {"guid": fragment_guid, "shader_id": "UI/Fragment"},
            },
            "properties": {
                "gain": {"type": "Float", "value": 0.5},
                "detailTex": {"type": "Texture2D", "guid": texture_guid},
            },
        }).encode("utf-8"),
        f"Library/Artifacts/Document/{vertex_guid}.vert": b"#version 450\n",
        f"Library/Artifacts/Document/{fragment_guid}.frag": b"#version 450\n",
        f"Library/Artifacts/Textures/{texture_guid}.inxtex": b"texture artifact",
    }
    catalog = build_catalog(
        [
            {
                "package": "Content.inxpkg",
                "runtime_path": path,
                "bytes": len(payload),
                "payload": payload,
                "asset_binding": {"source_guid": Path(path).stem, "source_path": f"Assets/{Path(path).name}",
                                  "dependencies": []},
            }
            for path, payload in payloads.items()
        ],
        player_host={"executable": "Game.exe"},
        package_records=[],
    )
    by_path = {item["runtime_path"]: item for item in catalog["artifacts"]}
    material = by_path[f"Library/Artifacts/Document/{material_guid}.mat"]
    expected = {by_path[path]["runtime_artifact_id"] for path in payloads if not path.endswith(".mat")}
    assert set(material["dependencies"]) == expected
    assert material["unresolved_dependencies"] == []


def test_payload_manifest_rejects_source_replaced_by_current_library_artifact(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(builder, final_dir)
    source = tmp_path / "albedo.png"
    source.write_bytes(b"duplicate source")
    write_pack(
        (("Assets/Textures/albedo.png", source),),
        data_root / builder._CONTENT_ARCHIVE_FILENAME,
    )
    builder._runtime_artifact_source_paths = {"assets/textures/albedo.png"}
    _install_runtime_identity_bindings(
        builder,
        {
            "Assets/Textures/albedo.png": (
                "texture-guid",
                "compiled_source_must_not_ship",
            )
        },
    )

    with pytest.raises(RuntimeError, match="direct or serialized runtime payloads"):
        builder._write_payload_manifest(str(final_dir))


def test_generated_player_log_is_lazy_and_stale_crash_is_cleared(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    source_path = Path(builder._generate_boot_script())
    source = source_path.read_text(encoding="utf-8")

    assert "os.makedirs(_LOGS_DIR, exist_ok=True)" in source
    assert source.count("os.makedirs(_LOGS_DIR, exist_ok=True)") == 1
    assert "open(_LOG, \"w\", encoding=\"utf-8\").close()" not in source
    assert '_CRASH_LOG = os.path.join(_LOGS_DIR, "crash.log")' in source
    assert "if os.path.isfile(_CRASH_LOG):\n    os.remove(_CRASH_LOG)" in source
    assert 'with open(_CRASH_LOG, "w", encoding="utf-8") as _stream:' in source


def test_generated_player_boot_registers_lowercase_public_namespace(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    source = Path(builder._generate_boot_script()).read_text(encoding="utf-8")

    assert "import infernux as _public_api" in source
    assert 'sys.modules["infernux"] = _public_api' in source


def test_generated_player_boot_requires_build_manifest_in_the_data_tree(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    source = Path(builder._generate_boot_script()).read_text(encoding="utf-8")

    required = 'if not os.path.isfile(_BUILD_MANIFEST_PATH):'
    assert required in source
    assert '_BUILD_MANIFEST_PATH = os.path.join(_DATA_DIR, "BuildManifest.json")' in source
    assert "_copy_player_file_atomic" not in source
    assert 'if os.path.isfile(_BUILD_MANIFEST_PATH):' not in source


def test_payload_manifest_rejects_invalid_native_package(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(
        builder,
        final_dir,
        include_runtime=False,
    )
    (data_root / builder._RUNTIME_ARCHIVE_FILENAME).write_bytes(b"not-inxpack")

    with pytest.raises(RuntimeError, match="cannot validate"):
        builder._write_payload_manifest(str(final_dir))


def test_payload_manifest_requires_content_package(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    _prepare_runtime_catalog_inputs(
        builder,
        final_dir,
        include_content=False,
    )

    with pytest.raises(RuntimeError, match=r"Content\.inxpkg"):
        builder._write_payload_manifest(str(final_dir))


def test_payload_manifest_requires_player_executable(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    _prepare_runtime_catalog_inputs(
        builder,
        final_dir,
        include_executable=False,
    )

    with pytest.raises(RuntimeError, match="Player executable is missing"):
        builder._write_payload_manifest(str(final_dir))


def test_payload_manifest_reports_current_native_packages(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(builder, final_dir)

    builder._write_payload_manifest(str(final_dir))

    catalog = _read_runtime_catalog(data_root, builder)
    assert catalog["$schema"] == "infernux.runtime_asset_catalog"
    assert catalog["player_host"]["executable"] == _player_executable_name()
    assert {package["path"] for package in catalog["packages"]} == {
        "TestGame_Data/Runtime.inxrt",
        "TestGame_Data/Content.inxpkg",
    }
    assert all(
        set(package)
        == {"path", "archive_bytes", "file_count", "raw_bytes", "stored_bytes", "codec"}
        for package in catalog["packages"]
    )
    package_index = (
        data_root / builder._PLAYER_PACKAGE_INDEX_FILENAME
    ).read_text(encoding="ascii").splitlines()
    assert package_index[0] == "INFERNUX_PLAYER_PACKAGE_INDEX"
    assert {line.split("\t", 1)[0] for line in package_index[1:]} == {
        "runtime",
        "content",
        "catalog",
    }
    assert all(len(line.split("\t")) == 3 for line in package_index[1:])
    assert len(catalog["artifacts"]) == 2
    assert all(
        artifact["runtime_artifact_id"].startswith("ra_")
        and artifact["dependencies"] == []
        and set(artifact)
        == {
            "runtime_artifact_id",
            "logical_type",
            "payload_kind",
            "package",
            "runtime_path",
            "content_bytes",
            "dependencies",
            "unresolved_dependencies",
        }
        for artifact in catalog["artifacts"]
    )


def test_desktop_player_keeps_project_content_in_native_package(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(builder, final_dir)
    bootstrap_source = tmp_path / "bootstrap.pyd"
    python_source = tmp_path / "python313.dll"
    bootstrap_source.write_bytes(b"bootstrap")
    python_source.write_bytes(b"python")
    write_pack(
        (
            ("_InfernuxBootstrap.pyd", bootstrap_source),
            ("python313.dll", python_source),
        ),
        data_root / "Bootstrap.inxrt",
    )
    (data_root / "BuildManifest.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}), encoding="utf-8"
    )
    builder._write_payload_manifest(str(final_dir))

    builder._materialize_desktop_player_layout(str(final_dir))
    builder._audit_direct_player_layout(str(final_dir))

    assert (data_root / "Runtime" / "_InfernuxBootstrap.pyd").read_bytes() == b"bootstrap"
    assert (data_root / "Runtime" / "python313.dll").read_bytes() == b"python"
    assert (data_root / "Runtime" / "infernux" / "resources" / "runtime.bin").read_bytes() == b"runtime"
    assert not (data_root / "Bootstrap.inxrt").exists()
    assert not (data_root / builder._RUNTIME_ARCHIVE_FILENAME).exists()
    assert (data_root / builder._CONTENT_ARCHIVE_FILENAME).is_file()
    assert (data_root / builder._ASSET_CATALOG_ARCHIVE_FILENAME).is_file()
    assert (data_root / builder._PLAYER_PACKAGE_INDEX_FILENAME).is_file()
    assert not (data_root / "Library").exists()
    assert not (data_root / "BuildManifest.json").exists()
    assert not (data_root / "RuntimeAssets").exists()
    assert not (data_root / "Cache").exists()
    catalog = _read_runtime_catalog(data_root, builder)
    assert json.loads(
        read_entry(
            data_root / builder._ASSET_CATALOG_ARCHIVE_FILENAME,
            "BuildManifest.json",
        ).decode("utf-8")
    )["scene_guids"] == ["scene-guid"]
    assert len(catalog["packages"]) == 1
    assert Path(catalog["packages"][0]["path"]).name == "Content.inxpkg"
    manifest = json.loads(
        (data_root / builder._PLAYER_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["product"]["layout"] == "single_executable_native_packages"
    package_index = (data_root / builder._PLAYER_PACKAGE_INDEX_FILENAME).read_text(
        encoding="ascii"
    )
    assert "content\t" in package_index
    assert "catalog\t" in package_index
    assert "runtime\t" not in package_index


def test_desktop_player_indexes_the_sealed_parallel_module(tmp_path):
    project_root = _make_project(tmp_path)
    builder = GameBuilder(
        str(project_root),
        str(tmp_path / "build_output"),
        game_name="TestGame",
        include_jit_runtime=True,
    )
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(builder, final_dir)
    bootstrap_source = tmp_path / "bootstrap.pyd"
    parallel_source = tmp_path / "parallel.pyd"
    bootstrap_source.write_bytes(b"bootstrap")
    parallel_source.write_bytes(b"parallel")
    write_pack(
        (("_InfernuxBootstrap.pyd", bootstrap_source),),
        data_root / "Bootstrap.inxrt",
    )
    parallel_archive = data_root / "Modules" / builder._PARALLEL_ARCHIVE_FILENAME
    parallel_archive.parent.mkdir(parents=True)
    write_pack(
        (("llvmlite/binding/ffi.pyd", parallel_source),),
        parallel_archive,
    )
    builder._write_payload_manifest(str(final_dir))

    builder._materialize_desktop_player_layout(str(final_dir))
    builder._audit_direct_player_layout(str(final_dir))

    package_index = (
        data_root / builder._PLAYER_PACKAGE_INDEX_FILENAME
    ).read_text(encoding="ascii").splitlines()
    records = {
        parts[0]: (parts[1], int(parts[2]))
        for parts in (line.split("\t") for line in package_index[1:])
    }
    parallel_manifest = read_manifest(parallel_archive)
    assert records["parallel"] == (
        parallel_manifest["archive_sha256"],
        parallel_manifest["archive_bytes"],
    )
    assert "runtime" not in records


def test_linux_runtime_restores_identical_elf_aliases_without_hashing(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "Runtime"
    runtime.mkdir()
    canonical = runtime / "libsample.so.3.1"
    same_abi = runtime / "libsample.so.3"
    short_name = runtime / "libsample.so"
    different_abi = runtime / "libsample.so.2"
    canonical.write_bytes(b"same shared library")
    same_abi.write_bytes(canonical.read_bytes())
    short_name.write_bytes(canonical.read_bytes())
    different_abi.write_bytes(b"different ABI")
    links = []

    def record_link(path, target, *args, **kwargs):
        links.append((path.name, str(target)))
        path.write_text(str(target), encoding="utf-8")

    monkeypatch.setattr(Path, "symlink_to", record_link)

    collapsed = GameBuilder._collapse_linux_shared_library_aliases(runtime)

    assert collapsed == 2
    assert sorted(links) == [
        ("libsample.so", "libsample.so.3.1"),
        ("libsample.so.3", "libsample.so.3.1"),
    ]
    assert canonical.read_bytes() == b"same shared library"
    assert different_abi.read_bytes() == b"different ABI"


def test_payload_manifest_supports_platform_native_player_host(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(
        builder,
        final_dir,
        include_runtime=False,
        include_executable=False,
    )
    host = {
        "identity": "android-sdl-python-player-host",
        "entry_point": "com.infernux.bootstrap/.InfernuxActivity",
        "platform": "android",
        "architecture": "x86_64",
    }

    builder._write_payload_manifest(
        str(final_dir),
        platform_host=host,
        include_runtime_archive=False,
    )

    catalog = _read_runtime_catalog(data_root, builder)
    manifest = json.loads(
        (data_root / "Player.inxmanifest").read_text(encoding="utf-8")
    )
    package_index = (data_root / "PackageIndex.inxmanifest").read_text(
        encoding="ascii"
    )
    assert catalog["player_host"] == host
    assert [record["path"] for record in catalog["packages"]] == [
        "TestGame_Data/Content.inxpkg"
    ]
    assert manifest["product"]["layout"] == "platform_native_packages"
    assert manifest["product"]["entry_points"] == [host["entry_point"]]
    assert "content\t" in package_index
    assert "catalog\t" in package_index
    assert "runtime\t" not in package_index


def test_payload_manifest_rejects_direct_documents_after_dependency_reads(
    tmp_path,
    monkeypatch,
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = final_dir / "TestGame_Data"
    data_root.mkdir(parents=True)
    _write_player_executable(final_dir)

    sources = tmp_path / "catalog-payloads"
    sources.mkdir()
    bytecode = sources / "module.pyc"
    native = sources / "native.dll"
    shader = sources / "builtin.frag"
    artifact = sources / "texture.inxtex"
    bytecode.write_bytes(b"bytecode")
    native.write_bytes(b"native")
    shader.write_text("void main() {}", encoding="utf-8")
    artifact.write_bytes(b"artifact")
    metadata = sources / "RuntimeIndex.json"
    scene = sources / "Main.scene"
    material = sources / "Test.mat"
    audio = sources / "Sound.wav"
    reference = {
        "material": {
            "$type": "asset_ref",
            "path_hint": "Assets/Materials/Test.mat",
        }
    }
    metadata.write_text(json.dumps(reference), encoding="utf-8")
    scene.write_text(json.dumps(reference), encoding="utf-8")
    material.write_text("{}", encoding="utf-8")
    audio.write_bytes(b"audio")

    runtime_entries = [
        *[(f"stdlib/module_{index}.pyc", bytecode) for index in range(128)],
        *[(f"infernux/lib/native_{index}.dll", native) for index in range(64)],
        ("infernux/resources/shaders/standard.frag", shader),
        ("numpy/core/_multiarray_umath.pyd", native),
        ("Library/Artifacts/Textures/Test.inxtex", artifact),
        ("Library/Particle/RuntimeIndex.json", metadata),
    ]
    write_pack(runtime_entries, data_root / builder._RUNTIME_ARCHIVE_FILENAME)
    write_pack(
        (
            ("Assets/Main.scene", scene),
            ("Assets/Materials/Test.mat", material),
            ("Assets/Audio/Sound.wav", audio),
        ),
        data_root / builder._CONTENT_ARCHIVE_FILENAME,
    )

    reads: list[str] = []
    native_read_entry = game_builder_module.read_entry

    def counted_read_entry(package_path, entry_path):
        reads.append(str(entry_path).replace("\\", "/"))
        return native_read_entry(package_path, entry_path)

    monkeypatch.setattr(game_builder_module, "read_entry", counted_read_entry)
    _install_runtime_identity_bindings(
        builder,
        {
            "Assets/Main.scene": (
                "scene-guid",
                "runtime_loader_requires_serialized_document",
            ),
            "Assets/Materials/Test.mat": (
                "material-guid",
                "runtime_loader_requires_serialized_document",
            ),
            "Assets/Audio/Sound.wav": (
                "audio-guid",
                "runtime_audio_backend_requires_encoded_stream",
            ),
        },
    )

    with pytest.raises(
        RuntimeError,
        match="direct or serialized runtime payload",
    ):
        builder._write_payload_manifest(str(final_dir))

    assert set(reads) == {
        "Library/Particle/RuntimeIndex.json",
        "Assets/Main.scene",
        "Assets/Materials/Test.mat",
    }


class TestGameBuilderOutputSafety:
    def test_debug_player_boot_and_manifest_mark_validation_capability(self, tmp_path):
        output_dir = tmp_path / "build_output"
        builder = GameBuilder(
            str(_make_project(tmp_path)),
            str(output_dir),
            game_name="TestGame",
            debug_mode=True,
        )

        boot_path = builder._generate_boot_script()
        boot_bytes = Path(boot_path).read_bytes()
        assert b"\x00" not in boot_bytes
        boot_source = boot_bytes.decode("utf-8")
        compile(boot_source, boot_path, "exec")
        assert "import json" not in boot_source
        assert "import pathlib" not in boot_source
        assert "from pathlib" not in boot_source
        assert "import traceback" not in boot_source
        assert '_DEBUG_MODE = os.environ["_INFERNUX_PLAYER_DEBUG_BUILD"] == "1"' in boot_source
        assert boot_source.index("_DATA_DIR = prepare_platform_player(") < boot_source.index("_DEBUG_MODE =")
        assert 'os.environ["PYTHONDONTWRITEBYTECODE"] = "1"' in boot_source
        assert 'os.environ["_INFERNUX_PLAYER_DATA_ROOT"] = _DATA_ROOT' in boot_source
        assert (
            'os.environ["_INFERNUX_PLAYER_PERSISTENT_DATA_ROOT"] = '
            '_PLAYER_PERSISTENT_DATA_ROOT'
        ) in boot_source
        assert "sys.dont_write_bytecode = True" in boot_source
        assert 'os.environ["_INFERNUX_PACKAGED_RESOURCE_ROOT"]' in boot_source
        assert "_CORE_RUNTIME_DIR = _RUNTIME_ROOT" in boot_source
        assert "if not os.path.isdir(_CORE_RUNTIME_DIR):" in boot_source
        assert '"stdlib"' in boot_source
        assert '_STDLIB_RUNTIME_DIR' in boot_source
        assert 'os.add_dll_directory(_dll_dir)' in boot_source
        assert "from infernux.engine.platform_player_bootstrap import prepare_platform_player" in boot_source
        assert "_DATA_DIR = prepare_platform_player(" in boot_source
        assert '_RUNTIME_MODULE_DIR = os.path.join(_DATA_ROOT, "Modules", "Parallel")' in boot_source
        assert 'if os.path.isdir(_RUNTIME_MODULE_DIR):' in boot_source
        assert '_mark_boot_phase("parallel_ready")' in boot_source
        assert 'class _ParallelRuntimeFinder:' not in boot_source
        assert "_extract_cached_archive" not in boot_source
        assert "PackageIndex.inxmanifest" not in boot_source
        assert "_validate_native_archive_paths" not in boot_source
        assert '"_INFERNUX_PLAYER_DATA_ROOT"' in boot_source
        assert "_INFERNUX_PLAYER_" in boot_source
        assert "_ARCHIVE_SHA256" not in boot_source
        assert "_ARCHIVE_BYTES" not in boot_source
        assert '_GAME_NAME = _EXE_STEM or "InfernuxPlayer"' in boot_source
        assert 'if os.environ.get("_INFERNUX_PLAYER_CONTROL_FILE"):' in boot_source
        pre_native_boot = boot_source.split("_CORE_RUNTIME_DIR = _RUNTIME_ROOT", 1)[0]
        assert "from infernux" not in pre_native_boot
        assert "import infernux" not in pre_native_boot
        assert "os.path.dirname(sys.executable)" in pre_native_boot
        assert "import _InfernuxBootstrap as _NATIVE_PACK" in boot_source
        assert "import shutil" not in boot_source
        assert "shutil." not in boot_source
        assert "import ctypes" not in boot_source
        assert "ctypes." not in boot_source
        assert '"_inxplayer_show_error"' in boot_source
        assert "_NATIVE_PACK._inxplayer_show_error(" in boot_source
        assert 'sys.modules["infernux.lib._Infernux"]' not in boot_source
        assert "import _Infernux as _module" not in boot_source
        assert "from infernux.lib import _Infernux" not in boot_source
        assert '_INFERNUX_LIB_DIR = os.path.join(_CORE_RUNTIME_DIR, "infernux", "lib")' in boot_source
        assert 'os.environ["INFERNUX_NATIVE_MODULE_DIR"] = _INFERNUX_LIB_DIR' in boot_source
        assert "PlayerCache" not in boot_source
        assert "cache.complete" not in boot_source
        assert boot_source.index("import _InfernuxBootstrap as _NATIVE_PACK") < boot_source.index(
            "from infernux.engine import run_player"
        )

        settings = output_dir / "Data" / "ProjectSettings"
        settings.mkdir(parents=True)
        (settings / "BuildSettings.json").write_text(
            json.dumps({"scene_guids": ["scene-guid"]}), encoding="utf-8"
        )
        builder._generate_manifest(str(output_dir))
        manifest = json.loads((output_dir / "Data" / "BuildManifest.json").read_text(encoding="utf-8"))
        assert manifest["debug_build"] is True
        assert manifest["game_name"] == "TestGame"

    def test_validate_rejects_non_empty_unmarked_output_dir(self, tmp_path):
        output_dir = tmp_path / "build_output"
        output_dir.mkdir()
        keep_file = output_dir / "keep.txt"
        keep_file.write_text("keep", encoding="utf-8")
        builder = _make_builder(tmp_path, output_dir)

        with pytest.raises(BuildOutputDirectoryError) as exc_info:
            builder._validate()

        assert exc_info.value.reason == "not-empty-unmarked"
        assert exc_info.value.entries == ["keep.txt"]

        assert keep_file.read_text(encoding="utf-8") == "keep"

    def test_validate_rejects_unmarked_build_temp_output_dir(self, tmp_path):
        output_dir = tmp_path / "build_output"
        temp_dir = output_dir / "_build_temp"
        nested_dir = temp_dir / "nested"
        nested_dir.mkdir(parents=True)
        (nested_dir / "stale.bin").write_bytes(b"stale")
        builder = _make_builder(tmp_path, output_dir)

        with pytest.raises(BuildOutputDirectoryError, match="must be empty"):
            builder._validate()

    def test_clean_output_allows_marked_build_directory(self, tmp_path):
        output_dir = tmp_path / "build_output"
        output_dir.mkdir()
        old_file = output_dir / "old.bin"
        old_file.write_text("old", encoding="utf-8")
        nested_dir = output_dir / "Data"
        nested_dir.mkdir()
        (nested_dir / "stale.txt").write_text("stale", encoding="utf-8")

        builder = _make_builder(tmp_path, output_dir)
        builder._write_output_marker(str(output_dir))

        builder._validate()
        builder._clean_output()

        assert output_dir.is_dir()
        assert list(output_dir.iterdir()) == []

    def test_clean_output_propagates_directory_removal_failure(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "build_output"
        nested_dir = output_dir / "Data"
        nested_dir.mkdir(parents=True)
        (nested_dir / "stale.txt").write_text("stale", encoding="utf-8")
        builder = _make_builder(tmp_path, output_dir)
        builder._write_output_marker(str(output_dir))

        def reject_removal(_path):
            raise PermissionError("directory is locked")

        monkeypatch.setattr(game_builder_module.shutil, "rmtree", reject_removal)

        with pytest.raises(PermissionError, match="directory is locked"):
            builder._clean_output()

    def test_write_output_marker_creates_reusable_build_marker(self, tmp_path):
        output_dir = tmp_path / "build_output"
        output_dir.mkdir()
        builder = _make_builder(tmp_path, output_dir)

        builder._write_output_marker(str(output_dir))

        marker_path = Path(builder._output_marker_path(str(output_dir)))
        assert marker_path.is_file()
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
        assert payload["tool"] == "Infernux"
        assert payload["kind"] == "build-output"
        assert payload["state"] == "complete"
        assert payload["project_name"] == "TestGame"
        assert len(payload["project_identity"]) == 64
        assert "project_path" not in payload

    def test_in_progress_marker_allows_safe_retry(self, tmp_path):
        output_dir = tmp_path / "build_output"
        output_dir.mkdir()
        builder = _make_builder(tmp_path, output_dir)
        builder._write_output_marker(str(output_dir), state="in_progress")
        (output_dir / "partial-player.exe").write_bytes(b"partial")

        builder._clean_output()

        assert list(output_dir.iterdir()) == []

    @pytest.mark.parametrize("same_project,same_name", [(True, True), (False, True), (True, False)])
    def test_sealed_output_manifest_controls_rebuild_ownership(self, tmp_path, same_project, same_name):
        output_dir = tmp_path / "build_output"
        builder = _make_builder(tmp_path, output_dir)
        data_root = output_dir / "TestGame_Data"
        data_root.mkdir(parents=True)
        source = tmp_path / "BuildManifest.json"
        source.write_text(json.dumps({"build_output": {
            "tool": "Infernux",
            "project_name": builder.project_name if same_name else "OtherGame",
            "project_identity": game_builder_module.path_fingerprint(builder.project_path) if same_project else "0" * 64,
        }}), encoding="utf-8")
        metadata = write_pack((("BuildManifest.json", source),), data_root / "AssetCatalog.inxcat")
        (data_root / "PackageIndex.inxmanifest").write_text(
            f"INFERNUX_PLAYER_PACKAGE_INDEX\ncatalog\t{metadata['archive_sha256']}\t{metadata['archive_bytes']}\n",
            encoding="ascii",
        )
        sentinel = output_dir / "preserve.bin"
        sentinel.write_bytes(b"previous product")

        if same_project and same_name:
            builder._validate_output_directory()
        else:
            with pytest.raises(BuildOutputDirectoryError, match="must be empty"):
                builder._validate_output_directory()
        assert sentinel.read_bytes() == b"previous product"
        assert not (data_root / "BuildManifest.json").exists()

    def test_foreign_output_marker_does_not_authorize_cleanup(self, tmp_path):
        output_dir = tmp_path / "build_output"
        output_dir.mkdir()
        (output_dir / GameBuilder.OUTPUT_MARKER_FILENAME).write_text(
            json.dumps(
                {
                    "tool": "Infernux",
                    "kind": "build-output",
                    "state": "complete",
                    "project_name": "OtherGame",
                    "project_identity": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
        (output_dir / "keep.txt").write_text("keep", encoding="utf-8")
        builder = _make_builder(tmp_path, output_dir)

        with pytest.raises(BuildOutputDirectoryError, match="must be empty"):
            builder._clean_output()


class TestGameBuilderDependencyCollection:
    def test_collect_renderstack_provider_without_scene_component_reference(self, tmp_path):
        builder = _make_builder(tmp_path, tmp_path / "build_output")
        project = Path(builder.project_path)
        scene = project / "Assets" / "Main.scene"
        provider = project / "Assets" / "Rendering" / "StylizedPipeline.py"
        ordinary = project / "Assets" / "Scripts" / "Unused.py"
        provider.parent.mkdir(parents=True, exist_ok=True)
        ordinary.parent.mkdir(parents=True, exist_ok=True)
        scene.write_text("{}", encoding="utf-8")
        provider.write_text(
            "from infernux.renderstack import DefaultForwardPipeline\n"
            "class StylizedPipeline(DefaultForwardPipeline):\n"
            "    name = 'Stylized'\n",
            encoding="utf-8",
        )
        ordinary.write_text("class Unused:\n    pass\n", encoding="utf-8")
        _write_asset_index(
            project,
            [
                _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
                _asset_index_entry(project, provider, "provider-guid", "", "Script"),
                _asset_index_entry(project, ordinary, "ordinary-guid", "", "Script"),
            ],
        )
        (project / "ProjectSettings" / "BuildSettings.json").write_text(
            json.dumps({"scene_guids": ["scene-guid"]}), encoding="utf-8"
        )

        selected = builder._collect_library_asset_entries(builder._asset_index_entries())

        assert set(selected) == {"scene-guid", "provider-guid", "ordinary-guid"}

    def test_collect_user_dependencies_allows_mcp_named_user_requirements(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        (project_root / "ProjectSettings" / "requirements.txt").write_text(
            "mcp>=1.24,<2\nfastmcp\n",
            encoding="utf-8",
        )
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")

        def fake_find_spec(name):
            return object() if name in {"mcp", "fastmcp"} else None

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
        monkeypatch.setattr("infernux.engine._build_dependencies._has_requirement", lambda *_args: True)

        assert _collect_fixture_dependencies(builder) == ["fastmcp", "mcp"]

    def test_collect_user_dependencies_allows_mcp_named_asset_imports(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        _write_asset_script(project_root, "tooling.py", "import mcp\nimport fastmcp\n")
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")

        def fake_find_spec(name):
            return object() if name in {"mcp", "fastmcp"} else None

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

        assert _collect_fixture_dependencies(builder) == ["fastmcp", "mcp"]

    def test_project_requirements_use_shared_path_markers_and_import_names(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        req_path = project_root / "ProjectSettings" / "requirements.txt"
        req_path.write_text(
            "# keep comments\n"
            "Pillow>=10\n"
            "numba>=0.61\n"
            "llvmlite>=0.44\n"
            'skipped==1; python_version < "2.0"\n',
            encoding="utf-8",
        )
        # The obsolete root file must not change the authored dependency set.
        (project_root / "requirements.txt").write_text("old_missing_package==1\n", encoding="utf-8")
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")
        before = req_path.read_bytes()
        checked = []
        monkeypatch.setattr(
            "infernux.engine._build_dependencies._has_requirement",
            lambda spec, module: checked.append((spec, module)) or True,
        )
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: object() if name == "PIL" else None)
        assert _collect_fixture_dependencies(builder) == ["PIL"]
        assert checked == [("Pillow>=10", "PIL")]
        assert req_path.read_bytes() == before

    def test_project_build_rejects_an_importable_dependency_with_wrong_version(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        (project_root / "ProjectSettings" / "requirements.txt").write_text("demo==2.0\n", encoding="utf-8")
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")
        monkeypatch.setattr("infernux.engine._build_dependencies._has_requirement", lambda *_args: False)
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
        with pytest.raises(RuntimeError, match="do not satisfy project requirements: demo==2.0"):
            _collect_fixture_dependencies(builder)

    def test_filter_shipped_requirements_keeps_user_packages_and_removes_disabled_jit(self, tmp_path):
        data_dir = tmp_path / "build_output" / "Data"
        settings_dir = data_dir / "ProjectSettings"
        settings_dir.mkdir(parents=True)
        req_path = settings_dir / "requirements.txt"
        req_path.write_text(
            "numba>=0.61.0\n"
            "llvmlite>=0.44\n"
            "mcp>=1.24,<2\n"
            "fastmcp\n"
            "requests>=2\n",
            encoding="utf-8",
        )
        builder = _make_builder(tmp_path, tmp_path / "build_output")

        builder._filter_shipped_requirements(str(data_dir))

        filtered_text = req_path.read_text(encoding="utf-8")
        assert "requests>=2" in filtered_text
        assert "numba" not in filtered_text.lower()
        assert "llvmlite" not in filtered_text.lower()
        assert "mcp>=1.24,<2" in filtered_text.lower()
        assert "fastmcp" in filtered_text.lower()

    def test_nuitka_builder_does_not_reserve_plugin_dependency_names(self):
        assert not NuitkaBuilder._is_game_build_excluded_package("mcp")
        assert not NuitkaBuilder._is_game_build_excluded_package("fastmcp.server")
        assert not NuitkaBuilder._is_game_build_excluded_package("requests")


    def test_nuitka_player_excludes_editor_graph_but_keeps_runtime_viewport_utility(self):
        editor_modules = NuitkaBuilder._PLAYER_EDITOR_ONLY_MODULES

        assert "infernux.engine.ui.editor_panel" in editor_modules
        assert "infernux.engine.ui.asset_resource_preview" in editor_modules
        assert "infernux.engine.ui.window_manager" in editor_modules
        assert "infernux.engine.i18n" in editor_modules
        assert "infernux.engine.play_mode" in editor_modules
        assert "infernux.engine.scene_manager" in editor_modules
        assert "infernux.engine.scene_document_transaction" in editor_modules
        assert "infernux.engine.resources_manager" in editor_modules
        assert "infernux.engine.import_coordinator" in editor_modules
        expected_authoring_modules = {
            path[:-4].replace("/", ".")
            for path in forbidden_player_service_modules()
            if path.endswith(".pyc")
        }
        assert expected_authoring_modules.issubset(editor_modules)
        assert "infernux.engine.ui.viewport_utils" not in editor_modules
        assert not NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/player_scene.py"
        )
        assert not NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/runtime_scene_transaction.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/scene_document_transaction.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/import_coordinator.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/_bootstrap_panels.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/_bootstrap_selection.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/_bootstrap_trace.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/_bootstrap_wiring.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/bootstrap_inspector/_wire.py"
        )
        assert not NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/build_settings.py"
        )
        assert not NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/build_target.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/candidate_import.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/library_sync.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "engine/preferences_store.py"
        )
        assert NuitkaBuilder._is_player_runtime_excluded_source(
            "gizmos/collector.py"
        )
        assert "infernux_mcp" not in NuitkaBuilder._GAME_BUILD_NOFOLLOW_MODULES

    def test_collect_user_dependencies_adds_llvmlite_for_numba_import(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        _write_asset_script(project_root, "stress.py", "import numba\n")
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")
        builder.include_jit_runtime = True

        original_find_spec = importlib.util.find_spec

        def fake_find_spec(name):
            if name in {"numba", "llvmlite", "numpy"}:
                return object()
            return original_find_spec(name)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

        deps = _collect_fixture_dependencies(builder)

        assert deps == ["llvmlite", "numba", "numpy"]

    def test_collect_user_dependencies_excludes_both_public_engine_names(self, tmp_path):
        project_root = _make_project(tmp_path)
        _write_asset_script(project_root, "public_api.py", "import infernux as inx\nfrom infernux import Application\n")
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"))
        assert _collect_fixture_dependencies(builder) == []

    def test_collect_user_dependencies_rejects_invalid_project_script(self, tmp_path):
        project_root = _make_project(tmp_path)
        _write_asset_script(project_root, "broken.py", "def broken(:\n")
        script_path = project_root / "Assets" / "broken.py"
        builder = GameBuilder(
            str(project_root),
            str(tmp_path / "build_output"),
            game_name="TestGame",
        )

        with pytest.raises(SyntaxError) as exc_info:
            _collect_fixture_dependencies(builder)

        assert exc_info.value.filename == str(script_path)

    def test_collect_user_dependencies_rejects_missing_packages(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        _write_asset_script(
            project_root,
            "missing_dependency.py",
            "import absent_first\nimport absent_second\n",
        )
        builder = GameBuilder(
            str(project_root),
            str(tmp_path / "build_output"),
            game_name="TestGame",
        )
        monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)

        with pytest.raises(
            RuntimeError,
            match="Player build dependencies are not installed: absent_first, absent_second",
        ):
            _collect_fixture_dependencies(builder)


class TestGameBuilderAutoParallelExport:
    @pytest.mark.parametrize("location", ["Assets", "Packages/vendor/compute/runtime"])
    def test_gpu_kernel_is_cooked_as_player_jit_declaration(
        self, tmp_path, location, monkeypatch
    ):
        from infernux.compute import Kernel
        from infernux._compiler.taichi import frontend

        output = tmp_path / "output"
        script = output / "Data" / location / "gpu.py"
        script.parent.mkdir(parents=True)
        script.write_text(
            "import infernux as inx\n"
            "@inx.compute.kernel\n"
            "def scale(domain, values, factor):\n"
            "    i = inx.compute.index(domain)\n"
            "    values[i] = values[i] * factor\n",
            encoding="utf-8",
        )
        builder = _make_builder(tmp_path, output)
        _bind_staged_script_to_asset_index(
            builder, output, script, guid="gpu-script-guid"
        )
        compile_scripts = builder._compile_user_scripts
        if location.startswith("Packages/"):
            registry = output / "Data/ProjectSettings/InxPlugins.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({"installed": [{
                "reference": "vendor/compute",
                "files": [{
                    "guid": "gpu-script-guid", "path_hint": location + "/gpu.py"
                }],
            }]}), encoding="utf-8")
            compile_scripts = builder._compile_player_plugin_scripts

        compile_scripts(str(output))

        assert not script.exists()
        assert not (output / "Data/Library/Compute").exists()
        loader = importlib.machinery.SourcelessFileLoader(
            "gpu_cooked", str(script.with_suffix(".pyc"))
        )
        namespace = {}
        exec(loader.get_code("gpu_cooked"), namespace)
        assert isinstance(namespace["scale"], Kernel)
        monkeypatch.setattr(
            frontend.inspect,
            "getsource",
            lambda _value: (_ for _ in ()).throw(OSError("source is absent")),
        )
        assert "def scale(domain, values, factor)" in frontend._function_source(
            namespace["scale"].function
        )

    def test_compile_user_scripts_propagates_bytecode_failure(self, tmp_path, monkeypatch):
        output_dir = tmp_path / "build_output"
        assets_dir = output_dir / "Data" / "Assets"
        assets_dir.mkdir(parents=True)
        script_path = assets_dir / "gameplay.py"
        script_path.write_text("score = 1\n", encoding="utf-8")

        builder = _make_builder(tmp_path, output_dir)
        builder.include_jit_runtime = True
        _bind_staged_script_to_asset_index(
            builder,
            output_dir,
            script_path,
            guid="gameplay-script-guid",
        )
        failure = py_compile.PyCompileError(
            SyntaxError,
            SyntaxError("compiler rejected source"),
            str(script_path),
            "compiler rejected source",
        )

        def reject_compile(*_args, **_kwargs):
            raise failure

        monkeypatch.setattr(game_builder_module.py_compile, "compile", reject_compile)

        with pytest.raises(py_compile.PyCompileError):
            builder._compile_user_scripts(str(output_dir))

        assert script_path.is_file()
        assert not (assets_dir / "gameplay.pyc").exists()

    @pytest.mark.parametrize("declaration", [
        "from infernux import jit\n@jit.compile(cache=True)\n",
    ])
    def test_compile_user_scripts_embeds_auto_parallel_without_sidecar(self, tmp_path, declaration, monkeypatch):
        output_dir = tmp_path / "build_output"
        assets_dir = output_dir / "Data" / "Assets"
        assets_dir.mkdir(parents=True)

        script_path = assets_dir / "stress.py"
        stale_sidecar = assets_dir / "stress.autop.pyc"
        stale_sidecar.write_bytes(b"obsolete")
        script_path.write_text(
            declaration +
            "def burn(n):\n"
            "    acc = 0\n"
            "    for i in range(n):\n"
            "        acc += i\n"
            "    return acc\n",
            encoding="utf-8",
        )

        builder = _make_builder(tmp_path, output_dir)
        builder.include_jit_runtime = True
        _bind_staged_script_to_asset_index(
            builder,
            output_dir,
            script_path,
            guid="auto-parallel-script-guid",
        )
        builder._compile_user_scripts(str(output_dir))

        assert not script_path.exists()
        bytecode_path = assets_dir / "stress.pyc"
        assert bytecode_path.is_file()
        assert not stale_sidecar.exists()

        loader = importlib.machinery.SourcelessFileLoader(
            "infernux_test_embedded_auto_parallel",
            str(bytecode_path),
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        import infernux.application as application

        player_data = tmp_path / "player-data"
        cache = player_data / "Cache" / "Compute" / "CPU"
        cache.mkdir(parents=True)
        # This test exercises the embedded bytecode contract rather than the
        # Player package reader.  A real Player materializes this directory
        # from Content.inxpkg before importing cached JIT declarations.
        (cache / "inx-test-ready.nbi").write_bytes(b"ready")
        monkeypatch.setattr(application, "_runtime_kind", "player")
        monkeypatch.setenv("_INFERNUX_PLAYER_PERSISTENT_DATA_ROOT", str(player_data))
        monkeypatch.setitem(sys.modules, loader.name, module)
        loader.exec_module(module)
        assert module.burn(10) == 45
        assert module.burn.parallel is not module.burn.serial
        manifest = module.__infernux_jit_manifest__["burn"]
        assert len(manifest["hir_fingerprint"]) == 64
        assert module.burn.compiler_fingerprint == manifest["hir_fingerprint"]
        assert list((player_data / "Cache/Compute/CPU").glob("inx-*.nbc"))

    def test_compile_user_scripts_skips_sidecar_for_non_auto_parallel_script(self, tmp_path):
        output_dir = tmp_path / "build_output"
        assets_dir = output_dir / "Data" / "Assets"
        assets_dir.mkdir(parents=True)

        script_path = assets_dir / "plain.py"
        script_path.write_text(
            "from infernux import jit\n"
            "@jit.compile(cache=True, auto_parallel=False)\n"
            "def burn(n):\n"
            "    acc = 0\n"
            "    for i in range(n):\n"
            "        acc += i\n"
            "    return acc\n",
            encoding="utf-8",
        )

        builder = _make_builder(tmp_path, output_dir)
        builder.include_jit_runtime = True
        _bind_staged_script_to_asset_index(
            builder,
            output_dir,
            script_path,
            guid="serial-jit-script-guid",
        )
        builder._compile_user_scripts(str(output_dir))

        assert not script_path.exists()
        assert (assets_dir / "plain.pyc").is_file()
        assert not (assets_dir / "plain.autop.pyc").exists()

    def test_compile_user_scripts_rejects_required_unsafe_kernel(self, tmp_path):
        output_dir = tmp_path / "build_output"
        assets_dir = output_dir / "Data" / "Assets"
        assets_dir.mkdir(parents=True)
        script_path = assets_dir / "unsafe.py"
        script_path.write_text(
            "from infernux import jit\n"
            "@jit.compile(parallel_policy='required')\n"
            "def prefix(values):\n"
            "    for i in range(1, len(values)):\n"
            "        values[i] = values[i - 1] + 1\n",
            encoding="utf-8",
        )

        builder = _make_builder(tmp_path, output_dir)
        builder.include_jit_runtime = True
        _bind_staged_script_to_asset_index(
            builder,
            output_dir,
            script_path,
            guid="unsafe-parallel-script-guid",
        )
        with pytest.raises(RuntimeError, match="compute compilation rejected"):
            builder._compile_user_scripts(str(output_dir))

    def test_compile_user_scripts_without_jit_keeps_cpu_function_as_plain_python(
        self, tmp_path
    ):
        output_dir = tmp_path / "build_output"
        assets_dir = output_dir / "Data" / "Assets"
        assets_dir.mkdir(parents=True)
        script_path = assets_dir / "compute_user.py"
        script_path.write_text(
            'import infernux as inx\n'
            '@inx.jit.compile\n'
            'def square(value):\n'
            '    return value * value\n',
            encoding="utf-8",
        )
        builder = _make_builder(tmp_path, output_dir)
        builder.include_jit_runtime = False
        _bind_staged_script_to_asset_index(
            builder, output_dir, script_path, guid="cpu-compute-script-guid",
        )
        builder._compile_user_scripts(str(output_dir))

        assert not script_path.exists()
        bytecode_path = script_path.with_suffix(".pyc")
        assert bytecode_path.is_file()
        loader = importlib.machinery.SourcelessFileLoader(
            "infernux_test_plain_web_jit", str(bytecode_path)
        )
        namespace = {}
        exec(loader.get_code(loader.name), namespace)
        assert namespace["square"](7) == 49
        assert not hasattr(namespace["square"], "serial")

    def test_compile_user_scripts_without_jit_ignores_native_parallel_policy(
        self, tmp_path
    ):
        output_dir = tmp_path / "build_output"
        assets_dir = output_dir / "Data" / "Assets"
        assets_dir.mkdir(parents=True)
        (assets_dir / "required.py").write_text(
            "from infernux import jit\n"
            "@jit.compile(parallel_policy='required')\n"
            "def fill(values):\n"
            "    for i in range(len(values)):\n"
            "        values[i] = i\n",
            encoding="utf-8",
        )

        builder = _make_builder(tmp_path, output_dir)
        builder.include_jit_runtime = False
        _bind_staged_script_to_asset_index(
            builder,
            output_dir,
            assets_dir / "required.py",
            guid="required-parallel-script-guid",
        )
        builder._compile_user_scripts(str(output_dir))

        bytecode_path = (assets_dir / "required.pyc")
        assert bytecode_path.is_file()
        loader = importlib.machinery.SourcelessFileLoader(
            "infernux_test_plain_web_required", str(bytecode_path)
        )
        namespace = {}
        exec(loader.get_code(loader.name), namespace)
        values = [0, 0, 0, 0]
        namespace["fill"](values)
        assert values == [0, 1, 2, 3]

    def test_collect_user_dependencies_detects_public_infernux_jit_api(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        _write_asset_script(
            project_root,
            "jit_user.py",
            "from infernux import jit\n@jit.compile\ndef run(value):\n    return value\n",
        )
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")
        builder.include_jit_runtime = True

        original_find_spec = importlib.util.find_spec

        def fake_find_spec(name):
            if name in {"numba", "llvmlite", "numpy"}:
                return object()
            return original_find_spec(name)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

        deps = _collect_fixture_dependencies(builder)

        assert deps == ["llvmlite", "numba", "numpy"]

    def test_collect_user_dependencies_does_not_stage_cpu_runtime_when_disabled(self, tmp_path, monkeypatch):
        project_root = _make_project(tmp_path)
        _write_asset_script(project_root, "jit_user.py", "from infernux import jit\n")
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")
        builder.include_jit_runtime = False

        original_find_spec = importlib.util.find_spec

        def fake_find_spec(name):
            if name in {"numba", "llvmlite", "numpy"}:
                return object()
            return original_find_spec(name)

        monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

        assert _collect_fixture_dependencies(builder) == []

    def test_collect_user_dependencies_rejects_direct_numba_when_disabled(self, tmp_path):
        project_root = _make_project(tmp_path)
        _write_asset_script(project_root, "jit_user.py", "import numba\n")
        builder = GameBuilder(str(project_root), str(tmp_path / "build_output"), game_name="TestGame")
        builder.include_jit_runtime = False

        with pytest.raises(RuntimeError, match="CPU JIT build capability"):
            _collect_fixture_dependencies(builder)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows SDK environment")
class TestNuitkaWindowsSdkEnvironment:
    def test_augment_windows_sdk_environment_adds_kits_tools_and_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nuitka_builder_module.sys, "platform", "win32")

        sdk_root = tmp_path / "Windows Kits" / "10"
        sdk_version = "10.0.22621.0"
        for relative_dir in (
            f"Include/{sdk_version}/ucrt",
            f"Include/{sdk_version}/shared",
            f"Include/{sdk_version}/um",
            f"Include/{sdk_version}/winrt",
            f"Lib/{sdk_version}/ucrt/x64",
            f"Lib/{sdk_version}/um/x64",
            f"bin/{sdk_version}/x64",
        ):
            (sdk_root / relative_dir).mkdir(parents=True, exist_ok=True)
        (sdk_root / "Include" / sdk_version / "um" / "Windows.h").write_text("", encoding="utf-8")

        for tool_name in ("rc.exe", "mt.exe"):
            tool_path = sdk_root / "bin" / sdk_version / "x64" / tool_name
            tool_path.write_text("", encoding="utf-8")
            tool_path.chmod(0o755)

        msvc_bin = tmp_path / "msvc" / "bin"
        msvc_bin.mkdir(parents=True)
        for tool_name in ("cl.exe", "link.exe"):
            tool_path = msvc_bin / tool_name
            tool_path.write_text("", encoding="utf-8")
            tool_path.chmod(0o755)
        msvc_include = tmp_path / "msvc" / "include"
        msvc_lib = tmp_path / "msvc" / "lib" / "x64"
        msvc_include.mkdir(parents=True)
        msvc_lib.mkdir(parents=True)
        (msvc_include / "excpt.h").write_text("", encoding="utf-8")
        (msvc_lib / "vcruntime.lib").write_text("", encoding="utf-8")

        monkeypatch.setattr(nuitka_builder_module, "_windows_sdk_roots_from_registry", lambda: [str(sdk_root)])
        env = {
            "PATH": str(msvc_bin),
            "INCLUDE": str(msvc_include),
            "LIB": str(msvc_lib),
        }

        augmented = nuitka_builder_module._augment_windows_sdk_environment(env)
        forced = nuitka_builder_module._force_msvc_tool_variables(augmented)

        assert nuitka_builder_module._msvc_env_ready(forced)
        assert str(sdk_root / "bin" / sdk_version / "x64") in forced["PATH"]
        assert str(sdk_root / "Include" / sdk_version / "um") in forced["INCLUDE"]
        assert str(sdk_root / "Lib" / sdk_version / "um" / "x64") in forced["LIB"]
        assert str(sdk_root / "Lib" / sdk_version / "um" / "x64") in forced["LIBPATH"]
        assert forced["WindowsSdkBinPath"].rstrip("\\/").endswith(str(sdk_root / "bin" / sdk_version / "x64"))
        assert forced["UniversalCRTSdkDir"].rstrip("\\/").endswith(str(sdk_root))
        assert forced["MSSDK_DIR"].rstrip("\\/").endswith(str(sdk_root))
        assert forced["WindowsSDKVersion"] == sdk_version
        assert forced["CC"].endswith("cl.exe")
        assert forced["CXX"].endswith("cl.exe")
        assert "LINK" not in forced
        assert forced["RC"].endswith("rc.exe")
        assert forced["MT"].endswith("mt.exe")

    def test_force_msvc_tool_variables_removes_link_environment_options(self, tmp_path):
        tool_dir = tmp_path / "Program Files" / "MSVC" / "bin"
        tool_dir.mkdir(parents=True)
        for tool_name in ("cl.exe", "link.exe", "rc.exe", "mt.exe"):
            tool_path = tool_dir / tool_name
            tool_path.write_text("", encoding="utf-8")
            tool_path.chmod(0o755)

        forced = nuitka_builder_module._force_msvc_tool_variables({
            "PATH": str(tool_dir),
            "LINK": str(tool_dir / "link.exe"),
            "_LINK_": str(tool_dir / "link.exe"),
        })

        assert forced["CC"].endswith("cl.exe")
        assert forced["CXX"].endswith("cl.exe")
        assert "LINK" not in forced
        assert "_LINK_" not in forced
        assert forced["RC"].endswith("rc.exe")
        assert forced["MT"].endswith("mt.exe")

    def test_sdk_only_environment_is_not_ready_without_msvc_headers_and_libs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nuitka_builder_module.sys, "platform", "win32")

        sdk_root = tmp_path / "Windows Kits" / "10"
        sdk_version = "10.0.26100.0"
        for relative_dir in (
            f"Include/{sdk_version}/ucrt",
            f"Include/{sdk_version}/shared",
            f"Include/{sdk_version}/um",
            f"Lib/{sdk_version}/ucrt/x64",
            f"Lib/{sdk_version}/um/x64",
            f"bin/{sdk_version}/x64",
        ):
            (sdk_root / relative_dir).mkdir(parents=True, exist_ok=True)
        (sdk_root / "Include" / sdk_version / "um" / "Windows.h").write_text("", encoding="utf-8")
        for tool_name in ("rc.exe", "mt.exe"):
            tool_path = sdk_root / "bin" / sdk_version / "x64" / tool_name
            tool_path.write_text("", encoding="utf-8")
            tool_path.chmod(0o755)

        msvc_bin = tmp_path / "msvc" / "bin"
        msvc_bin.mkdir(parents=True)
        for tool_name in ("cl.exe", "link.exe"):
            tool_path = msvc_bin / tool_name
            tool_path.write_text("", encoding="utf-8")
            tool_path.chmod(0o755)

        monkeypatch.setattr(nuitka_builder_module, "_windows_sdk_roots_from_registry", lambda: [str(sdk_root)])
        env = nuitka_builder_module._augment_windows_sdk_environment({"PATH": str(msvc_bin)})

        missing = nuitka_builder_module._msvc_env_missing_parts(env)
        assert "MSVC INCLUDE (excpt.h)" in missing
        assert "MSVC LIB (vcruntime.lib)" in missing
        assert not nuitka_builder_module._msvc_env_ready(env)

    def test_windows_sdk_roots_include_explicit_override(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nuitka_builder_module.sys, "platform", "win32")
        sdk_root = tmp_path / "custom-sdk"
        sdk_root.mkdir()
        monkeypatch.setenv("INFERNUX_WINDOWS_SDK_DIR", str(sdk_root))
        monkeypatch.setattr(nuitka_builder_module, "_windows_sdk_roots_from_registry", lambda: [])

        assert str(sdk_root) in nuitka_builder_module._windows_sdk_roots({})

    def test_msvc_environment_scripts_include_explicit_vs_root(self, tmp_path, monkeypatch):
        vs_root = tmp_path / "VS"
        script_path = vs_root / "Common7" / "Tools" / "VsDevCmd.bat"
        script_path.parent.mkdir(parents=True)
        script_path.write_text("", encoding="utf-8")
        monkeypatch.setenv("INFERNUX_VSINSTALLDIR", str(vs_root))
        monkeypatch.delenv("INFERNUX_VCVARSALL", raising=False)
        monkeypatch.setattr(nuitka_builder_module, "_visual_studio_roots_from_vswhere", lambda: [])
        monkeypatch.setattr(nuitka_builder_module, "_visual_studio_roots_from_registry", lambda: [])

        assert (str(script_path), ["-arch=x64", "-host_arch=x64"]) in nuitka_builder_module._find_msvc_environment_scripts()

    def test_windows_nuitka_command_does_not_force_msvc_latest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nuitka_builder_module.sys, "platform", "win32")
        monkeypatch.setattr(nuitka_builder_module, "_has_msvc_toolchain", lambda: True)

        builder = object.__new__(NuitkaBuilder)
        builder._builder_python = "python"
        builder.console_mode = "disable"
        builder._staging_dir = str(tmp_path / "stage")
        builder.output_filename = "Game.exe"
        builder.lto = False
        builder.extra_include_packages = []
        builder.extra_include_data = []
        builder.raw_copy_packages = []
        builder.product_name = "Game"
        builder.file_version = "1.0.0.0"
        builder.icon_path = None
        builder._staged_entry = str(tmp_path / "boot.py")

        cmd = builder._build_command()

        assert "--msvc=latest" not in cmd
        assert "--standalone" in cmd
        assert "--follow-imports" in cmd
        assert "--include-module=_InfernuxBootstrap" in cmd
        assert "--include-module=ctypes" in cmd
        assert "--include-module=bz2" not in cmd
        assert "--include-module=lzma" not in cmd
