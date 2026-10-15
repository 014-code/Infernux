from __future__ import annotations
import json
import os
import struct
from dataclasses import replace
from types import SimpleNamespace
import pytest
from infernux.engine.i18n import t
from infernux.engine.interaction import DocumentRegistry
from infernux.engine.ui.graph_document_authoring import (
    GraphDocumentAuthoringModel,
    ParticleEmitterGraphAuthoringModel,
    particle_stage_definition_filter,
)
from infernux.graph import GraphDocument, GraphNodeRecord
from infernux.graph.types import CoordinateSpace, TypeRef, ValueType
from infernux.particle.asset import (
    EmitterSettings,
    ParticleEmitterAsset,
    ParticleEventField,
    ParticleEventFlow,
    ParticleEventType,
    ParticleGraphAsset,
    ParticleParameter,
    particle_attribute_cache_id,
)
from infernux.particle.nodes import (
    PARTICLE_EVENT_ACTIVE_TYPE_ID,
    PARTICLE_EVENT_TRIGGER_TYPE_ID,
    particle_event_payload_port_id,
    particle_graph_node_definitions,
)


@pytest.fixture(autouse=True)
def _isolate_particle_graph_panel_dirty_tracking(monkeypatch):
    from infernux.core.assets import AssetManager
    from infernux.engine.undo import UndoManager

    class _AssetDatabase:
        def __init__(self):
            self._paths: dict[str, str] = {}
            self._guids: dict[str, str] = {}

        def register(self, path: str, guid: str = "") -> str:
            normalized = os.path.abspath(str(path))
            identity = guid or self._guids.get(os.path.normcase(normalized)) or f"{len(self._paths) + 1:032x}"
            self._paths[identity] = normalized
            self._guids[os.path.normcase(normalized)] = identity
            return identity

        def get_guid_from_path(self, path: str) -> str:
            normalized = os.path.abspath(str(path))
            key = os.path.normcase(normalized)
            if key not in self._guids and os.path.isfile(normalized):
                self.register(normalized)
            return self._guids.get(key, "")

        def get_path_from_guid(self, guid: str) -> str:
            return self._paths.get(str(guid), "")

        def contains_path(self, path: str) -> bool:
            return bool(self.get_guid_from_path(path))

    database = _AssetDatabase()
    monkeypatch.setattr(AssetManager, "_asset_database", database)
    monkeypatch.setattr(
        AssetManager,
        "reimport_asset",
        classmethod(lambda _cls, path, **_kwargs: bool(database.register(path))),
    )
    monkeypatch.setattr(
        AssetManager,
        "import_asset",
        classmethod(lambda _cls, path, **_kwargs: bool(database.register(path))),
    )

    previous_manager = UndoManager.instance()
    UndoManager()
    DocumentRegistry.instance().close_view("particle_graph_editor")
    try:
        yield database
    finally:
        DocumentRegistry.instance().close_view("particle_graph_editor")
        UndoManager._instance = previous_manager


def _register_asset_reference(path, guid: str) -> None:
    from infernux.core.assets import AssetManager

    AssetManager.require_asset_database().register(str(path), guid)


def _stage_model(document):
    return GraphDocumentAuthoringModel(
        document,
        definition_filter=particle_stage_definition_filter(document.domain),
    )


def _finish_particle_graph_save(panel, path) -> None:
    for pending in tuple(panel._authoring_document_controller._pending_writes.values()):
        pending.io_ticket.wait()
    panel._authoring_document_controller.poll_pending_writes()


def _save_particle_graph(panel, path) -> bool:
    registry = DocumentRegistry.instance()
    document = registry.require(panel.document_id)
    target = os.path.abspath(str(path))
    current = os.path.abspath(document.resource_path) if document.resource_path else ""
    ticket = registry.begin_save(
        document.document_id,
        save_as=not current or os.path.normcase(target) != os.path.normcase(current),
    )
    accepted = panel._save_to(str(path), ticket_id=ticket.ticket_id)
    if accepted:
        _finish_particle_graph_save(panel, path)
    return accepted


def _particle_panel_with_command_core():
    from infernux.engine.interaction import EditorInteractionCore
    from infernux.engine.ui.particle_graph_editor_panel import (
        ParticleGraphEditorPanel,
    )
    from infernux.engine.undo import UndoManager

    core = EditorInteractionCore()
    manager = UndoManager(core.action_journal)
    panel = ParticleGraphEditorPanel()
    core.panels.register_type(panel.panel_type_id, panel.PANEL_INTERACTION)
    core.panels.bind_view(panel.window_id, panel.panel_type_id, panel)
    panel._graph_selection.bind(core.selection)
    core.focus.activate_panel(
        panel.panel_type_id,
        view_id=panel.window_id,
        document_id=panel.document_id,
        child_context_id=panel.current_child_context_id(),
        record_history=False,
    )
    return panel, core, manager


def _particle_panel_with_history():
    from infernux.engine.interaction import (
        DocumentRegistry,
        EditorContextSnapshot,
        SelectionService,
    )
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel
    from infernux.engine.undo import UndoManager

    DocumentRegistry()
    selection = SelectionService()
    manager = UndoManager()
    manager.set_context_hooks(
        lambda: EditorContextSnapshot(selection=selection.snapshot),
        lambda context, phase: (
            selection.apply_snapshot(
                context.selection,
                reason=phase,
                record_history=False,
            ),
            True,
        )[1],
    )
    panel = ParticleGraphEditorPanel()
    panel._graph_selection.bind(selection)
    return panel, manager
