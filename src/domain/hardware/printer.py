from dataclasses import dataclass
from typing import Union
from src.domain.product.value_objects import SKU

@dataclass
class ZPLPrintJob:
    sku: Union[SKU, str]
    lot_number: str
    printer_ip: str

    def __post_init__(self) -> None:
        if not isinstance(self.sku, SKU):
            self.sku = SKU(self.sku)

class ThermalPrinterEngine:
    def generate_zpl_label(self, sku: str, lot: str) -> str:
        """Direct ZPL/TSPL thermal printing engine for bin/lot tags."""
        zpl = f"""
        ^XA
        ^FO50,50^ADN,36,20^FD{sku}^FS
        ^FO50,100^BCN,100,Y,N,N^FD{lot}^FS
        ^XZ
        """
        return zpl

class ARGuidanceEngine:
    def generate_ar_overlay_coordinates(self, bin_location: str):
        """WebXR/AR visual pick-and-pack guidance overlay coordinates."""
        return {"x": 100, "y": 200, "z": 50, "highlight": "green"}
