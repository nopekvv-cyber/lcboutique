"""Textul final al postării LC boutique."""

from __future__ import annotations

import html
import re

from .models import Product

ORDER_SECTION = (
    "📩 Pentru comandă, trimiteți mesaj pe Instagram:\n"
    "• poza produsului\n"
    "• codul exact al produsului\n"
    "• mărimea\n"
    "• culoarea\n"
    "• numărul de telefon"
)

# Limita Telegram pentru textul de sub poze.
CAPTION_LIMIT = 1024

_URL = re.compile(r"(https?://\S+|www\.\S+|\b(?:t\.me|instagram\.com|instagr\.am|wa\.me|viber\.me)/\S*)", re.I)
_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{3,}")
# numere de telefon: minim 9 cifre, eventual cu +, spații, cratime, paranteze
_PHONE = re.compile(r"\+?\d[\d\s()\-]{7,}\d")


def _is_phone(match: re.Match[str]) -> bool:
    raw = match.group(0)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith("+"):
        return 9 <= len(digits) <= 13
    if re.fullmatch(r"\d{10,}", raw):
        return True
    # 0XX XXX XX XX sau 380 XX XXX XX XX; măsurători ca „92 96 100 104" rămân
    return 10 <= len(digits) <= 12 and digits.startswith(("0", "380"))


def sanitize(text: str) -> str:
    """Elimină linkuri, conturi @ și numere de telefon care au scăpat în text."""
    text = _URL.sub("", text)
    text = _HANDLE.sub("", text)
    text = _PHONE.sub(lambda m: "" if _is_phone(m) else m.group(0), text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip(" \t,;:-")


def _clean(text: str) -> str:
    return html.escape(sanitize(text))


def format_post(product: Product, price_lei: int) -> str:
    """Postarea în format HTML pentru Telegram."""
    lines = [f"<b>{_clean(product.title)}</b>", ""]

    if product.code.strip():
        lines.append(f"Cod/Model: {html.escape(product.code.strip())}")
    if product.sizes.strip():
        lines.append(f"Mărimi: {html.escape(product.sizes.strip())}")
    material = sanitize(product.material)
    composition = sanitize(product.composition)
    if material and composition and composition.lower() not in material.lower():
        lines.append(f"Material: {html.escape(material)} ({html.escape(composition)})")
    elif material or composition:
        lines.append(f"Material: {html.escape(material or composition)}")
    colors = [sanitize(c) for c in product.colors if sanitize(c)]
    if colors:
        lines.append(f"Culori: {html.escape(', '.join(colors))}")
    lines.append(f"💰 Preț: {price_lei} lei")

    description = _clean(product.description)
    if description:
        lines += ["", description]

    measurements = [_clean(m) for m in product.measurements if sanitize(m)]
    if measurements:
        lines += ["", "📐 Măsurători:"] + [f"• {m}" for m in measurements]

    lines += ["", ORDER_SECTION]
    return "\n".join(lines)


def visible_length(html_text: str) -> int:
    """Lungimea textului așa cum o numără Telegram (fără etichete HTML)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


def fits_caption(html_text: str) -> bool:
    return visible_length(html_text) <= CAPTION_LIMIT
