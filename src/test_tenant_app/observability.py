"""Minimal OTEL tracing setup for the app.

A tracer provider so SkillClient spans are real and their W3C trace context
propagates to the skill runtime (see clients/skill_client.py). Spans export via
OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; otherwise the provider still mints
valid span contexts so propagation works even without a local collector.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider

_configured = False


def setup_tracing() -> None:
    """Install a TracerProvider once (idempotent). Best-effort OTLP export."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "test-tenant-app"}))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True
