"""Calculul prețului de vânzare LC boutique."""

from __future__ import annotations

from dataclasses import dataclass

from .config import PricingConfig
from .models import PROFIT_RANGES, SupplierPrice


class PricingError(Exception):
    """Prețul nu poate fi calculat automat (lipsește sau e într-o monedă necunoscută)."""


CURRENCY_SYMBOL = {"UAH": "грн", "USD": "$"}


def rate_to_lei(currency: str, cfg: PricingConfig) -> float | None:
    return {"UAH": cfg.uah_to_lei, "USD": cfg.usd_to_lei}.get(currency)


@dataclass(frozen=True)
class PriceResult:
    supplier_amount: float
    supplier_currency: str
    supplier_kind: str
    rate: float
    base_lei: float  # (preț producător × curs) + transport
    profit_lei: int  # profitul efectiv, după rotunjire
    final_lei: int

    def explain(self, cfg: PricingConfig) -> str:
        symbol = CURRENCY_SYMBOL.get(self.supplier_currency, self.supplier_currency)
        return (
            f"{self.supplier_amount:g} {symbol} ({self.supplier_kind}) × {self.rate:g} "
            f"+ {cfg.shipping_lei} transport + {self.profit_lei} profit = {self.final_lei} lei"
        )


def pick_supplier_price(prices: list[SupplierPrice], cfg: PricingConfig) -> SupplierPrice:
    """Prețul drop dacă există (apoi opt, preț simplu, retail); grivnele au prioritate față de dolari."""
    usable = [p for p in prices if p.amount > 0 and rate_to_lei(p.currency, cfg) is not None]
    if not usable:
        if any(p.amount > 0 for p in prices):
            raise PricingError("prețul producătorului nu este în grivne sau dolari")
        raise PricingError("nu am găsit prețul producătorului")
    kind_rank = {kind: i for i, kind in enumerate(cfg.price_priority)}
    currency_rank = {"UAH": 0, "USD": 1}
    return min(
        usable,
        key=lambda p: (kind_rank.get(p.kind, len(kind_rank)), currency_rank[p.currency]),
    )


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
    rate = rate_to_lei(supplier.currency, cfg)
    base = supplier.amount * rate + cfg.shipping_lei
    final = round_nice(base + profit, base + pmin, base + pmax, cfg.nice_endings)
    return PriceResult(
        supplier_amount=supplier.amount,
        supplier_currency=supplier.currency,
        supplier_kind=supplier.kind,
        rate=rate,
        base_lei=base,
        profit_lei=round(final - base),
        final_lei=final,
    )
