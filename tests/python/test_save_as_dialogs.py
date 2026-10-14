from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import pytest
from infernux.core.animation_timeline import TimelineKeyframe
from infernux.engine.interaction import ModalService
from infernux.engine.ui.animclip2d_editor_panel import AnimClip2DEditorPanel
from infernux.engine.ui.animfsm_editor_panel import AnimFSMEditorPanel
from infernux.engine.ui.animtimeline_editor_panel import AnimTimelineEditorPanel
from infernux.engine.ui.asset_save_dialog import AssetSaveAsDialog
from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel
from infernux.engine.ui import asset_save_dialog
from infernux.engine.ui.animclip2d_editor_panel import AnimClip2DEditorPanel, _ClipState
import os
from infernux.engine.scene_manager import SceneFileManager
from infernux.engine.interaction import (
    DocumentCapability,
    DocumentKind,
    DocumentRegistry,
    EditorInteractionCore,
)
from infernux.engine.ui.dirty_panel_confirmation import DirtyPanelConfirmationCoordinator
import infernux.engine._scene_save as scene_save


class _TestResourceDocumentController:
    def __init__(self, discard=None) -> None:
        self._discard = discard

    def discard(self, *, document_id):
        del document_id
        if not callable(self._discard):
            return False
        return self._discard()


def _open_test_dirty_document(panel_id: str, title: str, *, discard=None):
    registry = DocumentRegistry.instance()
    registry.close_view(panel_id)
    document = registry.create(
        DocumentKind.GENERIC,
        title,
        revision=1,
        saved_revision=0,
        capabilities=(
            DocumentCapability.DISCARD
            if callable(discard)
            else DocumentCapability.NONE
        ),
        controller=_TestResourceDocumentController(discard),
    )
    registry.attach_view(document.document_id, panel_id)
    return document


def _mark_test_document_saved(panel_id: str) -> None:
    registry = DocumentRegistry.instance()
    document = registry.document_for_view(panel_id)
    assert document is not None
    registry.mark_saved(document.document_id)


def _scene_manager() -> SceneFileManager:
    previous = SceneFileManager._instance
    previous_core = EditorInteractionCore._instance
    previous_confirmation = DirtyPanelConfirmationCoordinator._instance
    core = EditorInteractionCore()
    DirtyPanelConfirmationCoordinator._instance = None
    manager = SceneFileManager()
    manager._test_previous_instance = previous
    manager._test_interaction_core = core
    manager._test_previous_interaction_core = previous_core
    manager._test_previous_confirmation = previous_confirmation
    return manager


def _restore_scene_manager(manager: SceneFileManager) -> None:
    DirtyPanelConfirmationCoordinator._instance = manager._test_previous_confirmation
    manager._test_interaction_core.shutdown()
    EditorInteractionCore._instance = manager._test_previous_interaction_core
    SceneFileManager._instance = manager._test_previous_instance


def _set_scene_dirty(manager: SceneFileManager, dirty: bool) -> None:
    registry = DocumentRegistry.instance()
    document = registry.require(manager.document_id)
    if dirty and not document.is_dirty:
        registry.mark_changed(document.document_id)
    elif not dirty and document.is_dirty:
        registry.restore_saved_revision(document.document_id)


