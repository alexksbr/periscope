from __future__ import annotations

import os
from typing import Final

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME: Final[str] = "periscope-chaos"
SERVICE_VERSION: Final[str] = "0.1.0"

_tracer_provider_configured = False


def configure_telemetry(app: FastAPI) -> None:
    _configure_tracer_provider()
    FastAPIInstrumentor.instrument_app(app)


def _configure_tracer_provider() -> None:
    global _tracer_provider_configured

    if _tracer_provider_configured:
        return

    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": SERVICE_VERSION,
            "deployment.environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer_provider_configured = True
