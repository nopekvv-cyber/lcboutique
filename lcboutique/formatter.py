"""Textul final al postării LC boutique."""

from __future__ import annotations

import html
import re

from .models import Product

ORDER_SECTION = "📩 Comandă pe Instagram: poza, codul, mărimea, culoarea și nr. de telefon"

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


_PRICE_LINE = re.compile(r"(💰\s*Preț:\s*)\d+(?:[ \u00a0]\d{3})*(\s*lei)")


def set_price(html_text: str, price_lei: int) -> str | None:
    """Același text cu prețul schimbat; None dacă textul nu are rândul de preț."""
    if not _PRICE_LINE.search(html_text):
        return None
    return _PRICE_LINE.sub(lambda m: f"{m.group(1)}{price_lei}{m.group(2)}", html_text, count=1)


_FIELD = re.compile(r"^(Cod/Model|Mărimi|Material|Culori|💰\s*Preț)\s*:\s*(.*)$")


def parse_post(html_text: str) -> tuple[dict, int] | None:
    """Câmpurile unei postări publicate de bot (și din formatele mai vechi) + prețul; None dacă nu are preț."""
    text = html.unescape(re.sub(r"<[^>]+>", "", html_text))
    lines = [line.strip() for line in text.splitlines()]
    fields = {"title": "", "code": "", "sizes": "", "material": "", "composition": "", "colors": [], "measurements": []}
    price = None
    in_measurements = False
    for line in lines:
        if not line:
            in_measurements = False
            continue
        if not fields["title"]:
            fields["title"] = line
            continue
        if line.startswith("📐"):
            in_measurements = True
            continue
        if in_measurements and line.startswith("•"):
            fields["measurements"].append(line.lstrip("• ").strip())
            continue
        match = _FIELD.match(line)
        if not match:
            continue
        label, value = match.group(1), match.group(2).strip()
        if label == "Cod/Model":
            fields["code"] = value
        elif label == "Mărimi":
            fields["sizes"] = value
        elif label == "Material":
            inner = re.match(r"^(.*?)\s*\(([^()]*)\)$", value)
            fields["material"], fields["composition"] = (inner.group(1), inner.group(2)) if inner else (value, "")
        elif label == "Culori":
            fields["colors"] = [c.strip() for c in value.split(",") if c.strip()]
        else:
            digits = re.sub(r"\D", "", value.split("lei")[0])
            price = int(digits) if digits else None
    if price is None or not fields["title"]:
        return None
    return fields, price
