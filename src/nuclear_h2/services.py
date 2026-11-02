"""Ancillary-service product lists of the seven U.S. ISO/RTO markets, as named in the article's Supplementary
Table 6, turned into :class:`~nuclear_h2.parameters.Service` sets.

The direction of each product and the representation of a single symmetric regulation product (an up and a down
service on the same product, half the payments each, with equal bids by Eq. 44) are conventions of this
implementation. Market rules change; check each product against the ISO's current market manuals before use.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Tuple

from .parameters import COMPONENTS, ParameterError, Service

# product abbreviation -> (description, direction); "symmetric" = one regulation product in both directions
ISO_PRODUCTS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "SPP": {"RegU": ("Regulation Up", "up"), "RegD": ("Regulation Down", "down"), "RamU": ("Ramp Up", "up"),
            "RamD": ("Ramp Down", "down"), "Spin": ("Spinning Reserve", "up"), "Sup": ("Supplemental Reserve", "up"),
            "UncU": ("Uncertainty Up", "up")},
    "CAISO": {"RegU": ("Regulation Up", "up"), "RegD": ("Regulation Down", "down"), "Spin": ("Spinning Reserve", "up"),
              "NSpin": ("Non-spinning Reserve", "up"), "RMU": ("Ramp Up", "up"), "RMD": ("Ramp Down", "down")},
    "ERCOT": {"RegU": ("Regulation Up", "up"), "RegD": ("Regulation Down", "down"),
              "Spin": ("Responsive Reserve Service (mapped to Spin)", "up"), "NSpin": ("Non-spinning Reserve", "up"),
              "ECRS": ("ERCOT Contingency Reserve Service", "up")},
    "PJM": {"Rse": ("Primary Reserve", "up"), "Reg": ("Regulation", "symmetric"),
            "Syn": ("Synchronized Reserve", "up"), "TMR": ("Thirty-Minute Reserve", "up")},
    "NYISO": {"Spin10": ("10-Minute Spinning Reserve", "up"), "NSpin10": ("10-Minute Non-spinning Reserve", "up"),
              "Res30": ("30-Minute Reserve", "up"), "RegC": ("Regulation Capacity", "symmetric")},
    "ISONE": {"Spin10": ("Ten-Minute Spinning Reserve", "up"), "NSpin10": ("Ten-Minute Non-spinning Reserve", "up"),
              "OR30": ("Thirty-Minute Operating Reserve", "up")},
    "MISO": {"RamU": ("Ramp Up", "up"), "RamD": ("Ramp Down", "down"), "Spin": ("Spinning Reserve", "up"),
             "STR": ("Short-Term Reserve", "up"), "Sup": ("Supplemental Reserve", "up"),
             "Reg": ("Regulation", "symmetric")},
}


def iso_services(iso: str, components: Tuple[str, ...] = COMPONENTS, mileage: bool = False) -> Tuple[Service, ...]:
    """Service set of one ISO. A symmetric regulation product P becomes services 'P_up' and 'P_down' on product P
    (half the payments each, balanced bids). Separate regulation-up and regulation-down products are paired for
    joint generation of their series. ``mileage=True`` enables the mileage payment on regulation."""
    key = iso.upper().replace("-", "")
    if key not in ISO_PRODUCTS:
        raise ParameterError(f"unknown ISO {iso}; known: {sorted(ISO_PRODUCTS)}")
    out = []
    for product, (_, direction) in ISO_PRODUCTS[key].items():
        regulation = product.startswith("Reg")
        if direction == "symmetric":
            out.append(Service(f"{product}_up", "up", product=product, price_share=0.5, components=components,
                               balance_with=f"{product}_down", mileage=mileage))
            out.append(Service(f"{product}_down", "down", product=product, price_share=0.5, components=components,
                               balance_with=f"{product}_up", mileage=mileage))
        else:
            out.append(Service(product, direction, components=components, mileage=mileage and regulation))
    # separate regulation-up and regulation-down products are generated together (no equal-bid constraint)
    names = {s.name for s in out}
    for up_name, down_name in (("RegU", "RegD"),):
        if up_name in names and down_name in names:
            out = [replace(s, pair_with=down_name) if s.name == up_name else s for s in out]
    return tuple(out)


__all__ = ["ISO_PRODUCTS", "iso_services"]