def test_unsaved_scene_agent_save_uses_editor_owned_save_as_state(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_save, "_effective_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(scene_save, "is_synthetic_input_frame", lambda: True)
    manager = _scene_manager()
    try:
        manager._current_scene_path = None
        manager._show_save_as_dialog()

        assert manager._save_as_popup_open is True
        assert manager._save_as_popup_requested is True
        assert manager._save_as_focus_name is True
        assert manager._save_as_folder == "Assets"
        assert manager._save_as_name == "UntitledScene"
        assert manager._test_interaction_core.modals.active_modal_id == "scene.save_as"
    finally:
        _restore_scene_manager(manager)


def test_unsaved_scene_user_save_uses_native_dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_save, "_effective_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(scene_save, "is_synthetic_input_frame", lambda: False)
    target = tmp_path / "Assets" / "Scenes" / "RacingEntry.scene"
    saved: list[str] = []
    monkeypatch.setattr(scene_save, "save_file_dialog", lambda **_kwargs: str(target))
    manager = _scene_manager()
    try:
        manager._current_scene_path = None
        manager._do_save = lambda path, **_kwargs: saved.append(path) or True
        manager._show_save_as_dialog()

        assert manager._save_as_popup_open is False
        assert manager._save_as_native_dialog_pending is True

        manager._test_interaction_core.modals.render(None)

        assert saved == [str(target)]
        assert manager._test_interaction_core.modals.active_modal_id == ""
    finally:
        _restore_scene_manager(manager)


def test_save_as_path_is_constrained_to_assets_and_valid_names(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_save, "_effective_project_root", lambda: str(tmp_path))
    manager = _scene_manager()
    try:
        manager._save_as_folder = "Assets/Scenes"
        manager._save_as_name = "RacingEntry"
        path, error = manager._resolve_save_as_path()

        assert error == ""
        assert Path(path) == tmp_path / "Assets" / "Scenes" / "RacingEntry.scene"

        manager._save_as_folder = "../outside"
        path, error = manager._resolve_save_as_path()
        assert path == ""
        assert "Assets" in error

        manager._save_as_folder = "Assets"
        manager._save_as_name = "../invalid"
        path, error = manager._resolve_save_as_path()
        assert path == ""
        assert "invalid" in error.lower()
    finally:
        _restore_scene_manager(manager)


def test_is_under_assets_resolves_native_path_aliases(tmp_path, monkeypatch):
    project_root = tmp_path / "InfernuxRacingPilot"
    assets_root = project_root / "Assets"
    scene_path = assets_root / "RaceTrack.scene"
    scene_path.parent.mkdir(parents=True)
    scene_path.write_text("{}", encoding="utf-8")
    native_alias_root = tmp_path / "INFERN~1"
    native_alias_scene = native_alias_root / "Assets" / "RaceTrack.scene"
    original_realpath = os.path.realpath

    def resolve_native_alias(path: str) -> str:
        normalized = os.path.normcase(os.path.abspath(path))
        alias = os.path.normcase(os.path.abspath(native_alias_root))
        if normalized == alias:
            return str(project_root)
        if normalized.startswith(alias + os.sep):
            return str(project_root / os.path.relpath(normalized, alias))
        return original_realpath(path)

    monkeypatch.setattr(scene_save, "_effective_project_root", lambda: str(project_root))
    monkeypatch.setattr(scene_save.os.path, "realpath", resolve_native_alias)
    manager = _scene_manager()
    try:
        assert manager._is_under_assets(str(native_alias_scene)) is True
        assert manager._is_under_assets(str(native_alias_root / "Outside.scene")) is False
    finally:
        _restore_scene_manager(manager)


def test_dirty_scene_close_uses_editor_owned_confirmation(tmp_path, monkeypatch):
    manager = _scene_manager()

    class _Native:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def confirm_close(self) -> None:
            self.calls.append("confirm")

        def cancel_close(self) -> None:
            self.calls.append("cancel")

    native = _Native()
    camera_paths: list[str] = []
    coordinator = DirtyPanelConfirmationCoordinator.instance()
    try:
        if coordinator.is_active:
            coordinator.choose_cancel()
        manager._engine = native
        _set_scene_dirty(manager, True)
        manager._current_scene_path = str(tmp_path / "Assets" / "UnsavedChanges.scene")
        monkeypatch.setattr(manager, "_is_play_mode", lambda: False)
        monkeypatch.setattr(manager, "_save_camera_state", camera_paths.append)
        manager.request_close()

        assert manager._close_in_progress is True
        assert coordinator.active_document_id == manager.document_id
        assert camera_paths == []
        assert native.calls == []
        coordinator.choose_discard()
        assert camera_paths == [manager._current_scene_path]
        assert native.calls == ["confirm"]
    finally:
        if coordinator.is_active:
            coordinator.choose_cancel()
        _restore_scene_manager(manager)


def test_scene_save_as_nests_under_unsaved_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_save, "_effective_project_root", lambda: str(tmp_path))
    monkeypatch.setattr(scene_save, "is_synthetic_input_frame", lambda: True)
    manager = _scene_manager()
    parent_active = {"value": True}
    modals = manager._test_interaction_core.modals
    modals.register(
        "editor.unsaved_changes",
        is_active=lambda: parent_active["value"],
        render=lambda _ctx: None,
        cancel=lambda: parent_active.update(value=False),
    )
    try:
        assert modals.activate("editor.unsaved_changes", owner_id="scene")
        assert manager._show_save_as_dialog()
        assert [entry.modal_id for entry in modals.active_stack] == [
            "editor.unsaved_changes",
            "scene.save_as",
        ]
    finally:
        _restore_scene_manager(manager)


