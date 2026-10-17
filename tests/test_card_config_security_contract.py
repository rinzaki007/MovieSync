"""Security contract tests for third-party card configuration APIs."""
from __future__ import annotations

from moviesync.cards import Card, CardManifest


class SecretConfigCard(Card):
    manifest = CardManifest(
        id="secret-config-demo",
        name="Secret config demo",
        type="custom",
        config_fields=(
            {
                "key": "cookie",
                "label": "Cookie",
                "type": "password",
                "required": True,
                "secret": True,
            },
            {
                "key": "endpoint",
                "label": "Endpoint",
                "type": "string",
                "required": True,
            },
        ),
    )


def _configured_client(tmp_path):
    from moviesync.app import create_app

    app = create_app({"MOVIESYNC_DATA_DIR": str(tmp_path)}, start_scheduler=False)
    services = app.extensions["moviesync"]
    client = app.test_client()
    client.post(
        "/api/setup",
        json={"username": "admin", "password": "password123"},
    )
    with client.session_transaction() as session:
        csrf = session["csrf_token"]

    card = SecretConfigCard()
    services["card_registry"].register(card)
    services["config"].save_card_config(
        card.card_id,
        {
            "cookie": "sensitive-cookie-value",
            "endpoint": "https://example.invalid",
            "undeclared_internal_value": "must-not-leak",
        },
    )
    return client, services, {"X-CSRF-Token": csrf}, card


def test_card_config_get_redacts_secrets_and_exposes_only_declared_fields(tmp_path):
    client, _services, headers, card = _configured_client(tmp_path)

    response = client.get(f"/api/cards/{card.card_id}/config", headers=headers)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["config"] == {"cookie": "", "endpoint": "https://example.invalid"}
    assert payload["has_value"]["cookie"] is True
    assert "sensitive-cookie-value" not in response.get_data(as_text=True)
    assert "must-not-leak" not in response.get_data(as_text=True)


def test_blank_secret_update_preserves_existing_credential(tmp_path):
    client, services, headers, card = _configured_client(tmp_path)

    response = client.post(
        f"/api/cards/{card.card_id}/config",
        headers=headers,
        json={"config": {"cookie": "   ", "endpoint": "https://updated.example.invalid"}},
    )

    assert response.status_code == 200
    saved = services["config"].load()["cards"][card.card_id]["config"]
    assert saved["cookie"] == "sensitive-cookie-value"
    assert saved["endpoint"] == "https://updated.example.invalid"
    payload = response.get_json()
    assert payload["config"]["cookie"] == ""
    assert payload["has_value"]["cookie"] is True


def test_card_config_rejects_undeclared_fields_without_persisting_them(tmp_path):
    client, services, headers, card = _configured_client(tmp_path)

    response = client.post(
        f"/api/cards/{card.card_id}/config",
        headers=headers,
        json={"config": {"endpoint": "https://updated.example.invalid", "unexpected": "value"}},
    )

    assert response.status_code == 400
    saved = services["config"].load()["cards"][card.card_id]["config"]
    assert saved["endpoint"] == "https://example.invalid"
    assert saved["cookie"] == "sensitive-cookie-value"
