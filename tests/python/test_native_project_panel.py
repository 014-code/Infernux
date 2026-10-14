"""Tests for native ProjectPanel."""
import os
import json
import tempfile
from pathlib import Path
import pytest
from infernux.lib import AssetMutationResult, ProjectPanel
from infernux.engine.ui.project_file_ops import (
    SCRIPT_TEMPLATE,
    create_material,
    create_physic_material,
    create_prefab_from_gameobject,
    create_particlegraph,
    create_render_effect,
    create_render_effect_group,
)


class TestProjectPanelCreation:

    def test_script_template_imports_component_surface(self):
        assert "import infernux as inx" in SCRIPT_TEMPLATE
        assert "class {class_name}(inx.InxComponent):" in SCRIPT_TEMPLATE

        namespace = {}
        exec(SCRIPT_TEMPLATE.format(class_name="ExampleComponent"), namespace)
        assert issubclass(namespace["ExampleComponent"], namespace["inx"].InxComponent)


    def test_is_editor_panel(self):
        from infernux.lib import EditorPanel
        pp = ProjectPanel()
        assert isinstance(pp, EditorPanel)

    def test_window_id(self):
        pp = ProjectPanel()
        assert pp.get_window_id() == "project"

    def test_default_open(self):
        pp = ProjectPanel()
        assert pp.is_open()

    def test_explicit_global_rename_target_bridges_empty_native_selection(self):
        pp = ProjectPanel()
        target = "C:/Project/Assets/Smoke.mat"

        assert pp.can_rename_selected_asset(target)
        assert pp.begin_rename_selected_asset(target)

    def test_explicit_create_target_replaces_stale_native_selection(self):
        pp = ProjectPanel()
        pp.set_selected_file("C:/Project/Assets/Previous.mat", False)
        target = "C:/Project/Assets/NewMaterial.mat"

        assert pp.can_rename_selected_asset(target)
        assert pp.begin_rename_selected_asset(target)

    def test_create_physic_material_writes_strict_document_and_imports(self, tmp_path):
        class RecordingAssetDatabase:
            def __init__(self):
                self.paths = []

            def import_asset(self, path):
                self.paths.append(path)
                result = AssetMutationResult()
                result.succeeded = True
                result.database_committed = True
                result.changed = True
                result.guid = "physic-material-guid"
                return result

        database = RecordingAssetDatabase()
        ok, error = create_physic_material(str(tmp_path), "Ice", database)

        assert ok is True, error
        path = tmp_path / "Ice.physicMaterial"
        assert database.paths == [str(path)]
        assert json.loads(path.read_text(encoding="utf-8")) == pytest.approx({
            "friction": 0.6,
            "bounciness": 0.0,
            "friction_combine": 0,
            "bounce_combine": 0,
        })

    def test_create_render_effect_assets_write_current_documents(self, tmp_path):
        from infernux.renderstack.render_effect_asset import (
            RenderEffectAsset,
            RenderEffectGroupAsset,
            parse_render_effect_document,
        )

        ok, error = create_render_effect(
            str(tmp_path), "Pixels", "infernux.route.pixelation"
        )
        assert ok is True, error
        effect = parse_render_effect_document(
            (tmp_path / "Pixels.effect").read_text(encoding="utf-8")
        )
        assert isinstance(effect, RenderEffectAsset)
        assert effect.feature_type == "infernux.route.pixelation"
        assert effect.parameters == {}

        ok, error = create_render_effect_group(str(tmp_path), "Post")
        assert ok is True, error
        group = parse_render_effect_document(
            (tmp_path / "Post.effectgroup").read_text(encoding="utf-8")
        )
        assert isinstance(group, RenderEffectGroupAsset)
        assert group.entries == ()

    def test_create_material_writes_current_document(self, tmp_path, engine):
        from infernux.lib import InxMaterial

        class RecordingAssetDatabase:
            def __init__(self):
                self.paths = []

            def import_asset(self, path):
                self.paths.append(path)
                result = AssetMutationResult()
                result.succeeded = True
                result.database_committed = True
                result.changed = True
                result.guid = "material-guid"
                return result

        database = RecordingAssetDatabase()
        ok, error = create_material(str(tmp_path), "NewMaterial", database)

        assert ok is True, error
        path = tmp_path / "NewMaterial.mat"
        assert database.paths == [str(path)]
        document = json.loads(path.read_text(encoding="utf-8"))
        assert "material_version" not in document
        assert document["shaders"]["vertex"]["shader_id"] == "Standard"
        assert document["shaders"]["fragment"]["shader_id"] == "Lit"
        assert document["name"] == "NewMaterial"
        assert document["builtin"] is False
        material = InxMaterial()
        assert material.deserialize_document(document) is True
        assert material.name == "NewMaterial"

    def test_new_material_import_primes_native_preview(self, tmp_path, monkeypatch):
        from infernux.core.assets import AssetManager

        path = tmp_path / "Fresh.mat"
        path.write_text('{"name":"Fresh"}', encoding="utf-8")

        class Native:
            def __init__(self):
                self.queries = []
                self.full_speed_requests = 0

            def query_or_schedule_material_preview(self, *args):
                self.queries.append(args)
                return 0

            def request_full_speed_frame(self):
                self.full_speed_requests += 1

        native = Native()
        monkeypatch.setattr(AssetManager, "_native_engine", classmethod(lambda cls: native))

        AssetManager._prime_material_preview(str(path))

        normalized = os.path.normpath(str(path))
        assert native.queries == [(f"mat|{normalized}", normalized, "", path.stat().st_mtime_ns, False)]
        assert native.full_speed_requests == 1

        native.queries.clear()
        AssetManager._prime_material_preview(str(path), '{"name":"Fresh"}')
        assert native.queries == [(
            f"mat|{normalized}", normalized, '{"name":"Fresh"}', 0, True,
        )]

    def test_create_prefab_links_the_saved_source(self, engine, scene, tmp_path, monkeypatch):
        from infernux.engine import prefab_manager

        database = engine.get_asset_database()
        directory = Path(database.assets_root) / tmp_path.name
        directory.mkdir()
        source = scene.create_game_object("CheckpointGate")
        linked = []
        original_link = prefab_manager._link_created_prefab_source

        def link_saved_source(game_object, path, owner):
            assert Path(path).is_file()
            assert owner.get_guid_from_path(path)
            linked.append((game_object, path, owner))
            return original_link(game_object, path, owner)

        monkeypatch.setattr(prefab_manager, "_link_created_prefab_source", link_saved_source)
        result = create_prefab_from_gameobject(source, str(directory), database)
        ok, path = result

        assert ok is True
        assert path == str(directory / "CheckpointGate.prefab") == result.created_path
        assert linked == [(source, path, database)]
        assert source.prefab_guid == database.get_guid_from_path(path)