def test_dirty_panel_confirmation_precedes_dirty_scene_confirmation(tmp_path, monkeypatch):
    manager = _scene_manager()
    panel_id = "scene_close_dirty_panel_order"

    class _Native:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def confirm_close(self) -> None:
            self.calls.append("confirm")

        def cancel_close(self) -> None:
            self.calls.append("cancel")

    native = _Native()
    coordinator = DirtyPanelConfirmationCoordinator.instance()
    try:
        assert coordinator.is_active is False
        manager._engine = native
        _set_scene_dirty(manager, True)
        manager._current_scene_path = str(tmp_path / "Assets" / "Ordered.scene")
        monkeypatch.setattr(manager, "_is_play_mode", lambda: False)
        monkeypatch.setattr(manager, "_save_camera_state", lambda _path: None)

        def discard_panel() -> None:
            _mark_test_document_saved(panel_id)

        _open_test_dirty_document(
            panel_id,
            "Animation Editor",
            discard=discard_panel,
        )
        manager.request_close()

        assert coordinator.active_panel_id == panel_id
        assert native.calls == []
        coordinator.choose_discard()
        assert coordinator.is_active is True
        assert coordinator.active_document_id == manager.document_id
        assert native.calls == []
        coordinator.choose_discard()
        assert coordinator.is_active is False
        assert native.calls == ["confirm"]
    finally:
        if coordinator.is_active:
            coordinator.choose_cancel()
        DocumentRegistry.instance().close_view(panel_id)
        _restore_scene_manager(manager)


def test_dirty_scene_open_uses_replace_document_transaction(monkeypatch):
    manager = _scene_manager()
    coordinator = DirtyPanelConfirmationCoordinator.instance()
    try:
        if coordinator.is_active:
            coordinator.choose_cancel()
        _set_scene_dirty(manager, True)
        manager._current_scene_path = "current.scene"
        monkeypatch.setattr(manager, "_is_play_mode", lambda: False)
        monkeypatch.setattr(manager, "_save_camera_state", lambda _path: None)

        assert manager.open_scene("next.scene") is False
        assert coordinator.active_document_id == manager.document_id
        assert manager._deferred_load_path is None

        coordinator.choose_discard()

        assert coordinator.is_active is False
        assert manager._deferred_load_path == "next.scene"
    finally:
        if coordinator.is_active:
            coordinator.choose_cancel()
        _restore_scene_manager(manager)


def _enter_fake_prefab_mode(manager: SceneFileManager, prefab_path: str) -> tuple[str, str]:
    from infernux.engine.interaction import DocumentKey, DocumentKind

    previous_document_id = manager.document_id
    manager._previous_scene_document_id = previous_document_id
    manager._previous_scene_path = manager._current_scene_path
    manager.is_prefab_mode = True
    manager.prefab_mode_guid = "interaction-prefab-guid"
    manager.prefab_mode_path = prefab_path
    manager._current_scene_path = prefab_path
    manager._replace_scene_document(
        kind="prefab",
        resource_path=prefab_path,
        title="InteractionPrefab",
        dirty=True,
        preserve_previous=True,
        key_override=DocumentKey.asset(DocumentKind.PREFAB, manager.prefab_mode_guid),
    )
    return previous_document_id, manager.document_id


