from __future__ import annotations

import pytest

from pelando_mcp.models import Condition
from pelando_mcp.normalise import (
    assess_relevance,
    detect_condition,
    looks_like_coupon_code,
    strip_html,
    strip_promo_prefixes,
)


@pytest.mark.parametrize(
    "title,expected_tags",
    [
        ("[ PRIME ] Apple iPhone 16 (512 GB) – Preto", ["PRIME"]),
        ("[REEMBALADO] GeForce RTX 5070 OC 12GB", ["REEMBALADO"]),
        ("(MELI 8667,00) Apple iPhone 16 Pro Max", ["MELI 8667,00"]),
        ("[Moedas R$22] Capa protetora", ["Moedas R$22"]),
        ("[Com Cashback Pelando R$10439.10] iPhone 16 Pro", ["Com Cashback Pelando R$10439.10"]),
        ("Notebook sem prefixo", []),
    ],
)
def test_strip_promo_prefixes(title, expected_tags):
    _clean, tags = strip_promo_prefixes(title)
    assert tags == expected_tags


def test_stacked_prefixes_are_all_stripped():
    clean, tags = strip_promo_prefixes("[PRIME][REEMBALADO] RTX 5070")
    assert tags == ["PRIME", "REEMBALADO"]
    assert clean == "RTX 5070"


def test_a_title_that_is_only_a_prefix_is_not_emptied():
    clean, _ = strip_promo_prefixes("[PRIME]")
    assert clean


@pytest.mark.parametrize(
    "title,expected",
    [
        ("[REEMBALADO] GeForce RTX 5070", Condition.REEMBALADO),
        ("Placa Open Box, 34% Off", Condition.OPEN_BOX),
        ("iPhone 13 recondicionado 128GB", Condition.REFURBISHED),
        ("Notebook usado i5", Condition.USED),
        ("Console seminovo", Condition.USED),
        ("Produto de vitrine", Condition.OPEN_BOX),
    ],
)
def test_detect_condition(title, expected):
    assert detect_condition(title) is expected


def test_condition_defaults_to_unknown_never_new():
    """A silent title is not evidence a unit is new. This is the single most misleading thing the
    server could get wrong."""
    assert detect_condition("Apple iPhone 16 Pro 256GB") is Condition.UNKNOWN


def test_condition_is_accent_insensitive():
    assert detect_condition("SSD RECONDICIONADO 1TB") is Condition.REFURBISHED


@pytest.mark.parametrize(
    "value,expected",
    [
        ("400OFF3D", True),
        ("APPNINJA", True),
        ("10OFF", True),
        ("Resgatar cupom abaixo do produto", False),
        ("", False),
        (None, False),
    ],
)
def test_looks_like_coupon_code(value, expected):
    assert looks_like_coupon_code(value) is expected


def test_strip_html():
    assert strip_html("<p>3.542,55 no pix</p>") == "3.542,55 no pix"
    assert strip_html("plain text") == "plain text"
    assert strip_html(None) is None
    assert strip_html("") is None


def test_relevance_accepts_a_real_match():
    result = assess_relevance("rtx 5070", "GeForce RTX 5070 OC 12GB")
    assert result.is_match


def test_relevance_demotes_an_accessory():
    """`iphone 16 pro` returns cases and cables. They must not be presented as the phone."""
    result = assess_relevance(
        "iphone 16 pro", "Capa protetora transparente para iPhone 16 Pro Max"
    )
    assert not result.is_match
    assert any("accessory" in r for r in result.reasons)


def test_relevance_separates_variants():
    """RTX 5070 and RTX 5070 Ti are different products."""
    result = assess_relevance("rtx 5070", "Placa de Video RTX 5070 Ti 16GB")
    assert not result.is_match


def test_relevance_penalises_capacity_mismatch():
    match = assess_relevance("iphone 16 256gb", "Apple iPhone 16 256GB Preto")
    miss = assess_relevance("iphone 16 256gb", "Apple iPhone 16 512GB Preto")
    assert match.score > miss.score


def test_relevance_ignores_promo_prefix_noise():
    """The bracket prefix must not be scored as part of the product name."""
    assert assess_relevance("rtx 5070", "[REEMBALADO] GeForce RTX 5070 OC 12GB").is_match


def test_relevance_of_empty_query_is_not_a_match():
    assert not assess_relevance("", "anything").is_match
