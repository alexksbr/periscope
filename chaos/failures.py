from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Final

from fastapi import HTTPException
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from starlette.responses import JSONResponse, Response

from chaos.models import (
    FailureKind,
    FailureResponse,
    FailureRule,
    FailureRuleResponse,
    FailureState,
)

GLOBAL_TARGET: Final[str] = "*"


@dataclass(frozen=True, slots=True)
class ActiveFailure:
    target: str
    rule: FailureRule


class FailureRegistry:
    def __init__(self, *, seed: int | None = None) -> None:
        self._rules: dict[str, FailureRule] = {}
        self._random = random.Random(seed)

    def set_rule(self, target: str, rule: FailureRule) -> FailureRuleResponse:
        self._rules[target] = rule
        return FailureRuleResponse(target=target, **rule.model_dump())

    def clear_rule(self, target: str) -> None:
        self._rules.pop(target, None)

    def clear(self) -> None:
        self._rules.clear()

    def state(self) -> FailureState:
        return FailureState(
            rules=[
                FailureRuleResponse(target=target, **rule.model_dump())
                for target, rule in sorted(self._rules.items())
            ]
        )

    def resolve(self, target: str, override: FailureKind | None) -> ActiveFailure | None:
        if override is not None:
            return ActiveFailure(target=target, rule=FailureRule(kind=override))

        for candidate in (target, GLOBAL_TARGET):
            rule = self._rules.get(candidate)
            if rule is None or not rule.enabled:
                continue
            if self._random.random() <= rule.probability:
                return ActiveFailure(target=candidate, rule=rule)

        return None


async def apply_failure(
    registry: FailureRegistry,
    *,
    target: str,
    override: FailureKind | None,
) -> Response | None:
    active = registry.resolve(target, override)
    if active is None:
        return None

    rule = active.rule
    span = trace.get_current_span()
    span.set_attribute("chaos.failure.target", active.target)
    span.set_attribute("chaos.failure.kind", rule.kind.value)
    span.set_attribute("chaos.failure.message", rule.message)

    match rule.kind:
        case FailureKind.latency:
            await asyncio.sleep(rule.latency_ms / 1_000)
            return None
        case FailureKind.http_error:
            _mark_error(rule.message)
            raise HTTPException(
                status_code=rule.status_code,
                detail=FailureResponse(
                    failure=rule.kind,
                    target=active.target,
                    message=rule.message,
                ).model_dump(mode="json"),
            )
        case FailureKind.timeout:
            await asyncio.sleep(rule.latency_ms / 1_000)
            _mark_error(rule.message)
            raise HTTPException(
                status_code=504,
                detail=FailureResponse(
                    failure=rule.kind,
                    target=active.target,
                    message=rule.message,
                ).model_dump(mode="json"),
            )
        case FailureKind.dependency_error:
            _record_dependency_failure(rule.message)
            _mark_error(rule.message)
            raise HTTPException(
                status_code=502,
                detail=FailureResponse(
                    failure=rule.kind,
                    target=active.target,
                    message=rule.message,
                ).model_dump(mode="json"),
            )
        case FailureKind.malformed_response:
            _mark_error(rule.message)
            return JSONResponse(
                status_code=200,
                content={
                    "malformed": True,
                    "target": active.target,
                    "message": rule.message,
                },
            )


def _record_dependency_failure(message: str) -> None:
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("chaos.dependency.call") as span:
        span.set_attribute("peer.service", "payments-sim")
        span.set_attribute("rpc.system", "http")
        span.set_attribute("chaos.failure.message", message)
        span.set_status(Status(StatusCode.ERROR, message))


def _mark_error(message: str) -> None:
    trace.get_current_span().set_status(Status(StatusCode.ERROR, message))