def test_dirty_prefab_exit_waits_for_explicit_close_decision(tmp_path, monkeypatch):
    manager = _scene_manager()
    coordinator = DirtyPanelConfirmationCoordinator.instance()
    scheduled: list[object] = []
    saved: list[str] = []
    try:
        if coordinator.is_active:
            coordinator.choose_cancel()
        prefab_path = str(tmp_path / "Assets" / "Interaction.prefab")
        _previous_id, prefab_id = _enter_fake_prefab_mode(manager, prefab_path)
        monkeypatch.setattr(
            manager,
            "_schedule_prefab_exit",
            lambda callback=None, **_kwargs: scheduled.append(callback) or True,
        )
        def save_prefab(*, ticket_id=""):
            saved.append(ticket_id)
            registry = DocumentRegistry.instance()
            registry.capture_save_revision(ticket_id)
            registry.complete_save(ticket_id, success=True)
            return True

        monkeypatch.setattr(manager, "_save_prefab", save_prefab)

        assert manager.exit_prefab_mode() is True
        assert coordinator.active_document_id == prefab_id
        assert scheduled == []
        assert saved == []

        coordinator.choose_cancel()
        assert coordinator.is_active is False
        assert scheduled == []
        assert saved == []

        assert manager.exit_prefab_mode() is True
        coordinator.choose_discard()
        assert coordinator.is_active is False
        assert len(scheduled) == 1
        assert saved == []

        scheduled.clear()
        assert manager.exit_prefab_mode() is True
        coordinator.choose_save()
        assert coordinator.is_active is False
        assert len(saved) == 1
        assert len(scheduled) == 1
    finally:
        if coordinator.is_active:
            coordinator.choose_cancel()
        _restore_scene_manager(manager)


def test_open_from_prefab_resolves_prefab_then_previous_scene(tmp_path, monkeypatch):
    manager = _scene_manager()
    coordinator = DirtyPanelConfirmationCoordinator.instance()
    scheduled: list[object] = []
    saved: list[str] = []
    try:
        if coordinator.is_active:
            coordinator.choose_cancel()
        manager._current_scene_path = str(tmp_path / "Assets" / "Current.scene")
        _set_scene_dirty(manager, True)
        previous_id, prefab_id = _enter_fake_prefab_mode(
            manager,
            str(tmp_path / "Assets" / "Interaction.prefab"),
        )
        monkeypatch.setattr(
            manager,
            "_schedule_prefab_exit",
            lambda callback=None, **_kwargs: scheduled.append(callback) or True,
        )
        monkeypatch.setattr(
            manager,
            "_save_prefab",
            lambda *, ticket_id="": saved.append(ticket_id) or True,
        )
        monkeypatch.setattr(manager, "_save_camera_state", lambda _path: None)

        assert manager.open_scene("next.scene") is True
        assert coordinator.active_document_id == prefab_id
        assert manager._deferred_load_path is None
        assert saved == []

        coordinator.choose_discard()
        assert coordinator.is_active is False
        assert len(scheduled) == 1
        assert manager._deferred_load_path is None

        # The deferred Prefab swap restores the previous Scene before running
        # the continuation. The previous Scene must then get its own decision.
        manager.is_prefab_mode = False
        manager._scene_document_id = previous_id
        manager._current_scene_path = manager._previous_scene_path
        callback = scheduled[0]
        assert callable(callback)
        callback()

        assert coordinator.active_document_id == previous_id
        assert manager._deferred_load_path is None
        coordinator.choose_cancel()
        assert manager._deferred_load_path is None
        assert saved == []
    finally:
        if coordinator.is_active:
            coordinator.choose_cancel()
        _restore_scene_manager(manager)


