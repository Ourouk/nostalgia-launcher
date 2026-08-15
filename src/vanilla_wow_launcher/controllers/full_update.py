"""Sequential full-update workflow.

The workflow owns only sequencing.  Individual controllers continue to own
their workers and panel actions; progress and results travel over the shared
dispatcher.
"""

import threading

from ..state.events import EventDispatcher, OperationFailed, OperationFinished


class FullUpdateController:
    """Run client, mods, and addons updates in that order."""

    def __init__(self, dispatcher: EventDispatcher, updater, mods, addons):
        self._dispatcher = dispatcher
        self._updater = updater
        self._mods = mods
        self._addons = addons
        self._lock = threading.Lock()
        self._phase = None
        self._cancelled = False
        dispatcher.subscribe(self._on_event)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._phase is not None

    def start(self) -> bool:
        with self._lock:
            if self._phase is not None:
                return False
            self._cancelled = False
            if not self._updater.client_update_enabled:
                self._phase = "mods"
            else:
                self._phase = "client"
                start = self._updater.start_update
        if self._phase == "mods":
            return self._start_mods()
        result = start()
        if result is False or (result is None and not getattr(
                self._updater, "running", True)):
            self._fail("client update could not start")
            return False
        return True

    def cancel(self):
        with self._lock:
            if self._phase is None:
                return
            self._cancelled = True
            self._phase = None
        self._updater.cancel()
        for controller in (self._mods, self._addons):
            cancel = getattr(controller, "cancel", None)
            if cancel is not None:
                cancel()

    def _on_event(self, event):
        if not isinstance(event, (OperationFinished, OperationFailed)):
            return
        with self._lock:
            phase = self._phase
            if phase is None or self._cancelled:
                return
        if isinstance(event, OperationFailed):
            kind, ok = event.kind, False
        else:
            kind, ok = event.kind, event.ok
        if phase == "client" and kind == "update":
            if ok:
                self._start_mods()
            else:
                self._fail(event.message)
        elif phase == "mods" and kind == "mods":
            if ok:
                self._start_addons_verify()
            else:
                self._fail(event.message)
        elif phase == "addons_verify" and kind == "addons_verify":
            if ok:
                records = self._addons.update_all()
                if records:
                    with self._lock:
                        if self._phase != "addons_verify":
                            return
                        self._phase = "addons"
                    if not self._addons.apply(records):
                        self._fail("addons update could not start")
                else:
                    self._finish()
            else:
                self._fail(event.message)
        elif phase == "addons" and kind == "addons":
            if ok:
                self._finish()
            else:
                self._fail(event.message)

    def _start_mods(self) -> bool:
        with self._lock:
            if self._phase is None or self._cancelled:
                return False
            self._phase = "mods"
        if not self._mods.apply():
            self._fail("mods update could not start")
            return False
        return True

    def _start_addons_verify(self):
        with self._lock:
            if self._phase != "mods" or self._cancelled:
                return
            self._phase = "addons_verify"
        if not self._addons.verify(force=True):
            self._fail("addons verification could not start")

    def _finish(self):
        with self._lock:
            if self._phase is None:
                return
            self._phase = None
        self._dispatcher.post(OperationFinished("full_update", True, ""))

    def _fail(self, message):
        with self._lock:
            if self._phase is None:
                return
            self._phase = None
        self._dispatcher.post(OperationFinished("full_update", False, message or ""))
