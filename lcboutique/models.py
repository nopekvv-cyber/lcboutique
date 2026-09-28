"""Structura datelor extrase de Claude din postarea producătorului."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "rochie",
    "costum",
    "costum_sport",
    "compleu",
    "bluza",
    "camasa",
    "body",
    "top",
    "maleta",
    "cardigan",
    "sacou",
    "pantaloni",
    "blugi",
    "fusta",
    "vesta",
    "geaca",
    "palton",
    "alta",
]

# Ordinea în care se publică produsele dintr-un lot (grupare pe categorii).
CATEGORY_ORDER: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]

# Intervalul de profit (lei) pe categorie, conform regulilor LC boutique.
PROFIT_RANGES: dict[str, tuple[int, int]] = {
    # produse ieftine
    "maleta": (100, 150),
    "top": (100, 150),
    "body": (100, 150),
    "bluza": (100, 150),
    # preț mediu
    "rochie": (150, 200),
    "camasa": (150, 200),
    "pantaloni": (150, 200),
    "blugi": (150, 200),
    "fusta": (150, 200),
    "compleu": (150, 200),
    "alta": (150, 200),
    # produse mai consistente
    "costum": (150, 200),
    "costum_sport": (150, 200),
    "sacou": (150, 200),
    "cardigan": (150, 200),
    # articole de exterior
    "vesta": (200, 300),
    "geaca": (200, 300),
    "palton": (200, 300),
}

PriceKind = Literal["drop", "opt", "retail", "unspecified"]


class SupplierPrice(BaseModel):
    kind: PriceKind = Field(
        description=(
            "drop = preț dropshipping (дроп); opt = preț angro (опт/оптова); "
            "retail = preț de vânzare recomandat (роздріб/розниця/РРЦ); "
            "unspecified = un singur preț fără tip"
        )
    )
    amount: float
    currency: Literal["UAH", "USD", "EUR", "OTHER"]


class Product(BaseModel):
    category: Category
    title: str = Field(description="Denumirea produsului în română, scurtă și elegantă")
    code: str = Field(description="Codul/modelul/articolul EXACT, copiat caracter cu caracter; gol dacă lipsește")
    sizes: str = Field(description="Mărimile exact ca la producător; gol dacă lipsesc")
    material: str = Field(description="Stofa/materialul în română; gol dacă lipsește")
    composition: str = Field(description="Compoziția (ex. 95% bumbac, 5% elastan); gol dacă lipsește")
    colors: list[str] = Field(description="Culorile în română")
    description: str = Field(description="Descriere scurtă, feminină, în română (1-3 propoziții)")
    details: list[str] = Field(
        description="Detalii importante: croială, lungime, căptușeală, fermoar/nasturi, buzunare, talie, elastic, glugă, centură etc."
    )
    measurements: list[str] = Field(description="Măsurătorile produsului, dacă sunt date")
    prices: list[SupplierPrice] = Field(description="Toate prețurile producătorului pentru ACEST produs")
    profit_lei: int = Field(description="Profitul ales din intervalul categoriei")
    photo_indices: list[int] = Field(description="Indicii fotografiilor (#0, #1, ...) care aparțin acestui produs")


class Analysis(BaseModel):
    products: list[Product]
    notes: str = Field(description="Probleme sau nelămuriri pentru proprietară, în română; gol dacă totul e în regulă")
