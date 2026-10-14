from __future__ import annotations

from test_ui_logic import (
    pytest,
)

def test_ui_scalar_reassignment_does_not_invalidate_runtime_commands(monkeypatch):
    from infernux.ui import UIText
    from infernux.ui.ui_render_revision import get_runtime_ui_revision
    from infernux.ui import ui_render_revision

    text = UIText()
    initial_global = get_runtime_ui_revision()
    invalidated = []
    monkeypatch.setattr(ui_render_revision, '_invalidate_ui_command_groups', invalidated.append)

    text.text = text.text
    assert get_runtime_ui_revision() == initial_global
    assert invalidated == []

    text.text = "Changed"
    assert get_runtime_ui_revision() == initial_global + 1
    assert invalidated == [text]


def test_persistent_event_combo_preserves_temporarily_unresolved_method():
    from infernux.engine.ui.inspector_ui_components import _persistent_event_combo_options

    labels, values = _persistent_event_combo_options(
        "toggle_settings", [], "None"
    )

    assert labels == ["None", "toggle_settings"]
    assert values == ["", "toggle_settings"]


class TestUIButtonPersistentDispatch:
    class _TargetRef:
        def __init__(self, target):
            self._target = target

        def resolve(self):
            return self._target

    class _GameObject:
        id = 31
        name = "Menu Controller"

        def __init__(self, component):
            self._component = component

        def get_py_components(self):
            return [self._component]

    @staticmethod
    def _entry(target, method_name):
        from infernux.ui.ui_event_entry import UIEventEntry

        entry = UIEventEntry(
            component_name="MenuController",
            method_name=method_name,
            arguments=[],
        )
        entry.__dict__["target"] = TestUIButtonPersistentDispatch._TargetRef(target)
        return entry

    def test_invokes_bound_component_method_and_records_result(self):
        from infernux.ui import UIButton

        class MenuController:
            def __init__(self):
                self.called = False

            def toggle_settings(self):
                self.called = True

        component = MenuController()
        target = self._GameObject(component)
        button = UIButton()
        button.on_click_entries = [self._entry(target, "toggle_settings")]

        button._dispatch_persistent_entries()

        assert component.called is True
        assert button.debug_dispatch_state() == [{
            "index": 0,
            "component_name": "MenuController",
            "method_name": "toggle_settings",
            "status": "invoked",
            "target_id": 31,
            "target_name": "Menu Controller",
        }]

    def test_missing_method_is_reported_instead_of_silently_ignored(self, monkeypatch):
        from infernux.debug import Debug
        from infernux.ui import UIButton

        class MenuController:
            pass

        errors = []
        monkeypatch.setattr(Debug, "log_error", errors.append)
        target = self._GameObject(MenuController())
        button = UIButton()
        button.on_click_entries = [self._entry(target, "toggle_settings")]

        button._dispatch_persistent_entries()

        assert button.debug_dispatch_state()[0]["status"] == "missing_method"
        assert errors == [
            "UIButton persistent event could not invoke "
            "Menu Controller.MenuController.toggle_settings: missing_method"
        ]


def test_focused_save_rejects_stale_document_instead_of_saving_scene():
    from infernux.engine.interaction import (
        DocumentCapability,
        DocumentKind,
        DocumentRegistry,
        EditorSaveService,
        FocusService,
    )
    from infernux.engine.scene_manager import SceneFileManager

    class Controller:
        calls = 0

        def save(self, *, ticket, save_as=False):
            self.calls += 1
            DocumentRegistry.instance().complete_save(ticket.ticket_id, success=True)

    registry = DocumentRegistry()
    scene_controller = Controller()
    scene = registry.create(
        DocumentKind.SCENE,
        "Scene",
        document_id="scene-document",
        capabilities=DocumentCapability.SAVE | DocumentCapability.SAVE_AS,
        controller=scene_controller,
    )
    scene.revision = 1
    previous_scene = SceneFileManager._instance
    previous_focus = FocusService._instance
    previous_saving = EditorSaveService._instance
    SceneFileManager._instance = type("SceneFiles", (), {"document_id": scene.document_id})()
    focus = FocusService()
    saving = EditorSaveService(registry)
    try:
        focus.activate_panel(
            "timeline",
            view_id="timeline",
            document_id="missing-document",
        )
        result = saving.save_focused()
    finally:
        EditorSaveService._instance = previous_saving
        FocusService._instance = previous_focus
        SceneFileManager._instance = previous_scene

    assert result.accepted is False
    assert "no longer registered" in result.result.message
    assert scene_controller.calls == 0