def test_prefab_exit_records_history_only_after_deferred_transition_completes(
    tmp_path,
    monkeypatch,
):
    from infernux.engine.interaction import EditorContextSnapshot, PrefabCommandService
    from infernux.engine.undo import PrefabModeCommand, UndoManager

    manager = _scene_manager()
    previous_undo = UndoManager._instance
    undo = UndoManager()
    previous_service = PrefabCommandService._instance
    service = PrefabCommandService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        context_provider=lambda: EditorContextSnapshot(),
    )
    requested: list[tuple[object, bool]] = []
    try:
        prefab_path = str(tmp_path / "Assets" / "Interaction.prefab")
        _enter_fake_prefab_mode(manager, prefab_path)
        monkeypatch.setattr(
            manager,
            "_request_prefab_exit",
            lambda on_complete=None, *, preserve_undo_history=False: (
                requested.append((on_complete, preserve_undo_history)) or True
            ),
        )

        assert service.exit() is True
        assert undo.action_journal.applied_entries() == ()
        assert len(requested) == 1
        callback, preserve_history = requested[0]
        assert preserve_history is True

        manager.is_prefab_mode = False
        callback()

        entries = undo.action_journal.applied_entries()
        assert len(entries) == 1
        assert isinstance(entries[0].action, PrefabModeCommand)
        assert entries[0].action.description == "Exit Prefab Mode"
    finally:
        service.shutdown()
        PrefabCommandService._instance = previous_service
        UndoManager._instance = previous_undo
        _restore_scene_manager(manager)


def test_deferred_prefab_exit_preserves_history_for_tracked_transition(monkeypatch):
    manager = _scene_manager()
    calls: list[bool] = []
    completed: list[bool] = []
    try:
        manager._deferred_exit_prefab = True
        manager._post_prefab_exit_callback = lambda: completed.append(True)
        monkeypatch.setattr(
            manager,
            "_do_exit_prefab_mode",
            lambda preserve_undo_history=False: (
                calls.append(bool(preserve_undo_history)) or True
            ),
        )

        assert manager._run_deferred_exit_prefab_task(
            preserve_undo_history=True,
        ) is True
        assert calls == [True]
        assert completed == [True]
        assert manager._deferred_exit_prefab is False
    finally:
        _restore_scene_manager(manager)


def test_dirty_panel_cancel_releases_native_close_request(monkeypatch):
    manager = _scene_manager()
    panel_id = "scene_close_dirty_panel_cancel"

    class _Native:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def confirm_close(self) -> None:
            self.calls.append("confirm")

        def cancel_close(self) -> None:
            self.calls.append("cancel")

    native = _Native()
    coordinator = DirtyPanelConfirmationCoordinator.instance()
    try:
        assert coordinator.is_active is False
        manager._engine = native
        _set_scene_dirty(manager, False)
        monkeypatch.setattr(manager, "_is_play_mode", lambda: False)
        _open_test_dirty_document(panel_id, "Animation Editor")
        manager.request_close()
        coordinator.choose_cancel()

        assert native.calls == ["cancel"]
        assert manager._close_in_progress is False
        assert coordinator.is_active is False
    finally:
        if coordinator.is_active:
            coordinator.choose_cancel()
        DocumentRegistry.instance().close_view(panel_id)
        _restore_scene_manager(manager)


def test_play_mode_still_confirms_dirty_resource_panels(monkeypatch):
    manager = _scene_manager()
    panel_id = "play_close_dirty_resource"

    class _Native:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def confirm_close(self) -> None:
            self.calls.append("confirm")

        def cancel_close(self) -> None:
            self.calls.append("cancel")

    native = _Native()
    coordinator = DirtyPanelConfirmationCoordinator.instance()
    try:
        assert coordinator.is_active is False
        manager._engine = native
        monkeypatch.setattr(manager, "_is_play_mode", lambda: True)
        _open_test_dirty_document(panel_id, "Timeline")
        manager.request_close()

        assert coordinator.active_panel_id == panel_id
        assert native.calls == []
        coordinator.choose_discard()
        assert native.calls == ["confirm"]
    finally:
        if coordinator.is_active:
            coordinator.choose_cancel()
        DocumentRegistry.instance().close_view(panel_id)
        _restore_scene_manager(manager)
