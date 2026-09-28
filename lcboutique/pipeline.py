"""De la postarea producătorului la postările gata de publicat (fără Telegram)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import PricingConfig
from .formatter import format_post
from .models import CATEGORY_ORDER, Analysis, SupplierPrice
from .pricing import PricingError, calculate_price, find_drop_price


@dataclass(frozen=True)
class Media:
    kind: str  # "photo" sau "video"
    file_id: str  # cea mai mare rezoluție, pentru publicare
    preview_id: str = ""  # o variantă mai mică a pozei, pentru analiză (mai ieftin)


@dataclass
class ReadyPost:
    category: str
    text: str  # HTML
    media: list[Media]
    summary: str  # pentru confirmarea din grupul sursă (conține calculul)
    source_chat_id: int = 0
    source_message_id: int = 0

    @property
    def sort_key(self) -> int:
        return CATEGORY_ORDER.index(self.category) if self.category in CATEGORY_ORDER else len(CATEGORY_ORDER)


@dataclass
class BuildResult:
    posts: list[ReadyPost] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def build_posts(
    analysis: Analysis, photos: list[Media], videos: list[Media], cfg: PricingConfig, source_text: str = ""
) -> BuildResult:
    """Transformă analiza lui Claude în postări; produsele fără preț sunt raportate, nu publicate."""
    result = BuildResult()
    products = analysis.products
    if not products:
        result.problems.append("nu am identificat niciun produs în postare")
    single = len(products) == 1

    # un singur produs: prețul drop scris clar în text are prioritate (verificare fără AI)
    drop = find_drop_price(source_text) if single else None
    if drop:
        amount, currency = drop
        products[0].prices = [SupplierPrice(kind="drop", amount=amount, currency=currency)]

    for product in products:
        label = product.title or product.category
        if product.code.strip():
            label += f" (cod {product.code})"
        try:
            price = calculate_price(product.prices, product.category, product.profit_lei, cfg)
        except PricingError as exc:
            result.problems.append(f"{label}: {exc} — nu am publicat")
            continue

        if single:
            media = photos + videos
        else:
            indices = [i for i in dict.fromkeys(product.photo_indices) if 0 <= i < len(photos)]
            media = [photos[i] for i in indices]

        result.posts.append(
            ReadyPost(
                category=product.category,
                text=format_post(product, price.final_lei),
                media=media,
                summary=f"{product.title} — cod {product.code or '—'} — {price.final_lei} lei\n{price.explain(cfg)}",
            )
        )
    return result
