"""通用通知卡片接口与隔离投递测试。"""

import logging

from moviesync.cards import CardManifest, CardRegistry, NotificationCard
from moviesync.services.notifications import NotificationManager


class FakeConfigStore:
    def __init__(self, value=None):
        self.value = value or {}

    def load(self):
        return self.value


class DemoNotification(NotificationCard):
    manifest = CardManifest(
        id="demo.notification",
        name="Demo Notification",
        type="notification",
        capabilities=("notification.send",),
    )

    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def send(self, message, **kwargs):
        self.calls.append((message, kwargs["payload"], kwargs["config"]))
        if self.error:
            raise self.error
        return self.result


def test_notification_manager_sends_event_and_card_config():
    card = DemoNotification()
    registry = CardRegistry()
    registry.register(card)
    config_store = FakeConfigStore({
        "cards": {
            "demo.notification": {
                "enabled": True,
                "config": {"endpoint": "https://notify.example"},
            }
        }
    })
    manager = NotificationManager(registry, logging.getLogger("test.notifications"), config_store)

    assert manager.send("task.completed", {"task_id": "123"}) == {"demo.notification": True}
    assert card.calls == [
        ("task.completed", {"task_id": "123"}, {"endpoint": "https://notify.example"})
    ]


def test_notification_manager_skips_disabled_cards():
    card = DemoNotification()
    registry = CardRegistry()
    registry.register(card)
    config_store = FakeConfigStore({
        "cards": {"demo.notification": {"enabled": False}}
    })
    manager = NotificationManager(registry, logging.getLogger("test.notifications"), config_store)

    assert manager.send("task.completed", {}) == {}
    assert card.calls == []


def test_notification_manager_isolates_card_errors_and_invalid_results():
    registry = CardRegistry()

    class BrokenNotification(DemoNotification):
        manifest = CardManifest(
            id="broken.notification",
            name="Broken",
            type="notification",
            capabilities=("notification.send",),
        )

    class MalformedNotification(DemoNotification):
        manifest = CardManifest(
            id="malformed.notification",
            name="Malformed",
            type="notification",
            capabilities=("notification.send",),
        )

    class HealthyNotification(DemoNotification):
        manifest = CardManifest(
            id="healthy.notification",
            name="Healthy",
            type="notification",
            capabilities=("notification.send",),
        )

    registry.register(BrokenNotification(error=RuntimeError("transport error")))
    registry.register(MalformedNotification(result="sent"))
    registry.register(HealthyNotification())
    manager = NotificationManager(registry, logging.getLogger("test.notifications"))

    results = manager.send("task.failed", {"task_id": "123"})
    assert results == {
        "broken.notification": False,
        "malformed.notification": False,
        "healthy.notification": True,
    }


def test_notification_manager_validates_event_and_payload():
    manager = NotificationManager(CardRegistry(), logging.getLogger("test.notifications"))

    try:
        manager.send(" ", {})
    except ValueError as exc:
        assert "不能为空" in str(exc)
    else:
        raise AssertionError("empty event should be rejected")

    try:
        manager.send("task.completed", [])
    except TypeError as exc:
        assert "字典" in str(exc)
    else:
        raise AssertionError("non-dict payload should be rejected")


def test_notification_manager_loads_installed_plugins_and_skips_broken_ones(monkeypatch):
    from moviesync.services import notifications as notifications_module

    card = DemoNotification()
    received = {}

    class EntryPoint:
        def __init__(self, name, factory):
            self.name = name
            self.factory = factory

        def load(self):
            return self.factory

    def factory(context):
        received.update(context)
        return card

    monkeypatch.setattr(
        notifications_module,
        "entry_points",
        lambda group: [
            EntryPoint("good-channel", factory),
            EntryPoint("bad-channel", lambda context: object()),
        ] if group == "moviesync.notification_channels" else [],
    )
    registry = CardRegistry()
    manager = NotificationManager(registry, logging.getLogger("test.notifications"))
    context = {"logger": logging.getLogger("plugin")}
    assert manager.load_plugins(context) == ["demo.notification"]
    assert registry.get("demo.notification") is card
    assert received == context


def test_notification_plugin_factory_closes_card_when_registration_fails(monkeypatch):
    from moviesync.services import notifications as module

    class CandidateNotification(DemoNotification):
        def __init__(self):
            super().__init__()
            self.closed = False

        def close(self):
            self.closed = True

    candidate = CandidateNotification()

    class EntryPoint:
        name = "duplicate-notification"

        def load(self):
            return lambda context: candidate

    monkeypatch.setattr(module, "entry_points", lambda group: [EntryPoint()])
    registry = CardRegistry()
    registry.register(DemoNotification())
    manager = NotificationManager(registry, logging.getLogger("test.notifications.cleanup"))

    assert manager.load_plugins() == []
    assert candidate.closed is True
    assert registry.get("demo.notification") is not candidate



def test_notification_manager_fails_closed_when_config_cannot_be_read():
    class BrokenConfigStore:
        def load(self):
            raise OSError("simulated config read failure")

    card = DemoNotification()
    registry = CardRegistry()
    registry.register(card)
    manager = NotificationManager(
        registry,
        logging.getLogger("test.notifications.config-failure"),
        BrokenConfigStore(),
    )

    assert manager.send("task.completed", {"task_id": "123"}) == {
        "demo.notification": False
    }
    assert card.calls == []


def test_notification_manager_fails_closed_on_invalid_config_shape():
    card = DemoNotification()
    registry = CardRegistry()
    registry.register(card)
    manager = NotificationManager(
        registry,
        logging.getLogger("test.notifications.invalid-config"),
        FakeConfigStore(["not", "a", "config"]),
    )

    assert manager.send("task.completed", {}) == {"demo.notification": False}
    assert card.calls == []
