"""A stop that later recovers loses to holding the same coins."""

from decimal import Decimal

from desk.models import Position
from desk.report import metrics, render

MINT = "Mint11111111111111111111111111111111"


def test_stopped_book_loses_while_buy_and_hold_recovers() -> None:
    closed = Position(
        mint=MINT,
        symbol="PAPER",
        decimals=6,
        qty_raw=0,
        initial_raw=1_000_000,
        entry_price=Decimal("10"),
        cost_usd=Decimal("20"),
        proceeds_usd=Decimal("8"),
        last_price=Decimal("30"),
        tp1_done=False,
        status="closed",
    )
    rows = metrics(
        Decimal("1000"),
        Decimal("988"),
        [closed],
        [Decimal("1000"), Decimal("988")],
        slippage_bps=0,
        fee_bps=0,
    )
    assert rows["return"] < 0
    assert rows["hold_return"] > 0
    assert rows["win_rate"] == 0
    assert rows["max_drawdown"] == Decimal("12") / Decimal("1000")
    text = render(None, rows)
    assert "승률" in text
    assert "총수익률" in text
    assert "단순 보유" in text
    assert "KST" in text
