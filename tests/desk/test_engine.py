"""Pass/fail gates and paper fills. A gap through the stop records the quote."""

import json
from decimal import Decimal
from pathlib import Path

from desk.engine import evaluate_buy, evaluate_exit
from desk.journal import Journal
from desk.models import BookView, Candidate, Position, Read
from desk.pricing import USDC_MINT

from pumpgrok.config.sections.desk import DeskSettings

ROOT = Path(__file__).resolve().parent / "fixtures" / "happy"
MINT = "Mint11111111111111111111111111111111"
NOW = 1_700_000_000.0


def _reads(now: float = NOW) -> dict[str, Read]:
    loaded: dict[str, Read] = {}
    for name in ("rug", "rpc", "pool", "quote"):
        payload = json.loads((ROOT / f"{name}.json").read_text(encoding="utf-8"))
        loaded[name] = Read(name, True, now, payload)
    return loaded


def _buy(reads: dict[str, Read], cfg: DeskSettings | None = None) -> object:
    book = BookView(Decimal("1000"), frozenset(), frozenset())
    return evaluate_buy(
        Candidate(MINT, "PAPER", "dexscreener"),
        reads,
        NOW,
        book,
        cfg or DeskSettings(),
        input_mint=USDC_MINT,
        expected_in=20_000_000,
    )


def test_happy_path_buys_at_the_quoted_fill() -> None:
    plan = _buy(_reads())
    assert plan.accepted is True
    assert plan.tokens_raw == 987_030
    assert plan.cost_usd == Decimal("20")
    assert plan.steps[-1].seat == "SNIPER"


def test_stale_quote_forbids_the_buy() -> None:
    reads = _reads()
    reads["quote"] = Read("quote", True, NOW - 10.1, reads["quote"].payload)
    plan = _buy(reads)
    assert plan.accepted is False
    assert "오래" in plan.steps[-1].reason


def test_quote_aged_exactly_ten_seconds_still_passes() -> None:
    reads = _reads(NOW - 10)
    plan = _buy(reads)
    assert plan.accepted is True


def test_order_above_two_percent_of_the_pool_is_rejected() -> None:
    reads = _reads()
    reads["pool"] = Read("pool", True, NOW, {**reads["pool"].payload, "liquidityUsd": "999"})
    plan = _buy(reads)
    assert plan.accepted is False
    assert "2%" in plan.steps[-1].reason


def test_exact_two_percent_of_the_pool_is_allowed() -> None:
    reads = _reads()
    reads["pool"] = Read("pool", True, NOW, {**reads["pool"].payload, "liquidityUsd": "1000"})
    assert _buy(reads).accepted is True


def test_a_second_holder_above_the_whale_cap_fails() -> None:
    reads = _reads()
    holders = [
        {"address": "Pool1111111111111111111111111111111111", "amount": "400000000000000"},
        {"address": "Whale111111111111111111111111111111111", "amount": "200000000000000"},
    ]
    reads["rpc"] = Read("rpc", True, NOW, {**reads["rpc"].payload, "holders": holders})
    plan = _buy(reads)
    assert plan.accepted is False
    assert "고래" in plan.steps[-1].reason


def test_stop_gap_records_the_quote_not_the_stop_line() -> None:
    position = Position(
        MINT, "PAPER", 6, 2_000_000, 2_000_000,
        Decimal("10"), Decimal("20"), Decimal("0"), Decimal("10"), False, "open",
    )
    quote = Read(
        "quote",
        True,
        NOW,
        {
            "inputMint": MINT,
            "outputMint": USDC_MINT,
            "inAmount": "2000000",
            "outAmount": "8000000",
            "minOut": "8000000",
            "platformFeeOut": 0,
            "swapUsd": "8",
        },
    )
    plan = evaluate_exit(position, quote, NOW, DeskSettings(fee_bps=0, slippage_bps=0))
    assert len(plan.legs) == 1
    leg = plan.legs[0]
    assert leg.kind == "stop"
    assert leg.price == Decimal("4")
    assert leg.price < Decimal("7.5")
    assert "손절" in leg.reason


def test_both_take_profits_sell_half_then_the_rest_at_the_quote() -> None:
    position = Position(
        MINT, "PAPER", 6, 2_000_000, 2_000_000,
        Decimal("10"), Decimal("20"), Decimal("0"), Decimal("10"), False, "open",
    )
    quote = Read(
        "quote",
        True,
        NOW,
        {
            "inputMint": MINT,
            "outputMint": USDC_MINT,
            "minOut": "40000000",
            "platformFeeOut": 0,
            "outAmount": "40000000",
            "swapUsd": "40",
        },
    )
    plan = evaluate_exit(position, quote, NOW, DeskSettings(fee_bps=0))
    assert [leg.kind for leg in plan.legs] == ["tp50", "tp100"]
    assert sum(leg.qty_raw for leg in plan.legs) == 2_000_000
    assert sum((leg.proceeds for leg in plan.legs), Decimal(0)) == Decimal("40")


def test_buy_and_stop_are_stored_with_their_reasons(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "desk.sqlite")
    try:
        journal.ensure_account(Decimal("1000"))
        plan = _buy(_reads())
        journal.apply_buy(MINT, plan, "dexscreener")
        position = journal.open_positions()[0]
        quote = Read(
            "quote",
            True,
            NOW,
            {
                "inputMint": MINT,
                "outputMint": USDC_MINT,
                "minOut": "1000000",
                "platformFeeOut": 0,
                "outAmount": "1000000",
                "swapUsd": "1",
            },
        )
        exit_plan = evaluate_exit(position, quote, NOW, DeskSettings(fee_bps=0))
        journal.apply_exit(MINT, exit_plan.legs[0])
        reasons = [
            row["reason"]
            for row in journal._db.execute("SELECT reason FROM events").fetchall()
        ]
    finally:
        journal.close()
    assert any("모의 매수" in reason for reason in reasons)
    assert any("손절" in reason for reason in reasons)
    assert journal.path.is_file()