class TestProjectPanelPaths:

            # No crash

    def test_get_set_current_path(self):
        pp = ProjectPanel()
        with tempfile.TemporaryDirectory() as d:
            pp.set_root_path(d)
            assert pp.can_navigate_to_path(d)
            assert pp.set_current_path(d)
            assert pp.get_current_path() == d

    def test_project_worktree_navigation_is_not_limited_to_assets(self, tmp_path):
        assets = tmp_path / "Assets"
        asset_folder = assets / "Materials"
        library_folder = tmp_path / "Library" / "Resources" / "shaders"
        asset_folder.mkdir(parents=True)
        library_folder.mkdir(parents=True)
        outside = tmp_path.parent / "OutsideProject"
        outside.mkdir(exist_ok=True)

        pp = ProjectPanel()
        pp.set_root_path(str(tmp_path))

        for path in (tmp_path, assets, asset_folder, library_folder):
            assert pp.can_navigate_to_path(str(path))
            assert pp.set_current_path(str(path))
            assert pp.get_current_path() == str(path)

        assert pp.can_navigate_to_path(str(outside)) is False
        assert pp.set_current_path(str(outside)) is False

    def test_set_current_path_empty(self):
        pp = ProjectPanel()
        assert pp.set_current_path("") is False
        assert pp.get_current_path() == ""

    def test_selection_projection_does_not_navigate_to_asset_parent(self, tmp_path):
        assets = tmp_path / "Assets"
        first = assets / "First"
        second = assets / "Second"
        first.mkdir(parents=True)
        second.mkdir()
        asset = second / "Selected.mat"
        asset.write_text("{}", encoding="utf-8")
        pp = ProjectPanel()
        pp.set_root_path(str(tmp_path))
        assert pp.set_current_path(str(first))

        pp.set_selected_file(str(asset), False)

        assert pp.get_current_path() == str(first)

            # No crash


