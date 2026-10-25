"""Aducerea în mini-aplicație a postărilor vechi ale botului (publicate înainte de baza de date)."""

from __future__ import annotations

from .formatter import parse_post
from .storage import Post, new_post
from .topics import normalize

# cuvinte din denumire → topic (pentru postările la care nu știm topicul)
_KEYWORDS: list[tuple[str, str]] = [
    ("costum sport", "costume_sport"),
    ("trening", "costume_sport"),
    ("costum tricot", "costume_tricotate"),
    ("costum clasic", "costume_clasice"),
    ("costum de baie", "plaja"),
    ("rochie de seara", "rochii_elegante"),
    ("rochie eleganta", "rochii_elegante"),
    ("rochi", "rochite"),
    ("costum", "costume"),
    ("compleu", "costume"),
    ("geaca", "scurte_trenciuri"),
    ("scurta", "scurte_trenciuri"),
    ("trenci", "scurte_trenciuri"),
    ("puf", "scurte_trenciuri"),
    ("palton", "paltoane"),
    ("vesta", "jalete"),
    ("jilet", "jalete"),
    ("pantalon", "pantaloni"),
    ("blugi", "pantaloni"),
    ("pulover", "pulovere"),
    ("hanorac", "pulovere"),
    ("maleta", "malete_body"),
    ("body", "malete_body"),
    ("cardigan", "cardigane"),
    ("bluz", "bluzite"),
    ("camas", "camasi"),
    ("sacou", "sacouri"),
    ("fust", "fustite_sorti"),
    ("sort", "fustite_sorti"),
    ("pijama", "pijamale"),
    ("legh", "leghensi"),
    ("salopet", "salopete"),
    ("corset", "topuri_corsete"),
    ("top", "topuri_corsete"),
    ("maiou", "maiouri"),
]


def guess_category(title: str) -> str:
    norm = normalize(title)
    for word, key in _KEYWORDS:
        if word in norm:
            return key
    return ""


def legacy_post(
    html_text: str,
    is_caption: bool,
    message_id: int,
    category: str = "",
    media_group_id: str | None = None,
    photo: dict | None = None,
) -> Post | None:
    """Înregistrarea pentru o postare veche; None dacă textul nu e o postare de produs."""
    parsed = parse_post(html_text)
    if parsed is None:
        return None
    fields, price = parsed
    category = category or guess_category(fields["title"])
    product = {
        # Product cere o categorie validă; pentru topic necunoscut folosim una neutră doar la text
        "category": category if category and category != "haine_pe_loc" else "rochite",
        "description": "",
        "details": [],
        "prices": [],
        "profit_lei": 0,
        "photo_indices": [],
        **fields,
    }
    return new_post(
        category=category,
        product=product,
        price_lei=price,
        text_message_id=message_id,
        is_caption=is_caption,
        message_ids=[message_id],
        media_group_ids=[media_group_id] if media_group_id else [],
        media=[photo] if photo else [],
        thread_id=None,
        extra={"legacy": True},
    )
