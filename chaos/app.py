from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.responses import Response

from chaos.failures import GLOBAL_TARGET, FailureRegistry, apply_failure
from chaos.models import (
    AssistantRequest,
    AssistantResponse,
    CatalogItem,
    CatalogResponse,
    CheckoutRequest,
    CheckoutResponse,
    FailureKind,
    FailureRule,
    FailureRuleResponse,
    FailureState,
    HealthResponse,
    InventoryResponse,
)
from chaos.telemetry import SERVICE_NAME, SERVICE_VERSION, configure_telemetry


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    item_id: str
    sku: str
    name: str
    price_usd: float
    vendor: str


CATALOG: Final[dict[str, CatalogRecord]] = {
    "probe": CatalogRecord(
        item_id="probe",
        sku="peri-probe",
        name="Periscope Probe",
        price_usd=19.0,
        vendor="observability-labs",
    ),
    "lens": CatalogRecord(
        item_id="lens",
        sku="peri-lens",
        name="Trace Lens",
        price_usd=49.0,
        vendor="observability-labs",
    ),
}
INITIAL_STOCK: Final[dict[str, int]] = {"peri-probe": 42, "peri-lens": 9}
FAILURE_TARGETS: Final[frozenset[str]] = frozenset(
    {
        GLOBAL_TARGET,
        "catalog.list",
        "catalog.read",
        "inventory.read",
        "checkout",
        "assistant.respond",
    }
)
tracer = trace.get_tracer(__name__)


def create_app(*, seed: int | None = None, enable_telemetry: bool = True) -> FastAPI:
    app = FastAPI(
        title="Periscope Chaos Backend",
        version=SERVICE_VERSION,
        summary="Toy backend for synthetic happy-path and failure telemetry.",
    )
    app.state.failure_registry = FailureRegistry(seed=seed)
    app.state.stock = INITIAL_STOCK.copy()

    if enable_telemetry:
        configure_telemetry(app)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service=SERVICE_NAME, version=SERVICE_VERSION)

    @app.get("/catalog/items", response_model=CatalogResponse)
    async def list_catalog_items(
        registry: FailureRegistryDep,
        override: FailureOverrideDep,
    ) -> CatalogResponse | Response:
        failure = await apply_failure(registry, target="catalog.list", override=override)
        if failure is not None:
            return failure

        with tracer.start_as_current_span("catalog.scan", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("db.system", "in_memory")
            span.set_attribute("db.operation.name", "scan")
            span.set_attribute("db.collection.name", "catalog")
            return CatalogResponse(items=[_catalog_item(record) for record in CATALOG.values()])

    @app.get("/catalog/items/{item_id}", response_model=CatalogItem)
    async def get_catalog_item(
        item_id: Annotated[str, Path(min_length=1)],
        registry: FailureRegistryDep,
        override: FailureOverrideDep,
    ) -> CatalogItem | Response:
        failure = await apply_failure(registry, target="catalog.read", override=override)
        if failure is not None:
            return failure
        return _catalog_item(_lookup_item(item_id))

    @app.get("/inventory/{sku}", response_model=InventoryResponse)
    async def get_inventory(
        sku: Annotated[str, Path(min_length=1)],
        request: Request,
        registry: FailureRegistryDep,
        override: FailureOverrideDep,
    ) -> InventoryResponse | Response:
        failure = await apply_failure(registry, target="inventory.read", override=override)
        if failure is not None:
            return failure

        available = _read_stock(request, sku)
        return InventoryResponse(sku=sku, available=available, reserved=0)

    @app.post("/checkout", response_model=CheckoutResponse)
    async def checkout(
        payload: CheckoutRequest,
        request: Request,
        registry: FailureRegistryDep,
        override: FailureOverrideDep,
    ) -> CheckoutResponse | Response:
        failure = await apply_failure(registry, target="checkout", override=override)
        if failure is not None:
            return failure

        record = _lookup_item(payload.item_id)
        _reserve_stock(request, record.sku, payload.quantity)
        _authorize_payment(payload.customer_id, record.price_usd * payload.quantity)
        return CheckoutResponse(
            order_id=f"ord_{uuid.uuid4().hex[:12]}",
            item_id=record.item_id,
            sku=record.sku,
            quantity=payload.quantity,
            status="paid",
            total_usd=round(record.price_usd * payload.quantity, 2),
        )

    @app.post("/assistant/respond", response_model=AssistantResponse)
    async def assistant_respond(
        payload: AssistantRequest,
        registry: FailureRegistryDep,
        override: FailureOverrideDep,
    ) -> AssistantResponse | Response:
        failure = await apply_failure(registry, target="assistant.respond", override=override)
        if failure is not None:
            return failure

        with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT) as span:
            input_tokens = max(1, len(payload.prompt.split()))
            output_tokens = min(80, input_tokens + 17)
            span.set_attribute("gen_ai.system", "simulator")
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", payload.model)
            span.set_attribute("gen_ai.response.model", payload.model)
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            return AssistantResponse(
                answer=(
                    "Simulated investigation response: checkout, catalog, "
                    "and inventory look healthy."
                ),
                model=payload.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    @app.get("/admin/failures", response_model=FailureState)
    async def list_failures(registry: FailureRegistryDep) -> FailureState:
        return registry.state()

    @app.put("/admin/failures/{target:path}", response_model=FailureRuleResponse)
    async def set_failure(
        target: str,
        rule: FailureRule,
        registry: FailureRegistryDep,
    ) -> FailureRuleResponse:
        _validate_failure_target(target)
        return registry.set_rule(target, rule)

    @app.delete("/admin/failures/{target:path}", status_code=204)
    async def clear_failure(target: str, registry: FailureRegistryDep) -> None:
        _validate_failure_target(target)
        registry.clear_rule(target)

    @app.delete("/admin/failures", status_code=204)
    async def clear_failures(registry: FailureRegistryDep) -> None:
        registry.clear()

    return app


def get_failure_registry(request: Request) -> FailureRegistry:
    registry = request.app.state.failure_registry
    if not isinstance(registry, FailureRegistry):
        raise RuntimeError("failure registry is not configured")
    return registry


async def get_failure_override(
    failure: Annotated[FailureKind | None, Query()] = None,
    x_chaos_failure: Annotated[FailureKind | None, Header(alias="x-chaos-failure")] = None,
) -> FailureKind | None:
    return x_chaos_failure or failure


FailureRegistryDep = Annotated[FailureRegistry, Depends(get_failure_registry)]
FailureOverrideDep = Annotated[FailureKind | None, Depends(get_failure_override)]


def _validate_failure_target(target: str) -> None:
    if target not in FAILURE_TARGETS:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "unknown failure target",
                "target": target,
                "allowed_targets": sorted(FAILURE_TARGETS),
            },
        )


