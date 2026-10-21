"""Owned, reversible editor previews; authored documents remain authoritative."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable


@dataclass(slots=True)
class _PreviewTarget:
    target: object
    alive: Callable[[], bool]
    material: bool = False
    restore: list[Callable[[], None]] = field(default_factory=list)


class PreviewScope:
    """Capture values once, mutate directly, then restore when the owner closes.

    Use ``capture_material`` before material setters and ``capture_text`` before
    character-buffer edits. Preview updates run only in Edit mode.
    """

    def __init__(self, service: PreviewService, owner_id: str) -> None:
        self._service = service
        self.owner_id = owner_id
        self._targets: dict[int, _PreviewTarget] = {}
        self._fields: set[tuple[int, str]] = set()
        self._updates = []
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _target(self, target) -> _PreviewTarget:
        if self._closed:
            raise RuntimeError("Preview scope is closed")
        key = id(target)
        if key in self._targets:
            return self._targets[key]
        from infernux.components import InxComponent
        from infernux.core.material import Material
        from infernux.lib import Component

        if isinstance(target, InxComponent):
            alive = lambda: not target._is_destroyed and target._try_get_game_object() is not None
        elif isinstance(target, Component):
            alive = lambda: bool(target)
        elif isinstance(target, Material):
            alive = lambda: not target._cannot_persist()
        else:
            raise TypeError("Preview targets must be components, Transforms or materials")
        if not alive():
            raise ValueError("Preview target is no longer live")
        claimed = self._service._claims.get(key)
        if claimed is not None and claimed is not self:
            raise RuntimeError("Another preview already owns this target")
        entry = self._targets[key] = _PreviewTarget(target, alive, isinstance(target, Material))
        self._service._claims[key] = self
        return entry

    def capture(self, target, *attributes: str) -> None:
        entry = self._target(target)
        if entry.material:
            self.capture_material(target)
            return
        for name in attributes:
            key = (id(target), name)
            if key in self._fields:
                continue
            value = copy.deepcopy(getattr(target, name))
            entry.restore.append(lambda t=target, n=name, v=value: setattr(t, n, v))
            self._fields.add(key)

    def set(self, target, attribute: str, value) -> None:
        self.capture(target, attribute)
        setattr(target, attribute, value)

    def capture_material(self, material) -> None:
        entry = self._target(material)
        key = (id(material), "__material__")
        if key in self._fields:
            return
        material.flush()
        if material._save_pending:
            raise RuntimeError("Save the authored material before starting its preview")
        document = material.serialize_document()
        material._preview_active = True
        material._end_preview = self.close

        def restore():
            try:
                if not material.apply_dict(document):
                    raise RuntimeError("Could not restore the previewed material")
            finally:
                material._preview_active = False
                material._end_preview = None

        entry.restore.append(restore)
        self._fields.add(key)

    def capture_text(self, text) -> None:
        entry = self._target(text)
        key = (id(text), "__text_geometry__")
        if key in self._fields:
            return
        state = text.__dict__
        previous = state.get("_text_geometry"), state.get("_text_geometry_key")
        state.pop("_text_geometry", None)
        state.pop("_text_geometry_key", None)

        def restore():
            from infernux.ui.ui_render_revision import mark_runtime_ui_dirty

            state["_text_geometry"], state["_text_geometry_key"] = previous
            mark_runtime_ui_dirty(text)

        entry.restore.append(restore)
        self._fields.add(key)

    def add_update(self, callback: Callable[[float], None]):
        from infernux.runtime_services import register_update

        if self._closed:
            raise RuntimeError("Preview scope is closed")

        def tick(delta):
            if all(entry.alive() for entry in self._targets.values()):
                callback(delta)
            else:
                self.close()

        registration = register_update(tick, phase="editor_update")
        self._updates.append(registration)
        return registration

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for registration in self._updates:
            registration.close()
        errors = []
        for key, entry in reversed(tuple(self._targets.items())):
            try:
                if entry.alive():
                    for restore in reversed(entry.restore):
                        restore()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._service._claims.pop(key)
        self._updates.clear()
        self._targets.clear()
        del self._service._scopes[self]
        if errors:
            raise ExceptionGroup("Editor preview restoration failed", errors)


class PreviewService:
    _instance: PreviewService | None = None

    def __init__(self) -> None:
        self._scopes: dict[PreviewScope, None] = {}
        self._claims: dict[int, PreviewScope] = {}
        PreviewService._instance = self

    def begin(self, owner_id: str) -> PreviewScope:
        from infernux.application import Application
        from infernux.lib import SceneManager

        if not (Application.is_editor() or Application.is_headless()) or SceneManager.instance().is_playing():
            raise RuntimeError("Editor previews require Edit mode")
        if not owner_id:
            raise ValueError("Preview requires an owner id")
        scope = PreviewScope(self, owner_id)
        self._scopes[scope] = None
        return scope

    def close(self, owner_id: str | None = None) -> None:
        errors = []
        for scope in reversed(tuple(self._scopes)):
            if owner_id is None or scope.owner_id == owner_id:
                try:
                    scope.close()
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise ExceptionGroup("Editor previews could not be restored", errors)


def close_editor_previews(owner_id: str | None = None) -> None:
    service = PreviewService._instance
    if service is not None:
        service.close(owner_id)
