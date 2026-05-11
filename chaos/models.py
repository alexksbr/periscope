from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class FailureKind(StrEnum):
    latency = "latency"
    http_error = "http_error"
    timeout = "timeout"
    dependency_error = "dependency_error"
    malformed_response = "malformed_response"


class FailureRule(BaseModel):
    kind: FailureKind
    enabled: bool = True
    probability: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    latency_ms: Annotated[int, Field(ge=0, le=30_000)] = 500
    status_code: Annotated[int, Field(ge=400, le=599)] = 503
    message: str = "injected chaos failure"


class FailureRuleResponse(FailureRule):
    target: str


class FailureState(BaseModel):
    rules: list[FailureRuleResponse]


class FailureResponse(BaseModel):
    failure: FailureKind
    target: str
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


class CatalogItem(BaseModel):
    item_id: str
    sku: str
    name: str
    price_usd: float
    vendor: str


class CatalogResponse(BaseModel):
    items: list[CatalogItem]


class InventoryResponse(BaseModel):
    sku: str
    available: int
    reserved: int


class CheckoutRequest(BaseModel):
    item_id: str
    quantity: Annotated[int, Field(ge=1, le=20)] = 1
    customer_id: str = "demo-customer"


class CheckoutResponse(BaseModel):
    order_id: str
    item_id: str
    sku: str
    quantity: int
    status: Literal["paid"]
    total_usd: float


class AssistantRequest(BaseModel):
    prompt: Annotated[str, Field(min_length=1, max_length=2_000)]
    model: str = "periscope-sim-small"


class AssistantResponse(BaseModel):
    answer: str
    model: str
    input_tokens: int
    output_tokens: int
