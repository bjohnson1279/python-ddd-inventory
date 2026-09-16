import pytest
from src.domain.hardware.printer import ThermalPrinterEngine

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
