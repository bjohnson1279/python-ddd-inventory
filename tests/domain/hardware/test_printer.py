import pytest
from src.domain.hardware.printer import ThermalPrinterEngine, ZPLPrintJob
from src.domain.product.value_objects import SKU
from src.domain.product.exceptions import InvalidSKUError

def test_generate_zpl_label():
    engine = ThermalPrinterEngine()
    sku = "SKU12345"
    lot = "LOT67890"

    zpl = engine.generate_zpl_label(sku, lot)

    assert isinstance(zpl, str)
    assert "^XA" in zpl
    assert "^XZ" in zpl
    assert sku in zpl
    assert lot in zpl
    assert "^FO50,50^ADN,36,20^FD" + sku + "^FS" in zpl
    assert "^FO50,100^BCN,100,Y,N,N^FD" + lot + "^FS" in zpl


def test_zpl_print_job_coerces_string_to_sku():
    job = ZPLPrintJob(sku="TEST-SKU-1", lot_number="LOT-001", printer_ip="192.168.1.100")
    assert isinstance(job.sku, SKU)
    assert job.sku == SKU("TEST-SKU-1")
    assert job.sku.value == "TEST-SKU-1"


def test_zpl_print_job_accepts_sku_value_object():
    sku_vo = SKU("PROD-ITEM-99")
    job = ZPLPrintJob(sku=sku_vo, lot_number="LOT-002", printer_ip="192.168.1.101")
    assert job.sku is sku_vo
    assert job.sku == "PROD-ITEM-99"


def test_zpl_print_job_invalid_sku_raises():
    with pytest.raises(InvalidSKUError):
        ZPLPrintJob(sku="invalid sku with spaces", lot_number="LOT-003", printer_ip="192.168.1.102")

    with pytest.raises(InvalidSKUError):
        ZPLPrintJob(sku="", lot_number="LOT-004", printer_ip="192.168.1.103")

def test_generate_zpl_label_empty_strings():
    engine = ThermalPrinterEngine()
    sku = ""
    lot = ""

    zpl = engine.generate_zpl_label(sku, lot)

    assert "^XA" in zpl
    assert "^XZ" in zpl
    assert "^FO50,50^ADN,36,20^FD^FS" in zpl
    assert "^FO50,100^BCN,100,Y,N,N^FD^FS" in zpl

def test_generate_zpl_label_special_characters():
    engine = ThermalPrinterEngine()
    sku = "SKU-!@#"
    lot = "LOT-$%^"

    zpl = engine.generate_zpl_label(sku, lot)

    assert "^XA" in zpl
    assert "^XZ" in zpl
    assert sku in zpl
    assert lot in zpl

def test_generate_zpl_label_long_strings():
    engine = ThermalPrinterEngine()
    sku = "A" * 1000
    lot = "B" * 1000

    zpl = engine.generate_zpl_label(sku, lot)

    assert "^XA" in zpl
    assert "^XZ" in zpl
    assert sku in zpl
    assert lot in zpl

def test_generate_zpl_label_unicode():
    engine = ThermalPrinterEngine()
    sku = "SKU-🚀"
    lot = "LOT-📦"

    zpl = engine.generate_zpl_label(sku, lot)

    assert "^XA" in zpl
    assert "^XZ" in zpl
    assert sku in zpl
    assert lot in zpl
