import os
from unittest.mock import MagicMock, patch
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.resources import SERVICE_NAME

from src.infrastructure.telemetry import setup_telemetry


def test_setup_telemetry_disabled_when_env_var_true():
    app = FastAPI()
    with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "true"}), \
         patch("src.infrastructure.telemetry.Resource") as mock_resource, \
         patch("src.infrastructure.telemetry.TracerProvider") as mock_provider, \
         patch("src.infrastructure.telemetry.FastAPIInstrumentor") as mock_instrumentor, \
         patch("src.infrastructure.telemetry.logger") as mock_logger:

        setup_telemetry(app)

        mock_resource.assert_not_called()
        mock_provider.assert_not_called()
        mock_instrumentor.instrument_app.assert_not_called()
        mock_logger.info.assert_not_called()


@pytest.mark.parametrize("disabled_val", ["True", "TRUE", "true"])
def test_setup_telemetry_disabled_case_insensitive(disabled_val):
    app = FastAPI()
    with patch.dict(os.environ, {"OTEL_SDK_DISABLED": disabled_val}), \
         patch("src.infrastructure.telemetry.FastAPIInstrumentor") as mock_instrumentor:

        setup_telemetry(app)
        mock_instrumentor.instrument_app.assert_not_called()


def test_setup_telemetry_enabled_default_service_name():
    app = FastAPI()
    with patch.dict(os.environ, {}, clear=True), \
         patch("src.infrastructure.telemetry.Resource") as mock_resource_cls, \
         patch("src.infrastructure.telemetry.TracerProvider") as mock_provider_cls, \
         patch("src.infrastructure.telemetry.BatchSpanProcessor") as mock_batch_proc_cls, \
         patch("src.infrastructure.telemetry.ConsoleSpanExporter") as mock_console_exp_cls, \
         patch("src.infrastructure.telemetry.trace.set_tracer_provider") as mock_set_provider, \
         patch("src.infrastructure.telemetry.FastAPIInstrumentor") as mock_instrumentor, \
         patch("src.infrastructure.telemetry.logger") as mock_logger:

        mock_resource_instance = MagicMock()
        mock_resource_cls.return_value = mock_resource_instance

        mock_provider_instance = MagicMock()
        mock_provider_cls.return_value = mock_provider_instance

        mock_exporter_instance = MagicMock()
        mock_console_exp_cls.return_value = mock_exporter_instance

        mock_processor_instance = MagicMock()
        mock_batch_proc_cls.return_value = mock_processor_instance

        setup_telemetry(app)

        mock_logger.info.assert_called_once_with("Setting up OpenTelemetry for python-ddd-inventory")
        mock_resource_cls.assert_called_once_with(attributes={SERVICE_NAME: "python-ddd-inventory"})
        mock_provider_cls.assert_called_once_with(resource=mock_resource_instance)
        mock_console_exp_cls.assert_called_once_with()
        mock_batch_proc_cls.assert_called_once_with(mock_exporter_instance)
        mock_provider_instance.add_span_processor.assert_called_once_with(mock_processor_instance)
        mock_set_provider.assert_called_once_with(mock_provider_instance)
        mock_instrumentor.instrument_app.assert_called_once_with(app)


def test_setup_telemetry_custom_service_name():
    app = FastAPI()
    custom_service = "custom-inventory-service"
    with patch.dict(os.environ, {"OTEL_SDK_DISABLED": "false"}), \
         patch("src.infrastructure.telemetry.Resource") as mock_resource_cls, \
         patch("src.infrastructure.telemetry.TracerProvider") as mock_provider_cls, \
         patch("src.infrastructure.telemetry.BatchSpanProcessor") as mock_batch_proc_cls, \
         patch("src.infrastructure.telemetry.ConsoleSpanExporter") as mock_console_exp_cls, \
         patch("src.infrastructure.telemetry.trace.set_tracer_provider") as mock_set_provider, \
         patch("src.infrastructure.telemetry.FastAPIInstrumentor") as mock_instrumentor, \
         patch("src.infrastructure.telemetry.logger") as mock_logger:

        mock_resource_instance = MagicMock()
        mock_resource_cls.return_value = mock_resource_instance
        mock_provider_instance = MagicMock()
        mock_provider_cls.return_value = mock_provider_instance

        setup_telemetry(app, service_name=custom_service)

        mock_logger.info.assert_called_once_with(f"Setting up OpenTelemetry for {custom_service}")
        mock_resource_cls.assert_called_once_with(attributes={SERVICE_NAME: custom_service})
        mock_provider_cls.assert_called_once_with(resource=mock_resource_instance)
        mock_set_provider.assert_called_once_with(mock_provider_instance)
        mock_instrumentor.instrument_app.assert_called_once_with(app)
