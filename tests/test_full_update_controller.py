from vanilla_wow_launcher.controllers.full_update import FullUpdateController
from vanilla_wow_launcher.state.events import EventDispatcher, OperationFinished


class FakeController:
    def __init__(self, dispatcher, name, result=True):
        self.dispatcher = dispatcher
        self.name = name
        self.result = result
        self.calls = []
        self.cancelled = False

    def apply(self, *args):
        self.calls.append(("apply", args))
        return self.result

    def verify(self, **kwargs):
        self.calls.append(("verify", kwargs))
        return self.result

    def update_all(self):
        self.calls.append(("update_all", ()))
        return []


class FakeUpdater(FakeController):
    def __init__(self, dispatcher, enabled=True, result=True):
        super().__init__(dispatcher, "client", result)
        self.client_update_enabled = enabled

    def start_update(self):
        self.calls.append(("start_update", ()))
        return self.result

    def cancel(self):
        self.cancelled = True


def _finish(dispatcher, kind, ok=True, message=""):
    dispatcher.post(OperationFinished(kind, ok, message))
    dispatcher.dispatch_all()


def _workflow(enabled=True):
    dispatcher = EventDispatcher()
    updater = FakeUpdater(dispatcher, enabled)
    mods = FakeController(dispatcher, "mods")
    addons = FakeController(dispatcher, "addons")
    workflow = FullUpdateController(dispatcher, updater, mods, addons)
    return workflow, updater, mods, addons, dispatcher


def test_stages_run_in_order_and_wait_for_addon_verify():
    workflow, updater, mods, addons, dispatcher = _workflow()
    assert workflow.start()
    assert [x[0] for x in updater.calls] == ["start_update"]
    assert not mods.calls

    _finish(dispatcher, "update")
    assert [x[0] for x in mods.calls] == ["apply"]
    _finish(dispatcher, "mods")
    assert addons.calls == [("verify", {"force": True})]
    _finish(dispatcher, "addons_verify")
    assert addons.calls[-1] == ("update_all", ())
    assert not workflow.running


def test_disabled_client_skips_directly_to_mods():
    workflow, updater, mods, _, dispatcher = _workflow(enabled=False)
    assert workflow.start()
    assert not updater.calls
    assert [x[0] for x in mods.calls] == ["apply"]


def test_addon_records_are_applied_after_verification():
    workflow, _, mods, addons, dispatcher = _workflow()
    addons.update_all = lambda: [{"folder": "A"}]
    workflow.start()
    _finish(dispatcher, "update")
    _finish(dispatcher, "mods")
    _finish(dispatcher, "addons_verify")
    assert addons.calls[-1] == ("apply", ([{"folder": "A"}],))
    assert workflow.running
    _finish(dispatcher, "addons")
    assert not workflow.running


def test_failure_stops_chain():
    workflow, updater, mods, addons, dispatcher = _workflow()
    workflow.start()
    _finish(dispatcher, "update", False, "broken")
    assert not workflow.running
    assert not mods.calls
    assert not addons.calls
    event = dispatcher.drain()
    assert event == [OperationFinished("full_update", False, "broken")]


def test_cancel_and_reentrant_start_are_safe():
    workflow, updater, mods, addons, dispatcher = _workflow()
    assert workflow.start()
    assert not workflow.start()
    workflow.cancel()
    workflow.cancel()
    assert updater.cancelled
    _finish(dispatcher, "update")
    assert not mods.calls
    assert workflow.start()
