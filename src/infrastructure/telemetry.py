import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger(__name__)

def setup_telemetry(app: FastAPI, service_name: str = "python-ddd-inventory"):
    """
    Integrates OpenTelemetry Distributed Tracing & Observability.
    """
    logger.info(f"Setting up OpenTelemetry for {service_name}")
    
    resource = Resource(attributes={
        SERVICE_NAME: service_name
    })

    provider = TracerProvider(resource=resource)
    
    # In a real app, this would export to Jaeger, Zipkin, or OTLP collector.
    # We use ConsoleSpanExporter for local development/parity testing.
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    
    trace.set_tracer_provider(provider)

    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)
