"""Runtime capabilities and callbacks owned by the shared frame scheduler."""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Literal

_lock = RLock()
_services: dict[str, Any] = {}
UpdatePhase = Literal["update", "late_update", "fixed_update", "editor_update"]
_update_callbacks: dict[str, tuple[UpdateRegistration, ...]] = {
    phase: () for phase in ("update", "late_update", "fixed_update", "editor_update")
}


class UpdateRegistration:
    """One frame callback; disable it when idle and close it when its owner unloads."""

    __slots__ = ("_callback", "_phase", "_unscaled", "_enabled", "_closed")

    def __init__(self, callback: Callable[[float], None], phase: UpdatePhase, unscaled: bool) -> None:
        self._callback = callback
        self._phase = phase
        self._unscaled = unscaled
        self._enabled = False
        self._closed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._closed and enabled:
            raise RuntimeError("Update registration is closed")
        if enabled == self._enabled:
            return
        self._enabled = enabled
        global _update_callbacks
        previous = _update_callbacks[self._phase]
        current = previous + (self,) if enabled else tuple(item for item in previous if item is not self)
        _update_callbacks = {**_update_callbacks, self._phase: current}
        if bool(previous) != bool(current):
            from infernux.components._component_lifecycle import RuntimeExecutionScheduler

            RuntimeExecutionScheduler.set_runtime_service_phase_active(
                "public-updates:" + self._phase,
                "update" if self._phase == "editor_update" else self._phase,
                bool(current),
            )

    def close(self) -> None:
        self.enabled = False
        self._closed = True
        self._callback = None


def register_update(
    callback: Callable[[float], None], *, phase: UpdatePhase = "update",
    unscaled: bool = False, enabled: bool = True,
) -> UpdateRegistration:
    """Register on the engine thread, after component callbacks of the chosen phase.

    New registrations join the next frame snapshot. Disabling/closing suppresses
    pending calls immediately. Normal/Late/Fixed callbacks only run in Play;
    Editor callbacks only run in Edit mode and receive the editor frame duration.
    Independent Fixed callbacks still follow physics steps and pause at timeScale=0.
    Preload code should use ``context.add_update()`` to own cleanup automatically.
    """
    if phase not in _update_callbacks:
        raise ValueError(f"Unknown update phase: {phase}")
    if not callable(callback):
        raise TypeError("Update callback must be callable")
    registration = UpdateRegistration(callback, phase, bool(unscaled))
    registration.enabled = enabled
    return registration


def _update_snapshot() -> dict[str, tuple[UpdateRegistration, ...]]:
    return _update_callbacks


def _dispatch_updates(callbacks: tuple[UpdateRegistration, ...], delta: float, unscaled_delta: float) -> None:
    for registration in callbacks:
        if not registration._enabled:
            continue
        try:
            registration._callback(unscaled_delta if registration._unscaled else delta)
        except Exception as exc:
            from infernux.debug import Debug

            Debug.log_exception(exc)


def install_runtime_service(name: str, service: Any) -> None:
    identity = str(name).strip().casefold()
    if not identity or service is None:
        raise ValueError("runtime service name and implementation are required")
    with _lock:
        existing = _services.get(identity)
        if existing is not None and existing is not service:
            raise RuntimeError(f"runtime service is already installed: {identity}")
        _services[identity] = service


def get_runtime_service(name: str) -> Any | None:
    identity = str(name).strip().casefold()
    if not identity:
        return None
    with _lock:
        return _services.get(identity)


def remove_runtime_service(name: str, service: Any | None = None) -> bool:
    identity = str(name).strip().casefold()
    if not identity:
        return False
    with _lock:
        existing = _services.get(identity)
        if existing is None or (service is not None and existing is not service):
            return False
        del _services[identity]
        return True


__all__ = [
    "UpdatePhase",
    "UpdateRegistration",
    "register_update",
    "get_runtime_service",
    "install_runtime_service",
    "remove_runtime_service",
]