class TestProjectPanelCallbacks:

    def test_translate_callback(self):
        pp = ProjectPanel()
        pp.translate = lambda key: f"[{key}]"
        assert pp.translate("project.create_folder") == "[project.create_folder]"

    def test_external_drop_is_forwarded_as_one_global_command(self, tmp_path):
        assets = tmp_path / "Assets"
        assets.mkdir()
        pp = ProjectPanel()
        pp.set_root_path(str(tmp_path))
        pp.set_current_path(str(assets))
        calls = []
        pp.execute_command = (
            lambda command_id, source, argument: calls.append(
                (command_id, source, json.loads(argument))
            )
            or True
        )

        pp.receive_dropped_files(["C:/External/One.png", "C:/External/Two.png"])

        assert calls == [
            (
                "asset.import_external",
                "drag_drop",
                {
                    "paths": ["C:/External/One.png", "C:/External/Two.png"],
                    "destination": str(assets),
                },
            )
        ]

    def test_selection_snapshot_reports_all_paths_and_supports_silent_sync(self, tmp_path):
        first = tmp_path / "First.mat"
        second = tmp_path / "Second.mat"
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")
        pp = ProjectPanel()
        pp.set_root_path(str(tmp_path))
        received = []
        pp.on_selection_changed = (
            lambda paths, primary: received.append((list(paths), primary))
        )

        pp.set_selected_file(str(first))
        pp.clear_selection(False)
        pp.set_selected_file(str(second), False)

        assert received == [([str(first)], str(first))]

    def test_set_selected_files_publishes_one_multi_selection_snapshot(self, tmp_path):
        first = tmp_path / "First.mat"
        second = tmp_path / "Second.mat"
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")
        pp = ProjectPanel()
        pp.set_root_path(str(tmp_path))
        received = []
        pp.on_selection_changed = (
            lambda paths, primary: received.append((list(paths), primary))
        )

        pp.set_selected_files([str(first), str(second)], str(first))

        assert received == [([str(first), str(second)], str(first))]

    def test_on_state_changed_callback(self):
        pp = ProjectPanel()
        called = []
        pp.on_state_changed = lambda: called.append(True)
        assert pp.on_state_changed is not None

    def test_project_command_adapters_share_one_selection_contract(self, tmp_path):
        asset = tmp_path / "Selected.mat"
        asset.write_text("{}", encoding="utf-8")
        pp = ProjectPanel()
        pp.set_root_path(str(tmp_path))
        pp.set_selected_file(str(asset))
        assert pp.has_selected_assets() is True
        assert pp.can_rename_selected_asset() is True
        assert pp.can_rename_selected_asset(str(asset)) is True


    def test_folder_rename_maps_nested_asset_paths(self, tmp_path, monkeypatch):
        from infernux.engine.ui import project_file_ops

        source = tmp_path / "OldFolder"
        nested = source / "Nested"
        nested.mkdir(parents=True)
        (source / "root.mat").write_text("{}", encoding="utf-8")
        (nested / "child.png").write_bytes(b"png")

        moved = []
        monkeypatch.setattr(
            project_file_ops,
            "_notify_asset_moved",
            lambda old, new, _database=None: moved.append((Path(old), Path(new))),
        )

        class _Database:
            project_root = str(tmp_path)

            @staticmethod
            def get_guid_from_path(_path):
                return ""

        destination = project_file_ops.do_rename(
            str(source), "RenamedFolder", _Database()
        )

        expected = tmp_path / "RenamedFolder"
        assert Path(destination) == expected.resolve()
        assert not source.exists()
        assert (expected / "root.mat").is_file()
        assert (expected / "Nested" / "child.png").is_file()
        assert set(moved) == {
            (source.resolve() / "root.mat", expected.resolve() / "root.mat"),
            (
                source.resolve() / "Nested" / "child.png",
                expected.resolve() / "Nested" / "child.png",
            ),
        }


    def test_project_callbacks_never_forge_a_missing_command_context(self):
        bootstrap = Path("python/infernux/engine/bootstrap_project.py").read_text(
            encoding="utf-8"
        )

        assert "_activate_project_command_context" not in bootstrap


    def test_guid_callbacks(self):
        pp = ProjectPanel()
        pp.get_guid_from_path = lambda path: "guid-123" if path else ""
        pp.get_path_from_guid = lambda guid: "/test.txt" if guid else ""

        assert pp.get_guid_from_path("/test.txt") == "guid-123"
        assert pp.get_path_from_guid("guid-123") == "/test.txt"

    def test_invalidate_asset_inspector_callback(self):
        pp = ProjectPanel()
        invalidated = []
        pp.invalidate_asset_inspector = lambda path: invalidated.append(path)
        pp.invalidate_asset_inspector("/asset.mat")
        assert invalidated == ["/asset.mat"]


class TestProjectPanelPublicAPI:


        # No crash — used by selection undo replay

        # No crash — clears internal thumbnail cache entry

    def test_set_open(self):
        pp = ProjectPanel()
        pp.set_open(False)
        assert not pp.is_open()
        pp.set_open(True)
        assert pp.is_open()
