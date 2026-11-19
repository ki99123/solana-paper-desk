"""Complete `pumpgrok paper buy|sell` with a simulated fill, or refuse closed."""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
from typing import Any

from desk.engine import evaluate_buy, manual_sell
from desk.journal import Journal
from desk.models import BookView, Candidate, Step
from desk.pricing import SOL_MINT
from desk.runtime import journal_path, market_for
from pumpgrok.cli.codes import _TRADE_UNAVAILABLE
from pumpgrok.cli.errors import _say, _tool_error
from pumpgrok.config.sections.desk import DeskSettings
from pumpgrok.core.units import sol_to_lamports


async def run_paper_command(args: Any, verb: str, cfg: DeskSettings) -> int:
    """Paper-fill one CLI order. A failed check writes the reason and refuses."""
    journal = Journal(journal_path(cfg))
    try:
        journal.ensure_account(cfg.starting_usd)
        if args.cmd == "buy":
            return await _buy(args, verb, cfg, journal)
        if args.cmd == "sell":
            return await _sell(args, verb, cfg, journal)
        return _tool_error(verb, "unavailable", _TRADE_UNAVAILABLE)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return _tool_error(verb, "unavailable", _TRADE_UNAVAILABLE)
    finally:
        journal.close()


async def _buy(args: Any, verb: str, cfg: DeskSettings, journal: Journal) -> int:
    amount = sol_to_lamports(args.size_sol)
    market = market_for(cfg)
    reads = await market.load_buy(args.mint, SOL_MINT, amount, int(args.slippage_bps))
    plan = evaluate_buy(
        Candidate(args.mint, "", "cli"),
        reads,
        time.time(),
        _book(journal),
        cfg,
        input_mint=SOL_MINT,
        expected_in=amount,
    )
    if not plan.accepted:
        journal.record_steps(args.mint, plan.steps, "cli")
        return _tool_error(verb, "unavailable", _TRADE_UNAVAILABLE)
    journal.apply_buy(args.mint, plan, "cli")
    body = _fill_body("buy", args.mint, plan.price, plan.cost_usd, plan.tokens_raw)
    _say(sys.stdout, json.dumps(body))
    return 0


async def _sell(args: Any, verb: str, cfg: DeskSettings, journal: Journal) -> int:
    position = next((item for item in journal.open_positions() if item.mint == args.mint), None)
    if position is None:
        journal.record_steps(
            args.mint,
            (Step("EXIT", "reject", "매도 거절: 열린 모의 포지션이 없음"),),
            "cli",
        )
        return _tool_error(verb, "unavailable", _TRADE_UNAVAILABLE)
    market = market_for(cfg)
    now = time.time()
    quote = await market.load_sell(args.mint, position.qty_raw, int(args.slippage_bps), SOL_MINT)
    pool = await market.pool(args.mint)
    plan = manual_sell(
        position,
        quote,
        pool,
        now,
        cfg,
        size_sol=Decimal(args.size_sol),
        size_lamports=sol_to_lamports(args.size_sol),
    )
    if not plan.legs:
        reason = plan.defer_reason or "매도 거절: 청산 견적 없음"
        journal.record_steps(args.mint, (Step("EXIT", "reject", reason),), "cli")
        return _tool_error(verb, "unavailable", _TRADE_UNAVAILABLE)
    chosen = plan.legs[0]
    journal.apply_exit(args.mint, chosen)
    _say(
        sys.stdout,
        json.dumps(_fill_body("sell", args.mint, chosen.price, chosen.proceeds, chosen.qty_raw)),
    )
    return 0


def _book(journal: Journal) -> BookView:
    return BookView(journal.cash(), journal.open_mints(), journal.final_mints())


def _fill_body(
    side: str, mint: str, price: Decimal, notional: Decimal, raw: int
) -> dict[str, object]:
    return {
        "ok": True,
        "paper": True,
        "side": side,
        "mint": mint,
        "fill_price_usd": format(price, "f"),
        "notional_usd": format(notional, "f"),
        "tokens_raw": str(raw),
        "reason": "모의 체결",
    }