def _catalog_item(record: CatalogRecord) -> CatalogItem:
    return CatalogItem(
        item_id=record.item_id,
        sku=record.sku,
        name=record.name,
        price_usd=record.price_usd,
        vendor=record.vendor,
    )


def _lookup_item(item_id: str) -> CatalogRecord:
    with tracer.start_as_current_span("catalog.lookup", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("db.system", "in_memory")
        span.set_attribute("db.operation.name", "lookup")
        span.set_attribute("db.collection.name", "catalog")
        span.set_attribute("catalog.item_id", item_id)
        record = CATALOG.get(item_id)
        if record is None:
            span.set_status(Status(StatusCode.ERROR, "item not found"))
            raise HTTPException(status_code=404, detail={"message": "item not found"})
        return record


def _read_stock(request: Request, sku: str) -> int:
    stock = request.app.state.stock
    if not isinstance(stock, dict):
        raise RuntimeError("stock store is not configured")

    with tracer.start_as_current_span("inventory.read", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("db.system", "in_memory")
        span.set_attribute("db.operation.name", "read")
        span.set_attribute("db.collection.name", "inventory")
        span.set_attribute("inventory.sku", sku)
        available = stock.get(sku)
        if not isinstance(available, int):
            span.set_status(Status(StatusCode.ERROR, "sku not found"))
            raise HTTPException(status_code=404, detail={"message": "sku not found"})
        return available


def _reserve_stock(request: Request, sku: str, quantity: int) -> None:
    stock = request.app.state.stock
    if not isinstance(stock, dict):
        raise RuntimeError("stock store is not configured")

    with tracer.start_as_current_span("inventory.reserve", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("db.system", "in_memory")
        span.set_attribute("db.operation.name", "update")
        span.set_attribute("db.collection.name", "inventory")
        span.set_attribute("inventory.sku", sku)
        available = stock.get(sku)
        if not isinstance(available, int):
            span.set_status(Status(StatusCode.ERROR, "sku not found"))
            raise HTTPException(status_code=404, detail={"message": "sku not found"})
        if available < quantity:
            span.set_status(Status(StatusCode.ERROR, "insufficient stock"))
            raise HTTPException(status_code=409, detail={"message": "insufficient stock"})
        stock[sku] = available - quantity


def _authorize_payment(customer_id: str, amount_usd: float) -> None:
    with tracer.start_as_current_span("payment.authorize", kind=SpanKind.CLIENT) as span:
        span.set_attribute("peer.service", "payments-sim")
        span.set_attribute("payment.customer_id", customer_id)
        span.set_attribute("payment.amount_usd", round(amount_usd, 2))


app = create_app()
