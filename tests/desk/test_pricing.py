"""Fill math: fees, the 2% pool cap, and the 10 second freshness window."""

from decimal import Decimal

from desk.pricing import USDC_MINT, fill_price, is_fresh, tokens_from_quote, usdc_raw, within_pool


def test_usdc_order_is_twenty_dollars() -> None:
    assert usdc_raw(Decimal("20")) == 20_000_000


def test_token_fill_keeps_the_quote_minimum_and_the_fee() -> None:
    quote = {"minOut": 990_000, "platformFeeOut": 0, "outputMint": "mint"}
    assert tokens_from_quote(quote, 30) == 987_030


def test_pool_cap_allows_exactly_two_percent() -> None:
    assert within_pool(Decimal("20"), Decimal("1000"), 200) is True
    assert within_pool(Decimal("20.01"), Decimal("1000"), 200) is False


def test_freshness_rejects_future_and_older_than_ten_seconds() -> None:
    assert is_fresh(1_000.0, 1_010.0, 10) is True
    assert is_fresh(1_000.0, 1_010.1, 10) is False
    assert is_fresh(1_011.0, 1_010.0, 10) is False


def test_fill_price_is_usd_per_whole_token() -> None:
    assert fill_price(Decimal("20"), 1_000_000, 6) == Decimal("20")
    assert USDC_MINT
