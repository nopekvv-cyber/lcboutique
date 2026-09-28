"""Structura datelor extrase de Claude din postarea producătorului."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Topicurile din grupul de vânzare LC boutique, în ordinea din grup: cheie → numele topicului.
TOPICS: dict[str, str] = {
    "scurte_trenciuri": "Scurte / trenciuri",
    "costume": "Costume",
    "costume_sport": "Costume sport",
    "pantaloni": "Pantaloni",
    "pulovere": "Pulovere",
    "malete_body": "Malete / body",
    "rochite": "Rochițe",
    "cardigane": "Cardigane",
    "bluzite": "Bluzițe",
    "paltoane": "Paltoane",
    "camasi": "Cămăși",
    "costume_clasice": "Costume clasice",
    "sacouri": "Sacouri",
    "fustite_sorti": "Fustițe / șorți",
    "jalete": "Jalete",
    "pijamale": "Pijamale",
    "leghensi": "Leghensi",
    "rochii_elegante": "Rochii elegante",
    "salopete": "Salopete",
    "haine_pe_loc": "Haine pe loc în Chișinău",
    "topuri_corsete": "Topuri / corsete",
    "costume_tricotate": "Costume tricotate",
    "maiouri": "Maiouri",
    "plaja": "Plajă",
    "marimi_mari": "Mărimi mari",
    "barbati": "Haine pentru bărbați",
}

# Categoriile pe care le poate alege asistentul. „Haine pe loc în Chișinău" nu se deduce
# din postarea producătorului, deci nu e în listă.
Category = Literal[
    "scurte_trenciuri",
    "costume",
    "costume_sport",
    "pantaloni",
    "pulovere",
    "malete_body",
    "rochite",
    "cardigane",
    "bluzite",
    "paltoane",
    "camasi",
    "costume_clasice",
    "sacouri",
    "fustite_sorti",
    "jalete",
    "pijamale",
    "leghensi",
    "rochii_elegante",
    "salopete",
    "topuri_corsete",
    "costume_tricotate",
    "maiouri",
    "plaja",
    "marimi_mari",
    "barbati",
]

# Ordinea în care se publică produsele dintr-un lot (grupare pe categorii).
CATEGORY_ORDER: tuple[str, ...] = tuple(TOPICS)

_CHEAP, _MEDIUM, _OUTER = (100, 150), (150, 200), (200, 300)
DEFAULT_PROFIT = _MEDIUM

# Intervalul de profit (lei) pe categorie, conform regulilor LC boutique.
PROFIT_RANGES: dict[str, tuple[int, int]] = {
    # produse ieftine: malete, topuri, body-uri, bluze simple
    "malete_body": _CHEAP,
    "topuri_corsete": _CHEAP,
    "maiouri": _CHEAP,
    "bluzite": _CHEAP,
    "leghensi": _CHEAP,
    "plaja": _CHEAP,
    "pijamale": _CHEAP,
    # preț mediu: rochii, cămăși, pantaloni, fuste, compleuri
    "rochite": _MEDIUM,
    "rochii_elegante": _MEDIUM,
    "camasi": _MEDIUM,
    "pantaloni": _MEDIUM,
    "fustite_sorti": _MEDIUM,
    "salopete": _MEDIUM,
    "pulovere": _MEDIUM,
    "marimi_mari": _MEDIUM,
    "barbati": _MEDIUM,
    "haine_pe_loc": _MEDIUM,
    # produse mai consistente: costume, sacouri, cardigane
    "costume": _MEDIUM,
    "costume_sport": _MEDIUM,
    "costume_clasice": _MEDIUM,
    "costume_tricotate": _MEDIUM,
    "sacouri": _MEDIUM,
    "cardigane": _MEDIUM,
    # articole de exterior: veste, geci, scurte, paltoane
    "jalete": _OUTER,
    "scurte_trenciuri": _OUTER,
    "paltoane": _OUTER,
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
