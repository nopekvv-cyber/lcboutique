"""Calculul prețului de vânzare LC boutique."""

from __future__ import annotations

from dataclasses import dataclass

from .config import PricingConfig
from .models import PROFIT_RANGES, SupplierPrice


class PricingError(Exception):
    """Prețul nu poate fi calculat automat (lipsește sau nu e în grivne)."""


@dataclass(frozen=True)
class PriceResult:
    supplier_uah: float
    supplier_kind: str
    base_lei: float  # (grivne × curs) + transport
    profit_lei: int  # profitul efectiv, după rotunjire
    final_lei: int

    def explain(self, cfg: PricingConfig) -> str:
        return (
            f"{self.supplier_uah:g} грн ({self.supplier_kind}) × {cfg.uah_to_lei:g} "
            f"+ {cfg.shipping_lei} transport + {self.profit_lei} profit = {self.final_lei} lei"
        )


def pick_supplier_price(prices: list[SupplierPrice], cfg: PricingConfig) -> SupplierPrice:
    uah = [p for p in prices if p.currency == "UAH" and p.amount > 0]
    if not uah:
        if any(p.amount > 0 for p in prices):
            raise PricingError("prețul producătorului nu este în grivne")
        raise PricingError("nu am găsit prețul producătorului")
    for kind in cfg.price_priority:
        for p in uah:
            if p.kind == kind:
                return p
    return uah[0]


def nice_candidates(low: float, high: float, endings: tuple[int, ...]) -> list[int]:
    """Toate prețurile „frumoase" (ex. 550, 580, 590) din intervalul [low, high]."""
    result = []
    for hundred in range(int(low // 100) * 100, int(high // 100) * 100 + 100, 100):
        for end in sorted(endings):
            value = hundred + end
            if low <= value <= high:
                result.append(value)
    return result


def round_nice(target: float, low: float, high: float, endings: tuple[int, ...]) -> int:
    """Cel mai apropiat preț frumos de `target`, fără a ieși din [low, high].

    Dacă intervalul nu conține niciun preț frumos, se alege primul preț
    frumos peste `low` (nu vindem niciodată sub profitul minim).
    """
    candidates = nice_candidates(low, high, endings)
    if candidates:
        # la egalitate se alege prețul mai mare
        return min(candidates, key=lambda v: (abs(v - target), -v))
    return nice_candidates(low, low + 200, endings)[0]


def calculate_price(
    prices: list[SupplierPrice], category: str, profit_lei: int, cfg: PricingConfig
) -> PriceResult:
    supplier = pick_supplier_price(prices, cfg)
    pmin, pmax = PROFIT_RANGES.get(category, PROFIT_RANGES["alta"])
    profit = max(pmin, min(pmax, profit_lei))
    base = supplier.amount * cfg.uah_to_lei + cfg.shipping_lei
    final = round_nice(base + profit, base + pmin, base + pmax, cfg.nice_endings)
    return PriceResult(
        supplier_uah=supplier.amount,
        supplier_kind=supplier.kind,
        base_lei=base,
        profit_lei=round(final - base),
        final_lei=final,
    )