class TestPanelFocusEvents:
    def test_scene_change_binds_every_scene_backed_view_to_one_document(self):
        from infernux.engine.bootstrap import EditorBootstrap
        from infernux.engine.interaction import SelectionService

        class SceneFiles:
            document_id = "scene-document"
            callback = None

            def set_on_scene_changed(self, callback):
                self.callback = callback

        class View:
            def __init__(self):
                self.bound = []

            def bind_document(self, document_id, **options):
                self.bound.append((document_id, options))

        bootstrap = EditorBootstrap.__new__(EditorBootstrap)
        bootstrap.scene_file_manager = SceneFiles()
        bootstrap.scene_view = View()
        bootstrap.scene_view._fly_to_active = True
        bootstrap.scene_view._fly_to_last_obj_id = 17
        bootstrap.scene_view._fly_to_close = True
        bootstrap.game_view = View()
        bootstrap.ui_editor = View()

        previous_selection = SelectionService._instance
        SelectionService()
        try:
            bootstrap._setup_scene_change_cleanup()
            bootstrap.scene_file_manager.callback()
        finally:
            SelectionService._instance = previous_selection

        expected = [("scene-document", {"preserve_previous": True})]
        assert bootstrap.scene_view.bound == expected
        assert bootstrap.game_view.bound == expected
        assert bootstrap.ui_editor.bound == expected

    def test_document_binding_projects_focus_without_user_history(self):
        from infernux.engine.interaction import (
            DocumentKind,
            DocumentRegistry,
            FocusService,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel

        previous_focus = FocusService._instance
        previous_registry = DocumentRegistry._instance
        focus = FocusService()
        registry = DocumentRegistry()
        changes = []
        focus.add_change_listener(changes.append)
        try:
            panel = ClosablePanel("Graph", "graph")
            document = registry.create(DocumentKind.GENERIC, "Graph")
            focus.activate_panel("graph", view_id="graph", record_history=False)
            changes.clear()

            panel.bind_document(document.document_id)
            assert focus.snapshot.active_document_id == document.document_id
            assert [change.record_history for change in changes] == [False]

            changes.clear()
            panel.unbind_document()
            assert focus.snapshot.active_document_id == ""
            assert [change.record_history for change in changes] == [False]
        finally:
            DocumentRegistry._instance = previous_registry
            FocusService._instance = previous_focus

    def test_loaded_document_switch_detaches_view_without_retiring_previous_document(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel

        previous_registry = DocumentRegistry._instance
        registry = DocumentRegistry()
        try:
            scene_a = registry.create(DocumentKind.SCENE, "Scene A")
            scene_b = registry.create(DocumentKind.SCENE, "Scene B")
            panel = ClosablePanel("Scene", "scene_view")
            panel.bind_document(scene_a.document_id)

            panel.bind_document(scene_b.document_id, preserve_previous=True)

            assert registry.get(scene_a.document_id) is scene_a
            assert registry.get(scene_b.document_id) is scene_b
            assert registry.document_for_view("scene_view") is scene_b
            assert not scene_a.view_ids
            assert scene_b.view_ids == {"scene_view"}
        finally:
            DocumentRegistry._instance = previous_registry

    def test_closable_panel_publishes_focus_only_through_focus_service(self):
        from infernux.engine.ui.closable_panel import ClosablePanel

        class FocusContext:
            def set_window_focus(self):
                pass

        received = []
        from infernux.engine.interaction import FocusService

        previous_focus = FocusService._instance
        focus = FocusService()
        focus.add_listener(received.append)
        try:
            panel = ClosablePanel("Focus Test", "focus_test")
            panel._activate_panel(FocusContext(), focus_window=True)
            panel._activate_panel(FocusContext(), focus_window=True)
            assert [snapshot.active_view_id for snapshot in received] == [
                "focus_test"
            ]
            assert not hasattr(ClosablePanel, "set_on_panel_focus_changed")
        finally:
            FocusService._instance = previous_focus

    def test_untyped_editor_event_bus_cannot_be_reintroduced(self):
        from pathlib import Path

        root = Path("python/infernux/engine")
        assert not (root / "ui" / "event_bus.py").exists()
        assert all(
            "EditorEventBus" not in path.read_text(encoding="utf-8")
            for path in root.rglob("*.py")
        )

    def test_closable_panel_keeps_child_window_focus_as_panel_focus(self):
        from infernux.engine.ui.closable_panel import ClosablePanel

        class FocusContext:
            def __init__(self):
                self.focus_flags = []

            @staticmethod
            def begin_window_closable(_title, _open, _flags):
                return True, True

            @staticmethod
            def is_window_hovered(_flags):
                return False

            @staticmethod
            def is_mouse_button_clicked(_button):
                return False

            def is_window_focused(self, flags):
                self.focus_flags.append(flags)
                return flags == 1

        panel = ClosablePanel("Child Focus Test", "child_focus_test")
        ctx = FocusContext()
        from infernux.engine.interaction import FocusService

        previous_focus = FocusService._instance
        focus = FocusService()
        try:
            focus.activate_panel(panel.window_id)
            panel._panel_was_focused = True

            assert panel._begin_closable_window(ctx) is True
            assert ClosablePanel.get_active_panel_id() == panel.window_id
            assert ctx.focus_flags == [1]
        finally:
            FocusService._instance = previous_focus

    def test_closable_panel_records_pointer_driven_dock_tab_focus(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.closable_panel import ClosablePanel

        class FocusContext:
            @staticmethod
            def begin_window_closable(_title, _open, _flags):
                return True, True

            @staticmethod
            def is_window_hovered(_flags):
                return False

            @staticmethod
            def is_mouse_button_clicked(button):
                return button == 0

            @staticmethod
            def is_window_focused(_flags):
                return True

        previous_focus = FocusService._instance
        focus = FocusService()
        changes = []
        focus.add_change_listener(changes.append)
        try:
            focus.activate_panel("scene", record_history=False)
            changes.clear()
            panel = ClosablePanel("Docked", "docked")

            assert panel._begin_closable_window(FocusContext()) is True
            assert focus.snapshot.active_panel_id == "docked"
            assert len(changes) == 1
            assert changes[0].record_history is True
        finally:
            FocusService._instance = previous_focus

    def test_closable_panel_does_not_claim_click_consumed_by_popup(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.closable_panel import ClosablePanel

        class FocusContext:
            @staticmethod
            def begin_window_closable(_title, _open, _flags):
                return True, True

            @staticmethod
            def is_window_hovered(_flags):
                return True

            @staticmethod
            def is_mouse_button_clicked(button):
                return button == 0

            @staticmethod
            def is_window_focused(_flags):
                return True

            @staticmethod
            def is_pointer_activation_blocked_by_popup():
                return True

        previous_focus = FocusService._instance
        focus = FocusService()
        changes = []
        focus.add_change_listener(changes.append)
        try:
            focus.activate_panel("scene", record_history=False)
            changes.clear()
            panel = ClosablePanel("History", "history")

            assert panel._begin_closable_window(FocusContext()) is True
            assert focus.snapshot.active_panel_id == "scene"
            assert changes == []
            assert panel._last_pointer_press_at == 0.0
        finally:
            FocusService._instance = previous_focus

    def test_closable_panel_does_not_record_focus_when_already_visible(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.closable_panel import ClosablePanel

        class FocusContext:
            @staticmethod
            def begin_window_closable(_title, _open, _flags):
                return True, True

            @staticmethod
            def is_window_hovered(_flags):
                return True

            @staticmethod
            def is_mouse_button_clicked(button):
                return button == 0

            @staticmethod
            def is_window_focused(_flags):
                return True

            @staticmethod
            def set_window_focus():
                pass

        previous_focus = FocusService._instance
        focus = FocusService()
        changes = []
        focus.add_change_listener(changes.append)
        try:
            focus.activate_panel("scene", record_history=False)
            changes.clear()
            panel = ClosablePanel("Docked", "docked")
            panel._content_visible_previous_frame = True

            assert panel._begin_closable_window(FocusContext()) is True
            assert focus.snapshot.active_panel_id == "docked"
            assert len(changes) == 1
            assert changes[0].record_history is False
        finally:
            FocusService._instance = previous_focus

    def test_closable_panel_carries_dock_press_to_next_focus_frame(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.closable_panel import ClosablePanel

        class FocusContext:
            def __init__(self):
                self.visible = False
                self.clicked = True

            def begin_window_closable(self, _title, _open, _flags):
                return self.visible, True

            @staticmethod
            def is_window_hovered(_flags):
                return False

            def is_mouse_button_clicked(self, button):
                return self.clicked and button == 0

            def is_window_focused(self, _flags):
                return self.visible

        previous_focus = FocusService._instance
        focus = FocusService()
        changes = []
        focus.add_change_listener(changes.append)
        try:
            focus.activate_panel("scene", record_history=False)
            changes.clear()
            panel = ClosablePanel("Docked", "docked")
            ctx = FocusContext()

            assert panel._begin_closable_window(ctx) is False
            ctx.visible = True
            ctx.clicked = False
            assert panel._begin_closable_window(ctx) is True

            assert focus.snapshot.active_panel_id == "docked"
            assert len(changes) == 1
            assert changes[0].record_history is True
        finally:
            FocusService._instance = previous_focus

    def test_hidden_dock_tab_clears_stale_focus_latch_before_reveal(self):
        from infernux.engine.interaction import FocusService
        from infernux.engine.ui.closable_panel import ClosablePanel

        class FocusContext:
            def __init__(self):
                self.visible = False
                self.clicked = False

            def begin_window_closable(self, _title, _open, _flags):
                return self.visible, True

            @staticmethod
            def is_window_hovered(_flags):
                return False

            def is_mouse_button_clicked(self, button):
                return self.clicked and button == 0

            def is_window_focused(self, _flags):
                return self.visible

        previous_focus = FocusService._instance
        focus = FocusService()
        changes = []
        focus.add_change_listener(changes.append)
        try:
            panel = ClosablePanel("Scene", "scene_view")
            panel._panel_was_focused = True
            focus.activate_panel(
                "particle_graph_editor",
                view_id="particle_graph_editor",
                record_history=False,
            )
            changes.clear()
            ctx = FocusContext()

            # The Scene tab is still submitted by the dock host, but its
            # content is hidden behind Particle Graph.
            assert panel._begin_closable_window(ctx) is False
            assert panel._panel_was_focused is False
            assert focus.snapshot.active_view_id == "particle_graph_editor"

            # Revealing Scene must now create an independent navigation item.
            ctx.visible = True
            ctx.clicked = True
            assert panel._begin_closable_window(ctx) is True

            assert focus.snapshot.active_view_id == "scene_view"
            assert len(changes) == 1
            assert changes[0].record_history is True
        finally:
            FocusService._instance = previous_focus

    def test_non_authoring_panel_does_not_create_a_legacy_document(self):
        from infernux.engine.interaction import DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel

        panel = ClosablePanel("Probe", "dirty_probe")
        panel._sync_dirty_registry()
        panel._sync_dirty_registry()

        assert DocumentRegistry.instance().document_for_view(panel.window_id) is None

    def test_bound_document_dirty_state_is_a_per_view_panel_title_capability(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel

        previous_registry = DocumentRegistry._instance
        registry = DocumentRegistry()
        try:
            document = registry.create(DocumentKind.PROJECT_SETTINGS, "Settings")
            panel = ClosablePanel("Settings", "settings")
            panel.bind_document(document.document_id)

            assert panel._window_title_suffix() == ""
            registry.mark_changed(document.document_id)
            assert panel._window_title_suffix() == " *"
            registry.mark_saved(document.document_id)
            assert panel._window_title_suffix() == ""
        finally:
            DocumentRegistry._instance = previous_registry

    def test_shared_scene_document_does_not_broadcast_dirty_titles_to_every_view(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel

        previous_registry = DocumentRegistry._instance
        registry = DocumentRegistry()
        try:
            document = registry.create(DocumentKind.SCENE, "Main")
            scene = ClosablePanel("Scene", "scene_view")
            game = ClosablePanel("Game", "game_view")
            ui = ClosablePanel("UI Editor", "ui_editor")
            for panel in (scene, game, ui):
                panel.bind_document(document.document_id)

            registry.mark_changed(document.document_id, view_id="ui_editor")
            assert scene._window_title_suffix() == ""
            assert game._window_title_suffix() == ""
            assert ui._window_title_suffix() == " *"

            registry.mark_changed(document.document_id, view_id="scene_view")
            assert scene._window_title_suffix() == " *"
            assert game._window_title_suffix() == ""
            assert ui._window_title_suffix() == " *"

            registry.mark_saved(document.document_id)
            assert all(panel._window_title_suffix() == "" for panel in (scene, game, ui))
        finally:
            DocumentRegistry._instance = previous_registry

    def test_legacy_shared_document_without_owner_chooses_one_view_instead_of_broadcasting(self):
        from infernux.engine.interaction import DocumentKind, DocumentRegistry
        from infernux.engine.ui.closable_panel import ClosablePanel

        previous_registry = DocumentRegistry._instance
        registry = DocumentRegistry()
        try:
            document = registry.create(
                DocumentKind.GENERIC,
                "Shared",
                revision=1,
                saved_revision=0,
            )
            first = ClosablePanel("First", "first")
            second = ClosablePanel("Second", "second")
            first.bind_document(document.document_id)
            second.bind_document(document.document_id)

            assert first._window_title_suffix() == " *"
            assert second._window_title_suffix() == ""
        finally:
            DocumentRegistry._instance = previous_registry

    def test_unbound_authoring_panel_reports_once_without_creating_state(
        self, monkeypatch
    ):
        from infernux.debug import Debug
        from infernux.engine.interaction import (
            DocumentRegistry,
            PanelInteractionDescriptor,
        )
        from infernux.engine.ui.closable_panel import ClosablePanel

        class _AuthoringProbe(ClosablePanel):
            PANEL_INTERACTION = PanelInteractionDescriptor(document_backed=True)

        errors = []
        monkeypatch.setattr(Debug, "log_error", errors.append)
        panel = _AuthoringProbe("Probe", "dirty_probe")
        panel._sync_dirty_registry()
        panel._sync_dirty_registry()

        assert DocumentRegistry.instance().document_for_view(panel.window_id) is None
        assert len(errors) == 1
        assert "has no formal DocumentRegistry binding" in errors[0]


class TestSceneViewPicking:
    def test_particle_query_does_not_hide_scene_lifetime_failure(self, monkeypatch):
        import infernux.lib as infernux_lib
        from infernux.engine.ui import _scene_view_picking as picking

        class SceneManager:
            @staticmethod
            def instance():
                raise RuntimeError("scene lifetime failure")

        monkeypatch.setattr(infernux_lib, "SceneManager", SceneManager)

        with pytest.raises(RuntimeError, match="scene lifetime failure"):
            picking._owns_particle_system(42)

    def test_pick_ray_rebuild_failure_is_not_reordered_as_a_valid_hit(self):
        from infernux.engine.ui._scene_view_picking import SceneViewPickingMixin

        class Engine:
            @staticmethod
            def screen_to_world_ray(*_args):
                raise RuntimeError("camera ray unavailable")

        probe = SceneViewPickingMixin()
        probe._engine = Engine()

        with pytest.raises(RuntimeError, match="camera ray unavailable"):
            probe._insert_ids_by_depth([7], [9], 10.0, 12.0, 100.0, 80.0)

    def test_skinned_renderer_counts_as_mesh_pick_geometry(self, monkeypatch):
        import infernux.lib as infernux_lib
        from infernux.components.builtin import MeshRenderer, SkinnedMeshRenderer
        from infernux.engine.ui import _scene_view_picking as picking

        class Object:
            requested_types = []

            @classmethod
            def get_component(cls, component_type):
                cls.requested_types.append(component_type)
                return object() if component_type is SkinnedMeshRenderer else None

        class Scene:
            @staticmethod
            def find_by_id(object_id):
                return Object if object_id == 42 else None

        class SceneManager:
            @staticmethod
            def instance():
                return SceneManager()

            @staticmethod
            def get_active_scene():
                return Scene()

            @staticmethod
            def find_runtime_object_by_id(object_id):
                return Scene.find_by_id(object_id)

        monkeypatch.setattr(infernux_lib, "SceneManager", SceneManager)

        assert picking._has_mesh_pick_geometry(42) is True
        assert Object.requested_types == [MeshRenderer, SkinnedMeshRenderer]

    def test_scene_click_keeps_immediate_cpu_pick_and_queues_gpu_refinement(self):
        from infernux.engine.ui._scene_view_picking import SceneViewPickingMixin

        class Context:
            @staticmethod
            def is_mouse_button_clicked(_button):
                return True

            @staticmethod
            def is_key_down(_key):
                return False

        class Viewport:
            width = 100.0
            height = 80.0

            @staticmethod
            def mouse_local(_ctx):
                return 10.0, 12.0

        class Engine:
            def __init__(self):
                self.requests = []

            def request_scene_object_pick(self, x, y, width, height):
                self.requests.append((x, y, width, height))
                return 7

            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "pending"}

        class PickingProbe(SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._box_select_active = False
                self._pending_scene_pick = None
                self._pick_cycle_candidates = [42]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (10.0, 12.0)
                self._pick_cycle_last_viewport = (100, 80)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

            @staticmethod
            def _pick_scene_object(_ctx, _viewport):
                return 42

        probe = PickingProbe()
        probe._handle_picking_and_selection(
            Context(), Viewport(), gizmo_consumed=False, overlay_hovered=False,
            is_scene_hovered=True, play_border_clr=None,
        )

        assert probe.picked == [(42, False)]
        assert probe._engine.requests == [(10.0, 12.0, 100.0, 80.0)]
        assert probe._pending_scene_pick["cpu_id"] == 42
        assert probe._pending_scene_pick["cpu_candidates"] == [42]

    def test_world_ui_pick_is_not_overwritten_by_delayed_geometry_pick(self):
        from infernux.engine.ui._scene_view_picking import SceneViewPickingMixin

        class Context:
            @staticmethod
            def is_mouse_button_clicked(_button):
                return True

            @staticmethod
            def is_key_down(_key):
                return False

        class Viewport:
            width = 100.0
            height = 80.0

        class Engine:
            def __init__(self):
                self.requests = []

            def request_scene_object_pick(self, *args):
                self.requests.append(args)
                return 7

        class PickingProbe(SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._box_select_active = False
                self._pending_scene_pick = None
                self._last_world_ui_pick_ids = (42,)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append(
                    (object_id, ctrl)
                )

            def _pick_scene_object(self, _ctx, _viewport):
                self._last_world_ui_pick_ids = (42,)
                return 42

        probe = PickingProbe()
        probe._handle_picking_and_selection(
            Context(), Viewport(), gizmo_consumed=False, overlay_hovered=False,
            is_scene_hovered=True, play_border_clr=None,
        )

        assert probe.picked == [(42, False)]
        assert probe._pending_scene_pick is None
        assert probe._engine.requests == []

    def test_scene_click_defers_mesh_selection_until_gpu_refinement(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Context:
            @staticmethod
            def is_mouse_button_clicked(_button):
                return True

            @staticmethod
            def is_key_down(_key):
                return False

        class Viewport:
            width = 100.0
            height = 80.0

            @staticmethod
            def mouse_local(_ctx):
                return 10.0, 12.0

        class Engine:
            @staticmethod
            def request_scene_object_pick(*_args):
                return 7

            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 42}

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._box_select_active = False
                self._pending_scene_pick = None
                self._pick_cycle_candidates = [42]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (10.0, 12.0)
                self._pick_cycle_last_viewport = (100, 80)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

            @staticmethod
            def _pick_scene_object(_ctx, _viewport):
                return 42

        monkeypatch.setattr(picking, "_has_mesh_pick_geometry", lambda object_id: object_id == 42)

        selection = SelectionService.instance()
        previous = selection.snapshot
        selection.clear(reason="mesh_pick_test", record_history=False)
        try:
            probe = PickingProbe()
            probe._handle_picking_and_selection(
                Context(), Viewport(), gizmo_consumed=False, overlay_hovered=False,
                is_scene_hovered=True, play_border_clr=None,
            )
            assert probe.picked == []
            assert probe._pending_scene_pick["selection_deferred"] is True

            probe._poll_scene_object_pick()
            assert probe.picked == [(42, False)]
        finally:
            selection.apply_snapshot(previous, record_history=False)

    def test_scene_icon_owner_with_mesh_selects_without_gpu_overwrite(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking

        class Context:
            @staticmethod
            def is_mouse_button_clicked(_button):
                return True

            @staticmethod
            def is_key_down(_key):
                return False

        class Viewport:
            width = 100.0
            height = 80.0

        class Engine:
            def __init__(self):
                self.requests = []

            def request_scene_object_pick(self, *args):
                self.requests.append(args)
                return 7

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._box_select_active = False
                self._pending_scene_pick = None
                self._pick_cycle_candidates = [42, 7]
                self._pick_cycle_index = 0
                self._last_scene_icon_pick_ids = (42,)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

            def _pick_scene_object(self, _ctx, _viewport):
                self._last_scene_icon_pick_ids = (42,)
                return 42

        monkeypatch.setattr(picking, "_has_mesh_pick_geometry", lambda object_id: object_id == 42)
        probe = PickingProbe()
        probe._handle_picking_and_selection(
            Context(), Viewport(), gizmo_consumed=False, overlay_hovered=False,
            is_scene_hovered=True, play_border_clr=None,
        )
        assert probe.picked == [(42, False)]
        assert probe._engine.requests == []
        assert probe._pending_scene_pick is None

    def test_scene_icon_precedes_conservative_mesh_bounds_at_same_pixel(self):
        from infernux.engine.ui._scene_view_picking import SceneViewPickingMixin

        class Context:
            @staticmethod
            def get_mouse_pos_x():
                return 421.0

            @staticmethod
            def get_mouse_pos_y():
                return 130.0

        class Viewport:
            image_min_x = 0.0
            image_min_y = 0.0
            width = 975.0
            height = 587.0

            @staticmethod
            def mouse_local(ctx):
                return ctx.get_mouse_pos_x(), ctx.get_mouse_pos_y()

        class Engine:
            @staticmethod
            def pick_scene_icon_object_ids(*_args):
                return [411]

            @staticmethod
            def pick_scene_object_ids(*_args):
                # Large imported-mesh AABBs cross this pixel even though the
                # light icon is the visible editor overlay under the cursor.
                return [290, 291, 292, 293, 294, 411]

            @staticmethod
            def screen_to_world_ray(*_args):
                return None

        class PickingProbe(SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pick_cycle_candidates = []
                self._pick_cycle_index = -1
                self._pick_cycle_last_mouse = (-100.0, -100.0)
                self._pick_cycle_last_viewport = (0, 0)

        probe = PickingProbe()
        assert probe._pick_scene_object(Context(), Viewport()) == 411
        assert probe._pick_cycle_candidates == [411, 290, 291, 292, 293, 294]

    def test_deferred_mesh_selection_falls_back_when_gpu_pick_fails(self):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "failed", "error": "readback unavailable"}

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self, selection):
                self._engine = Engine()
                self._pending_scene_pick = {
                    "request_id": 1,
                    "cpu_id": 42,
                    "cpu_candidates": [42],
                    "selection_deferred": True,
                    "selection_primary": selection.primary_scene_object_id(),
                    "selection_revision": selection.revision,
                    "document_id": "",
                }
                self.document_id = ""
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

        selection = SelectionService.instance()
        previous = selection.snapshot
        selection.clear(reason="mesh_pick_failure_test", record_history=False)
        try:
            probe = PickingProbe(selection)
            probe._poll_scene_object_pick()
            assert probe.picked == [(42, False)]
        finally:
            selection.apply_snapshot(previous, record_history=False)

    def test_particle_refinement_keeps_icon_selection_but_joins_cycle(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 99}

            @staticmethod
            def screen_to_world_ray(*_args):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pending_scene_pick = {
                    "request_id": 1,
                    "x": 10.0,
                    "y": 12.0,
                    "width": 100.0,
                    "height": 80.0,
                    "cpu_id": 42,
                    "cpu_candidates": [42, 7],
                }
                self._pick_cycle_candidates = [42, 7]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (-1.0, -1.0)
                self._pick_cycle_last_viewport = (0, 0)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

        monkeypatch.setattr(picking, "_owns_particle_system", lambda object_id: object_id == 99)
        monkeypatch.setattr(picking, "_is_icon_only_pick_target", lambda object_id: object_id == 42)
        monkeypatch.setattr(picking, "_object_ray_depth", lambda object_id, *_args: {
            42: 1.0,
            99: 2.0,
            7: 3.0,
        }[object_id])

        sel = SelectionService.instance()
        previous = sel.snapshot
        sel.select_scene_object(42, owner_id="scene_view", record_history=False)
        try:
            probe = PickingProbe()
            probe._poll_scene_object_pick()
            assert probe.picked == []
            assert probe._pick_cycle_candidates == [42, 99, 7]
            assert probe._pick_cycle_index == 0
        finally:
            sel.apply_snapshot(previous, record_history=False)

    def test_particle_refinement_corrects_mesh_behind_spray(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 99}

            @staticmethod
            def screen_to_world_ray(*_args):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pending_scene_pick = {
                    "request_id": 1,
                    "x": 10.0,
                    "y": 12.0,
                    "width": 100.0,
                    "height": 80.0,
                    "cpu_id": 7,
                    "cpu_candidates": [7],
                }
                self._pick_cycle_candidates = [7]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (-1.0, -1.0)
                self._pick_cycle_last_viewport = (0, 0)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

        monkeypatch.setattr(picking, "_owns_particle_system", lambda object_id: object_id == 99)
        monkeypatch.setattr(picking, "_is_icon_only_pick_target", lambda object_id: False)
        monkeypatch.setattr(picking, "_object_ray_depth", lambda object_id, *_args: {
            99: 1.0,
            7: 3.0,
        }[object_id])

        sel = SelectionService.instance()
        previous = sel.snapshot
        sel.select_scene_object(7, owner_id="scene_view", record_history=False)
        try:
            probe = PickingProbe()
            probe._poll_scene_object_pick()
            assert probe.picked == [(99, False)]
            assert probe._pick_cycle_candidates == [99, 7]
            assert probe._pick_cycle_index == 0
        finally:
            sel.apply_snapshot(previous, record_history=False)

    def test_particle_without_mesh_is_an_icon_only_pick_target(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking

        monkeypatch.setattr(picking, "_has_mesh_pick_geometry", lambda object_id: object_id == 7)
        monkeypatch.setattr(picking, "_owns_particle_system", lambda object_id: object_id == 99)
        assert picking._is_icon_only_pick_target(99) is True
        assert picking._is_icon_only_pick_target(7) is False
        assert picking._is_icon_only_pick_target(0) is False

    def test_gpu_refinement_does_not_replace_particle_with_mesh_behind(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 7}

            @staticmethod
            def screen_to_world_ray(*_args):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pending_scene_pick = {
                    "request_id": 1,
                    "x": 10.0,
                    "y": 12.0,
                    "width": 100.0,
                    "height": 80.0,
                    "cpu_id": 99,
                    "cpu_candidates": [99],
                }
                self._pick_cycle_candidates = [99]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (-1.0, -1.0)
                self._pick_cycle_last_viewport = (0, 0)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

        monkeypatch.setattr(picking, "_owns_particle_system", lambda object_id: object_id == 99)
        monkeypatch.setattr(picking, "_has_mesh_pick_geometry", lambda object_id: object_id == 7)
        monkeypatch.setattr(picking, "_is_icon_only_pick_target", lambda object_id: object_id == 99)
        monkeypatch.setattr(picking, "_object_ray_depth", lambda object_id, *_args: {
            99: 1.0,
            7: 3.0,
        }[object_id])
        assert picking._is_icon_only_pick_target(99)

        sel = SelectionService.instance()
        previous = sel.snapshot
        sel.select_scene_object(99, owner_id="scene_view", record_history=False)
        try:
            probe = PickingProbe()
            probe._poll_scene_object_pick()
            assert probe.picked == []
            assert probe._pick_cycle_candidates == [99, 7]
            assert probe._pick_cycle_index == 0
        finally:
            sel.apply_snapshot(previous, record_history=False)

    def test_first_click_keeps_selected_particle_among_mesh_candidates(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def pick_scene_icon_object_ids(*_args):
                return []

            @staticmethod
            def pick_scene_object_ids(*_args):
                return [7, 99]

            @staticmethod
            def screen_to_world_ray(*_args):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        class Context:
            @staticmethod
            def mouse_local(_ctx=None):
                return 10.0, 12.0

        class Viewport:
            width = 100.0
            height = 80.0

            @staticmethod
            def mouse_local(_ctx):
                return 10.0, 12.0

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pick_cycle_candidates = []
                self._pick_cycle_index = -1
                self._pick_cycle_last_mouse = (-1.0, -1.0)
                self._pick_cycle_last_viewport = (0, 0)

        monkeypatch.setattr(picking, "_owns_particle_system", lambda object_id: object_id == 99)
        monkeypatch.setattr(
            picking.SceneViewPickingMixin,
            "_current_scene_pick_id",
            staticmethod(lambda: 99),
        )

        sel = SelectionService.instance()
        previous = sel.snapshot
        sel.select_scene_object(99, owner_id="hierarchy", record_history=False)
        try:
            probe = PickingProbe()
            assert probe._current_scene_pick_id() == 99
            assert probe._pick_scene_object(Context(), Viewport()) == 99
            assert probe._pick_cycle_candidates == [7, 99]
            assert probe._pick_scene_object(Context(), Viewport()) == 7
        finally:
            sel.apply_snapshot(previous, record_history=False)

    def test_gpu_refinement_clears_mesh_aabb_false_positive(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 0}

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pending_scene_pick = {
                    "request_id": 1,
                    "x": 10.0,
                    "y": 12.0,
                    "width": 100.0,
                    "height": 80.0,
                    "cpu_id": 7,
                    "cpu_candidates": [7],
                }
                self._pick_cycle_candidates = [7]
                self._pick_cycle_index = 0
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

        monkeypatch.setattr(picking, "_has_mesh_pick_geometry", lambda object_id: object_id == 7)

        selection = SelectionService.instance()
        previous = selection.snapshot
        selection.select_scene_object(7, owner_id="scene_view", record_history=False)
        try:
            probe = PickingProbe()
            probe._poll_scene_object_pick()
            assert probe.picked == [(0, False)]
            assert probe._pick_cycle_candidates == []
            assert probe._pick_cycle_index == -1
        finally:
            selection.apply_snapshot(previous, record_history=False)

    def test_gpu_refinement_corrects_one_mesh_aabb_to_visible_mesh(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 9}

            @staticmethod
            def screen_to_world_ray(*_args):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pending_scene_pick = {
                    "request_id": 1,
                    "x": 10.0,
                    "y": 12.0,
                    "width": 100.0,
                    "height": 80.0,
                    "cpu_id": 7,
                    "cpu_candidates": [7],
                }
                self._pick_cycle_candidates = [7]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (-1.0, -1.0)
                self._pick_cycle_last_viewport = (0, 0)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

        monkeypatch.setattr(picking, "_has_mesh_pick_geometry", lambda object_id: object_id in (7, 9))
        monkeypatch.setattr(picking, "_is_icon_only_pick_target", lambda _object_id: False)
        monkeypatch.setattr(picking, "_object_ray_depth", lambda object_id, *_args: {9: 1.0, 7: 2.0}[object_id])

        selection = SelectionService.instance()
        previous = selection.snapshot
        selection.select_scene_object(7, owner_id="scene_view", record_history=False)
        try:
            probe = PickingProbe()
            probe._poll_scene_object_pick()
            assert probe.picked == [(9, False)]
            assert probe._pick_cycle_candidates == [9, 7]
            assert probe._pick_cycle_index == 0
        finally:
            selection.apply_snapshot(previous, record_history=False)

    def test_gpu_refinement_preserves_intentional_overlap_cycle(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 7}

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pending_scene_pick = {
                    "request_id": 1,
                    "x": 10.0,
                    "y": 12.0,
                    "width": 100.0,
                    "height": 80.0,
                    "cpu_id": 9,
                    "cpu_candidates": [7, 9],
                    "cpu_cycle_index": 1,
                }
                self._pick_cycle_candidates = [7, 9]
                self._pick_cycle_index = 1
                self._pick_cycle_last_mouse = (10.0, 12.0)
                self._pick_cycle_last_viewport = (100, 80)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

        monkeypatch.setattr(picking, "_is_icon_only_pick_target", lambda _object_id: False)

        selection = SelectionService.instance()
        previous = selection.snapshot
        selection.select_scene_object(9, owner_id="scene_view", record_history=False)
        try:
            probe = PickingProbe()
            probe._poll_scene_object_pick()
            assert probe.picked == []
            assert probe._pick_cycle_candidates == [7, 9]
            assert probe._pick_cycle_index == 1
        finally:
            selection.apply_snapshot(previous, record_history=False)

    def test_particle_refinement_cannot_overwrite_a_newer_selection(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking
        from infernux.engine.interaction import SelectionService

        class Engine:
            @staticmethod
            def query_scene_object_pick(_request_id):
                return {"status": "completed", "object_id": 99}

            @staticmethod
            def screen_to_world_ray(*_args):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        selection = SelectionService.instance()
        previous = selection.snapshot
        selection.select_scene_object(7, owner_id="scene_view", record_history=False)
        request_revision = selection.revision

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._document_id = "scene-a"
                self._pending_scene_pick = {
                    "request_id": 1,
                    "x": 10.0,
                    "y": 12.0,
                    "width": 100.0,
                    "height": 80.0,
                    "cpu_id": 7,
                    "cpu_candidates": [7],
                    "selection_revision": request_revision,
                    "document_id": "scene-a",
                }
                self._pick_cycle_candidates = [7]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (-1.0, -1.0)
                self._pick_cycle_last_viewport = (0, 0)
                self.picked = []
                self._on_object_picked = lambda object_id, ctrl: self.picked.append((object_id, ctrl))

            @property
            def document_id(self):
                return self._document_id

        monkeypatch.setattr(picking, "_owns_particle_system", lambda object_id: object_id == 99)
        monkeypatch.setattr(picking, "_is_icon_only_pick_target", lambda _object_id: False)
        try:
            selection.select_scene_object(42, owner_id="hierarchy", record_history=False)
            selection.select_scene_object(7, owner_id="scene_view", record_history=False)
            probe = PickingProbe()
            probe._poll_scene_object_pick()
            assert probe.picked == []
        finally:
            selection.apply_snapshot(previous, record_history=False)

    def test_same_spot_click_keeps_particle_in_depth_cycle(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking

        class Engine:
            @staticmethod
            def pick_scene_icon_object_ids(*_args):
                return []

            @staticmethod
            def pick_scene_object_ids(*_args):
                return [42, 7]

            @staticmethod
            def screen_to_world_ray(*_args):
                return (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)

        class Context:
            @staticmethod
            def mouse_local(_ctx=None):
                return 10.0, 12.0

        class Viewport:
            width = 100.0
            height = 80.0

            @staticmethod
            def mouse_local(_ctx):
                return 10.0, 12.0

        class PickingProbe(picking.SceneViewPickingMixin):
            def __init__(self):
                self._engine = Engine()
                self._pick_cycle_candidates = [42, 99, 7]
                self._pick_cycle_index = 0
                self._pick_cycle_last_mouse = (10.0, 12.0)
                self._pick_cycle_last_viewport = (100, 80)

        monkeypatch.setattr(picking, "_owns_particle_system", lambda object_id: object_id == 99)
        monkeypatch.setattr(picking, "_object_ray_depth", lambda object_id, *_args: {
            42: 1.0,
            99: 2.0,
            7: 3.0,
        }[object_id])

        probe = PickingProbe()
        assert probe._pick_scene_object(Context(), Viewport()) == 99
        assert probe._pick_cycle_candidates == [42, 99, 7]
        assert probe._pick_scene_object(Context(), Viewport()) == 7

    def test_new_spot_does_not_keep_a_selected_non_particle_background(self, monkeypatch):
        from infernux.engine.ui import _scene_view_picking as picking

        probe = picking.SceneViewPickingMixin()
        probe._pick_cycle_candidates = [211]
        probe._pick_cycle_index = 0
        probe._pick_cycle_last_mouse = (10.0, 10.0)
        probe._pick_cycle_last_viewport = (100, 80)

        monkeypatch.setattr(
            picking.SceneViewPickingMixin,
            "_current_scene_pick_id",
            staticmethod(lambda: 211),
        )
        monkeypatch.setattr(picking, "_owns_particle_system", lambda _object_id: False)

        assert probe._cycle_pick_candidates(
            [212, 211], 40.0, 40.0, 100.0, 80.0
        ) == 212


class TestEditorPanelVisibilityLifecycle:
    def test_panel_content_failure_is_isolated_and_deduplicated(self, monkeypatch):
        from infernux.debug import Debug
        from infernux.engine.ui.editor_panel import EditorPanel

        class Context:
            @staticmethod
            def end_window():
                pass

        class ProbePanel(EditorPanel):
            def __init__(self):
                super().__init__("Probe", "probe")
                self.should_fail = True

            def _begin_closable_window(self, _ctx, _flags=0):
                return True

            def on_render_content(self, _ctx):
                if self.should_fail:
                    raise AttributeError("missing widget API")

        errors = []
        monkeypatch.setattr(Debug, "log_error", errors.append)
        panel = ProbePanel()

        panel.on_render(Context())
        panel.on_render(Context())
        assert len(errors) == 1
        assert "missing widget API" in errors[0]

        panel.should_fail = False
        panel.on_render(Context())
        panel.should_fail = True
        panel.on_render(Context())
        assert len(errors) == 2

    def test_dock_presentation_survives_begin_window_false(self):
        from infernux.engine.ui.editor_panel import EditorPanel

        class Context:
            @staticmethod
            def begin_window_closable(_title, _open, _flags):
                # ImGui may skip content submission on a throttled editor
                # frame even though this remains the selected dock tab.
                return False, True

            @staticmethod
            def is_current_window_content_presented():
                return True

            @staticmethod
            def is_mouse_button_clicked(_button):
                return False

            @staticmethod
            def is_window_focused(_flags):
                return False

            @staticmethod
            def end_window():
                pass

        panel = EditorPanel("Probe", "probe")
        panel.on_render(Context())

        assert panel.is_content_visible() is True
        assert panel._content_was_visible is True

    def test_hidden_hook_runs_only_on_visibility_transitions(self):
        from infernux.engine.ui.editor_panel import EditorPanel

        class Context:
            @staticmethod
            def end_window():
                pass

        class ProbePanel(EditorPanel):
            def __init__(self):
                super().__init__("Probe", "probe")
                self.visibility = iter([False, False, True, False, False])
                self.hidden_calls = 0
                self.visible_calls = 0
                self.content_calls = 0

            def _begin_closable_window(self, _ctx, _flags=0):
                return next(self.visibility)

            def _on_not_visible(self, _ctx):
                self.hidden_calls += 1

            def _on_visible_pre(self, _ctx):
                self.visible_calls += 1

            def on_render_content(self, _ctx):
                self.content_calls += 1

        panel = ProbePanel()
        ctx = Context()
        for _ in range(5):
            panel.on_render(ctx)

        assert panel.hidden_calls == 2
        assert panel.visible_calls == 1
        assert panel.content_calls == 1


def test_ui_editor_nudge_executes_before_recording_and_replays_through_history():
    from types import SimpleNamespace

    from infernux.engine.ui.ui_editor_panel import UIEditorPanel
    from infernux.engine.undo import UndoManager
    from infernux.lib import Vector3

    class ProbePanel(UIEditorPanel):
        def __init__(self, element):
            super().__init__()
            self.element = element

        @property
        def _selected_element_comp(self):
            return self.element

    previous_manager = UndoManager.instance()
    manager = UndoManager()
    transform = SimpleNamespace(local_position=Vector3(12.0, -24.0, 3.0))
    game_object = SimpleNamespace(transform=transform)
    panel = ProbePanel(SimpleNamespace(_try_get_game_object=lambda: game_object))
    try:
        assert panel.command_nudge_selected(-1, 10)
        assert tuple(transform.local_position) == pytest.approx((11.0, -34.0, 3.0))
        assert manager.undo_description == "Nudge UI Element"

        manager.undo()
        assert tuple(transform.local_position) == pytest.approx((12.0, -24.0, 3.0))
        manager.redo()
        assert tuple(transform.local_position) == pytest.approx((11.0, -34.0, 3.0))
    finally:
        UndoManager._instance = previous_manager


def test_ui_reparent_preserves_canvas_rect_after_parent_change_and_undo_redo(
    scene, monkeypatch,
):
    from infernux.engine.undo import ReparentCommand
    from infernux.engine.undo import _structural_commands
    from infernux.ui import UICanvas, UIFrame, UIImage

    canvas_a_object = scene.create_game_object("Canvas A")
    canvas_a = UICanvas()
    canvas_a.reference_width = 1000
    canvas_a.reference_height = 500
    canvas_a_object.add_py_component(canvas_a)

    canvas_b_object = scene.create_game_object("Canvas B")
    canvas_b = UICanvas()
    canvas_b.reference_width = 2400
    canvas_b.reference_height = 1350
    canvas_b_object.add_py_component(canvas_b)

    parent_a_object = scene.create_game_object("Parent A")
    parent_a_object.set_parent(canvas_a_object, False)
    parent_a = UIFrame()
    parent_a_object.add_py_component(parent_a)
    parent_a.set_rect(120.0, 70.0, 420.0, 230.0, 1000.0, 500.0)

    parent_b_object = scene.create_game_object("Parent B")
    parent_b_object.set_parent(canvas_b_object, False)
    parent_b = UIFrame()
    parent_b_object.add_py_component(parent_b)
    parent_b.set_rect(760.0, 410.0, 680.0, 360.0, 2400.0, 1350.0)

    child_object = scene.create_game_object("Image")
    child_object.set_parent(parent_a_object, False)
    child = UIImage()
    child_object.add_py_component(child)
    child.set_rect(205.0, 126.0, 96.0, 54.0, 1000.0, 500.0)
    expected_rect = child.get_rect(1000.0, 500.0)

    monkeypatch.setattr(
        _structural_commands, "_find_runtime_object", scene.find_by_id,
    )
    monkeypatch.setattr(
        _structural_commands, "_get_scene_by_world_id", lambda _world_id: scene,
    )
    command = ReparentCommand(
        child_object.id, parent_a_object.id, parent_b_object.id,
    )

    command.execute()
    assert child_object.get_parent() is parent_b_object
    assert child.get_rect(2400.0, 1350.0) == pytest.approx(expected_rect)

    command.undo()
    assert child_object.get_parent() is parent_a_object
    assert child.get_rect(1000.0, 500.0) == pytest.approx(expected_rect)

    command.redo()
    assert child_object.get_parent() is parent_b_object
    assert child.get_rect(2400.0, 1350.0) == pytest.approx(expected_rect)


def test_ui_editor_rotation_geometry_reads_transform_authority(scene):
    from infernux.engine.ui._ui_editor_alignment import UIEditorAlignmentMixin
    from infernux.engine.ui._ui_editor_geometry import UIEditorGeometryMixin
    from infernux.lib import Vector3
    from infernux.ui import UIImage

    owner = scene.create_game_object("Rotated UI")
    image = UIImage()
    owner.add_py_component(image)
    owner.transform.local_euler_angles = Vector3(0.0, 0.0, 37.0)

    geometry = UIEditorGeometryMixin()
    geometry._zoom = 1.0
    geometry._pan_x = 0.0
    geometry._pan_y = 0.0
    oriented = geometry._get_oriented_box_screen(
        image, 1920.0, 1080.0, 0.0, 0.0,
    )
    assert len(oriented["corners"]) == 4

    alignment = UIEditorAlignmentMixin()
    alignment._active_alignment_guides = [("stale",)]
    assert alignment._apply_resize_alignment_snapping(
        None, image, 100.0, 50.0, 1, 1, 0, 1920.0, 1080.0,
    ) == (100.0, 50.0)
    assert alignment._active_alignment_guides == []


def test_ui_editor_selection_identity_uses_game_object_id_not_wrapper_identity():
    from types import SimpleNamespace

    from infernux.engine.ui.ui_editor_panel import UIEditorPanel

    first_wrapper = SimpleNamespace(game_object=SimpleNamespace(id=42))
    refreshed_wrapper = SimpleNamespace(game_object=SimpleNamespace(id=42))

    assert first_wrapper is not refreshed_wrapper
    assert UIEditorPanel._element_object_id(first_wrapper) == UIEditorPanel._element_object_id(
        refreshed_wrapper
    )


def test_runtime_image_packet_refreshes_when_async_texture_becomes_ready():
    from infernux.core.asset_ref import TextureRef
    from infernux.ui import UIImage
    from infernux.ui.ui_render_dispatch import _runtime_render_image

    class Renderer:
        def __init__(self):
            self.rects = []
            self.images = []

        def add_filled_rect(self, *arguments):
            self.rects.append(arguments)

        def add_image(self, *arguments):
            self.images.append(arguments)

    renderer = Renderer()
    element = UIImage()
    texture = TextureRef(
        guid="texture-guid", path_hint="Assets/Textures/hud.png",
    )
    element.texture = texture
    texture_id = [0]
    requested_sources = []

    def get_texture_id(source):
        requested_sources.append(source)
        return texture_id[0]

    def render():
        _runtime_render_image(
            element,
            renderer,
            0,
            10.0,
            20.0,
            100.0,
            50.0,
            1.0,
            1.0,
            get_texture_id,
        )

    render()
    assert len(renderer.rects) == 1
    assert renderer.images == []

    texture_id[0] = 73
    render()
    assert len(renderer.rects) == 1
    assert len(renderer.images) == 1
    assert renderer.images[0][1] == 73
    assert requested_sources == [texture, texture]


def test_image_and_button_texture_sources_use_only_their_current_contracts():
    from infernux.core.asset_ref import TextureRef
    from infernux.engine.ui.inspector_ui_components import _has_native_size_texture
    from infernux.ui import UIButton, UIImage
    from infernux.ui.ui_render_dispatch import image_texture_source

    material_state = {"texture": None, "texture_guid": ""}
    image = UIImage()
    image_texture = TextureRef(
        guid="image-texture-guid", path_hint="Assets/Textures/image.png",
    )
    image.texture = image_texture
    button = UIButton()
    button_texture = TextureRef(
        guid="button-texture-guid", path_hint="Assets/Textures/button.png",
    )
    button.background_texture = button_texture

    assert image_texture_source(image, material_state) is image_texture
    assert image_texture_source(button, material_state) is button_texture
    assert _has_native_size_texture(image)
    assert _has_native_size_texture(button)

    material_texture = object()
    material_state["texture"] = material_texture
    button.background_texture = None
    assert image_texture_source(button, material_state) is material_texture


def test_button_obsolete_texture_path_is_ignored_in_favor_of_current_slot():
    from infernux.components.fields import get_raw_field_value
    from infernux.core.asset_ref import TextureRef
    from infernux.ui import UIButton

    button = UIButton()
    current = TextureRef(
        guid="button-current-guid", path_hint="Assets/Textures/current.png",
    )
    button.background_texture = current
    button._deserialize_fields_document({
        "texture_path": "Assets/Textures/legacy.png",
        "background_texture": None,
    })
    assert get_raw_field_value(button, "background_texture") is None


def test_runtime_text_packet_refreshes_when_transform_rotation_changes(scene):
    from infernux.lib import Vector3
    from infernux.ui import UIText
    from infernux.ui.ui_render_dispatch import _runtime_render_text

    class Renderer:
        def __init__(self):
            self.text_calls = []

        def add_text(self, *arguments):
            self.text_calls.append(arguments)

    element = UIText()
    game_object = scene.create_game_object("Rotating text")
    game_object.add_py_component(element)
    renderer = Renderer()

    def render():
        _runtime_render_text(
            element,
            renderer,
            0,
            sx=10.0,
            sy=20.0,
            sw=100.0,
            sh=50.0,
            ref_w=1920.0,
            ref_h=1080.0,
            scale_x=1.0,
            scale_y=1.0,
            text_scale=1.0,
            get_tex_id=lambda _path: 0,
        )

    render()
    assert renderer.text_calls[-1][14] == 0.0

    game_object.transform.local_euler_angles = Vector3(0.0, 0.0, 90.0)
    render()

    assert len(renderer.text_calls) == 2
    assert renderer.text_calls[-1][14] == 90.0

    game_object.transform.local_euler_angles = Vector3(15.0, 25.0, 45.0)
    _runtime_render_text(
        element,
        renderer,
        2,
        sx=0.0,
        sy=0.0,
        sw=100.0,
        sh=50.0,
        ref_w=100.0,
        ref_h=50.0,
        scale_x=1.0,
        scale_y=1.0,
        text_scale=1.0,
        get_tex_id=lambda _path: 0,
        world_transform_owned=True,
    )
    assert renderer.text_calls[-1][14] == 0.0


def test_ui_editor_continuous_manipulation_rejects_non_transform_targets():
    from types import SimpleNamespace

    from infernux.engine.interaction import (
        ContinuousEditService,
        EditorContextSnapshot,
        EditorInteractionCore,
        FocusService,
    )
    from infernux.engine.ui.ui_editor_panel import UIEditorPanel
    from infernux.engine.undo import UndoManager

    class ProbePanel(UIEditorPanel):
        def __init__(self, element):
            super().__init__()
            self.element = element

        @property
        def _selected_element_comp(self):
            return self.element

    previous_manager = UndoManager.instance()
    previous_core = EditorInteractionCore._instance
    previous_edits = ContinuousEditService._instance
    previous_focus = FocusService._instance
    manager = UndoManager()
    edits = ContinuousEditService()
    focus = FocusService()
    core = SimpleNamespace(
        continuous_edits=edits,
        focus=focus,
        capture_context=lambda: EditorContextSnapshot(focus=focus.snapshot),
    )
    EditorInteractionCore._instance = core
    element = SimpleNamespace()
    panel = ProbePanel(element)
    try:
        with pytest.raises(RuntimeError, match="current screen component"):
            panel._begin_element_manipulation("drag", element)
        assert edits.active_count == 0
        assert focus.snapshot.capture_owner_id == ""
    finally:
        UndoManager._instance = previous_manager
        EditorInteractionCore._instance = previous_core
        ContinuousEditService._instance = previous_edits
        FocusService._instance = previous_focus


def test_ui_editor_transform_backed_gesture_records_native_transform():
    from types import SimpleNamespace

    from infernux.components.value_codec import VALUE_CODECS
    from infernux.engine.interaction import (
        ContinuousEditService,
        EditorContextSnapshot,
        EditorInteractionCore,
        FocusService,
    )
    from infernux.engine.ui.ui_editor_panel import UIEditorPanel
    from infernux.engine.undo import UndoManager
    from infernux.lib import Vector3

    class ProbePanel(UIEditorPanel):
        def __init__(self, element):
            super().__init__()
            self.element = element

        @property
        def _selected_element_comp(self):
            return self.element

    previous_manager = UndoManager.instance()
    previous_core = EditorInteractionCore._instance
    previous_edits = ContinuousEditService._instance
    previous_focus = FocusService._instance
    manager = UndoManager()
    edits = ContinuousEditService()
    focus = FocusService()
    core = SimpleNamespace(
        continuous_edits=edits,
        focus=focus,
        capture_context=lambda: EditorContextSnapshot(focus=focus.snapshot),
    )
    EditorInteractionCore._instance = core
    transform = SimpleNamespace(
        local_position=Vector3(1.0, 2.0, 3.0),
        local_euler_angles=Vector3(0.0, 0.0, 0.0),
    )
    game_object = SimpleNamespace(id=72, transform=transform)
    element = SimpleNamespace(
        _try_get_game_object=lambda: game_object,
        width=160.0,
        height=40.0,
    )
    panel = ProbePanel(element)
    try:
        assert panel._begin_element_manipulation("drag", element)
        assert panel._mutate_element_manipulation(
            lambda: setattr(transform, "local_position", Vector3(4.0, 5.0, 6.0))
        )
        assert panel._finish_element_manipulation(commit=True)
        assert manager.undo_description == "Move UI Element"

        manager.undo()
        assert VALUE_CODECS.encode(transform.local_position) == pytest.approx(
            [1.0, 2.0, 3.0]
        )
        manager.redo()
        assert VALUE_CODECS.encode(transform.local_position) == pytest.approx(
            [4.0, 5.0, 6.0]
        )
    finally:
        UndoManager._instance = previous_manager
        EditorInteractionCore._instance = previous_core
        ContinuousEditService._instance = previous_edits
        FocusService._instance = previous_focus
