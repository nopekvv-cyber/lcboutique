import pytest

from lcboutique.config import PricingConfig
from lcboutique.formatter import ORDER_SECTION, fits_caption, format_post, sanitize
from lcboutique.models import Analysis, Product, SupplierPrice
from lcboutique.pipeline import Media, build_posts
from lcboutique.pricing import PricingError, calculate_price, round_nice

CFG = PricingConfig()


def price(amount, kind="unspecified", currency="UAH"):
    return SupplierPrice(kind=kind, amount=amount, currency=currency)


def product(**kw):
    base = dict(
        category="rochite",
        title="Rochie midi din tricot",
        code="R-1452",
        sizes="42-44, 46-48",
        material="tricot",
        composition="95% bumbac, 5% elastan",
        colors=["negru", "bej"],
        description="O rochie comodă și elegantă pentru fiecare zi.",
        details=["croială dreaptă", "lungime midi"],
        measurements=[],
        prices=[price(700)],
        profit_lei=180,
        photo_indices=[0, 1],
    )
    base.update(kw)
    return Product(**base)


# ---------- preț ----------

def test_formula_and_rounding():
    # 700 × 0.5 + 100 = 450; + 180 = 630 → cel mai apropiat preț frumos în [600, 650] este 650
    r = calculate_price([price(700)], "rochite", 180, CFG)
    assert r.base_lei == 450
    assert r.final_lei == 650
    assert 150 <= r.profit_lei <= 200


@pytest.mark.parametrize(
    "uah,category,profit",
    [(300, "topuri_corsete", 120), (850, "rochite", 150), (1450, "costume_sport", 200), (2600, "scurte_trenciuri", 250), (999, "pantaloni", 170)],
)
def test_price_is_nice_and_profit_in_range(uah, category, profit):
    from lcboutique.models import PROFIT_RANGES

    r = calculate_price([price(uah)], category, profit, CFG)
    lo, hi = PROFIT_RANGES[category]
    assert r.final_lei % 100 in CFG.nice_endings
    assert lo <= r.final_lei - r.base_lei <= hi


def test_profit_is_clamped_to_category():
    r = calculate_price([price(400)], "malete_body", 500, CFG)  # 300 + profit max 150
    assert r.final_lei <= 450


def test_price_priority_prefers_drop():
    r = calculate_price([price(600, "opt"), price(700, "drop"), price(1200, "retail")], "rochite", 150, CFG)
    assert r.supplier_amount == 700


def test_usd_price_uses_20_lei_per_dollar():
    # 20 $ × 20 + 100 = 500; profit rochie 150–200 → 650–700
    r = calculate_price([price(20, "drop", "USD")], "rochite", 150, CFG)
    assert r.base_lei == 500
    assert 650 <= r.final_lei <= 700
    assert "$" in r.explain(CFG)


def test_drop_wins_across_currencies():
    r = calculate_price([price(600, "opt"), price(15, "drop", "USD")], "rochite", 150, CFG)
    assert (r.supplier_amount, r.supplier_currency) == (15, "USD")


def test_missing_or_unknown_currency_price():
    with pytest.raises(PricingError):
        calculate_price([], "rochite", 150, CFG)
    with pytest.raises(PricingError):
        calculate_price([price(20, currency="EUR")], "rochite", 150, CFG)


def test_round_nice_never_below_minimum():
    assert round_nice(510, 505, 555, (50, 90)) == 550
    assert round_nice(600, 591, 600, (50, 90)) == 650  # niciun preț frumos în interval → următorul peste minim


# ---------- text ----------

def test_post_format_order_and_order_section():
    text = format_post(product(), 650)
    lines = text.splitlines()
    assert lines[0] == "<b>Rochie midi din tricot</b>"
    body = "\n".join(lines)
    assert body.index("Cod/Model: R-1452") < body.index("Mărimi:") < body.index("Material:")
    assert body.index("Material: tricot (95% bumbac, 5% elastan)") < body.index("Culori: negru, bej")
    assert body.index("Culori:") < body.index("💰 Preț: 650 lei") < body.index("O rochie comodă")
    assert text.endswith(ORDER_SECTION)
    assert "грн" not in text and "700" not in text
    assert fits_caption(text)


def test_details_section_is_not_in_post():
    assert "Detalii" not in format_post(product(), 650)
    assert "croială dreaptă" not in format_post(product(), 650)


def test_empty_fields_are_omitted():
    text = format_post(product(sizes="", material="", composition="", colors=[], details=[]), 590)
    assert "Mărimi" not in text and "Material" not in text and "Culori" not in text and "Detalii" not in text


def test_sanitize_removes_supplier_contacts_but_keeps_measurements():
    dirty = "Scrieți @supplier_ua sau +38 (050) 123-45-67, 0671234567, https://t.me/x. Bust 92 96 100 104"
    clean = sanitize(dirty)
    for bad in ("@supplier", "050", "0671234567", "t.me"):
        assert bad not in clean
    assert "92 96 100 104" in clean


def test_html_is_escaped():
    text = format_post(product(title="Rochie <nouă> & elegantă"), 650)
    assert "&lt;nouă&gt; &amp;" in text


# ---------- produse multiple ----------

def test_multiple_products_are_kept_separate():
    photos = [Media("photo", f"p{i}") for i in range(4)]
    analysis = Analysis(
        products=[
            product(code="A1", photo_indices=[0, 1]),
            product(code="B2", category="bluzite", prices=[price(400)], profit_lei=120, photo_indices=[2, 3]),
            product(code="", photo_indices=[]),
            product(code="C3", prices=[]),
        ],
        notes="materialul nu este specificat",
    )
    res = build_posts(analysis, photos, [], CFG)
    assert [p.media for p in res.posts] == [photos[:2], photos[2:]]
    assert "Cod/Model: A1" in res.posts[0].text and "B2" not in res.posts[0].text
    assert "Cod/Model: B2" in res.posts[1].text
    assert len(res.problems) == 2  # fără cod + fără preț → raportate, nepublicate


def test_single_product_gets_all_media_and_sort_key():
    photos = [Media("photo", "p0")]
    videos = [Media("video", "v0")]
    res = build_posts(Analysis(products=[product(photo_indices=[])], notes=""), photos, videos, CFG)
    assert res.posts[0].media == photos + videos
    geaca = build_posts(Analysis(products=[product(category="scurte_trenciuri")], notes=""), photos, [], CFG).posts[0]
    assert geaca.sort_key < res.posts[0].sort_key  # ordinea topicurilor din grup
