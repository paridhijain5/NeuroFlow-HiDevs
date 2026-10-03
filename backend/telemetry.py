from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from config import settings


def setup_tracing(service_name: str | None = None) -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name or settings.service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
