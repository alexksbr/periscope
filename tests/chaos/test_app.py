from __future__ import annotations

from chaos.app import create_app
from fastapi.testclient import TestClient


def test_health_happy_path() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_catalog_happy_path() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.get("/catalog/items")

    assert response.status_code == 200
    assert response.json()["items"][0]["item_id"] == "probe"


def test_checkout_happy_path_creates_order() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.post(
        "/checkout",
        json={"item_id": "probe", "quantity": 2, "customer_id": "customer-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paid"
    assert body["sku"] == "peri-probe"
    assert body["total_usd"] == 38.0


def test_checkout_reserves_inventory() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    before = client.get("/inventory/peri-probe")
    checkout = client.post("/checkout", json={"item_id": "probe", "quantity": 3})
    after = client.get("/inventory/peri-probe")

    assert before.status_code == 200
    assert checkout.status_code == 200
    assert after.status_code == 200
    assert before.json()["available"] == 42
    assert after.json()["available"] == 39


def test_checkout_rejects_insufficient_inventory() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.post("/checkout", json={"item_id": "lens", "quantity": 10})
    inventory = client.get("/inventory/peri-lens")

    assert response.status_code == 409
    assert response.json()["detail"]["message"] == "insufficient stock"
    assert inventory.json()["available"] == 9


def test_per_request_failure_query_param() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.get("/inventory/peri-probe?failure=http_error")

    assert response.status_code == 503
    assert response.json()["detail"]["failure"] == "http_error"


def test_header_failure_override_takes_precedence_over_query_param() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.get(
        "/inventory/peri-probe?failure=http_error",
        headers={"x-chaos-failure": "malformed_response"},
    )

    assert response.status_code == 200
    assert response.json()["malformed"] is True


def test_global_failure_rule_can_be_set_and_cleared() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    set_response = client.put(
        "/admin/failures/checkout",
        json={
            "kind": "dependency_error",
            "probability": 1.0,
            "message": "payment provider unavailable",
        },
    )
    failed_checkout = client.post("/checkout", json={"item_id": "probe", "quantity": 1})
    clear_response = client.delete("/admin/failures/checkout")
    healthy_checkout = client.post("/checkout", json={"item_id": "probe", "quantity": 1})

    assert set_response.status_code == 200
    assert failed_checkout.status_code == 502
    assert clear_response.status_code == 204
    assert healthy_checkout.status_code == 200


def test_global_failure_rule_applies_to_catalog_endpoint() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    set_response = client.put(
        "/admin/failures/*",
        json={"kind": "http_error", "status_code": 418, "message": "global outage"},
    )
    response = client.get("/catalog/items")

    assert set_response.status_code == 200
    assert response.status_code == 418
    assert response.json()["detail"] == {
        "failure": "http_error",
        "target": "*",
        "message": "global outage",
    }


def test_latency_failure_rule_allows_request_to_complete() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    set_response = client.put(
        "/admin/failures/catalog.list",
        json={"kind": "latency", "latency_ms": 0},
    )
    response = client.get("/catalog/items")

    assert set_response.status_code == 200
    assert response.status_code == 200
    assert response.json()["items"][0]["item_id"] == "probe"


def test_timeout_failure_rule_returns_gateway_timeout() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    set_response = client.put(
        "/admin/failures/inventory.read",
        json={"kind": "timeout", "latency_ms": 0, "message": "inventory stalled"},
    )
    response = client.get("/inventory/peri-probe")

    assert set_response.status_code == 200
    assert response.status_code == 504
    assert response.json()["detail"] == {
        "failure": "timeout",
        "target": "inventory.read",
        "message": "inventory stalled",
    }


def test_unknown_failure_target_is_rejected() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.put(
        "/admin/failures/not-a-target",
        json={"kind": "http_error"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["target"] == "not-a-target"


def test_malformed_response_failure_returns_bad_shape() -> None:
    client = TestClient(create_app(enable_telemetry=False))

    response = client.post(
        "/assistant/respond?failure=malformed_response",
        json={"prompt": "summarize checkout"},
    )

    assert response.status_code == 200
    assert response.json()["malformed"] is True
