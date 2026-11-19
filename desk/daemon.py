"""Headless paper loop. A crashed cycle is logged; the process stays up."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path

import websockets

from desk.engine import evaluate_buy, evaluate_exit
from desk.journal import Journal, utc_now
from desk.models import BookView, Candidate
from desk.parse import parse_pumpportal
from desk.pricing import USDC_MINT, fill_price, proceeds_from_quote, usdc_raw
from desk.report import metrics, render
from desk.runtime import journal_path, load_env_file, market_for
from pumpgrok.config.settings import ConfigError, load_settings

log = logging.getLogger("desk")
_NEW_PER_CYCLE = 3
_PUMP_URL = "wss://pumpportal.fun/api/data"


def main(argv: list[str] | None = None) -> int:
    load_env_file(Path.cwd() / ".env")
    args = _parser().parse_args(argv)
    try:
        cfg = load_settings().desk
    except ConfigError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    if args.command == "report":
        return _report(cfg)
    if args.fixture:
        cfg = cfg.model_copy(update={"fixture": args.fixture})
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    once = bool(args.once or cfg.fixture.strip())
    return asyncio.run(run(cfg, once=once))


async def run(cfg: object, once: bool) -> int:
    journal = Journal(journal_path(cfg))  # type: ignore[arg-type]
    try:
        journal.ensure_account(cfg.starting_usd)  # type: ignore[attr-defined]
        market = market_for(cfg)  # type: ignore[arg-type]
        if cfg.fixture.strip():  # type: ignore[attr-defined]
            await cycle(market, journal, cfg, await market.discovery())
            return 0
        stop = asyncio.Event()
        queue: asyncio.Queue[Candidate] = asyncio.Queue(maxsize=100)
        pump = asyncio.create_task(_pump(queue, stop))
        try:
            while True:
                await _safe_cycle(market, journal, cfg, queue)
                if once:
                    return 0
                await asyncio.sleep(cfg.poll_seconds)  # type: ignore[attr-defined]
        finally:
            stop.set()
            pump.cancel()
    finally:
        journal.close()


async def cycle(market: object, journal: Journal, cfg: object, candidates: list[Candidate]) -> None:
    acted = 0
    for candidate in candidates:
        if acted >= _NEW_PER_CYCLE:
            break
        if candidate.mint in journal.final_mints() or candidate.mint in journal.open_mints():
            continue
        amount = usdc_raw(cfg.order_usd)  # type: ignore[attr-defined]
        reads = await market.load_buy(candidate.mint, USDC_MINT, amount, cfg.slippage_bps)  # type: ignore[attr-defined]
        plan = evaluate_buy(
            candidate,
            reads,
            time.time(),
            _book(journal),
            cfg,  # type: ignore[arg-type]
            input_mint=USDC_MINT,
            expected_in=amount,
        )
        if plan.accepted:
            journal.apply_buy(candidate.mint, plan, candidate.source)
            log.info("paper buy %s %s", candidate.symbol or candidate.mint, plan.price)
        else:
            journal.record_steps(candidate.mint, plan.steps, candidate.source)
            log.info("reject %s %s", candidate.mint, plan.steps[-1].reason)
        acted += 1
    for position in list(journal.open_positions()):
        quote = await market.load_sell(  # type: ignore[attr-defined]
            position.mint, position.qty_raw, cfg.slippage_bps, USDC_MINT  # type: ignore[attr-defined]
        )
        if quote.ok:
            _remember_mark(
                journal,
                position.mint,
                position.qty_raw,
                position.decimals,
                quote.payload,
                cfg=cfg,
            )
        exit_plan = evaluate_exit(position, quote, time.time(), cfg)  # type: ignore[arg-type]
        for leg in exit_plan.legs:
            journal.apply_exit(position.mint, leg)
            log.info("paper exit %s %s", position.mint, leg.reason)
    await _mark_closed(market, journal, cfg)
    current = metrics(
        journal.starting(),
        journal.cash(),
        journal.all_positions(),
        journal.equity_points(),
        slippage_bps=cfg.slippage_bps,  # type: ignore[attr-defined]
        fee_bps=cfg.fee_bps,  # type: ignore[attr-defined]
    )
    journal.snapshot(Decimal(str(current["equity"])))
    health = journal.path.with_suffix(".health")
    health.write_text(utc_now() + "\n", encoding="utf-8")
    log.info("cycle cash=%s equity=%s", journal.cash(), current["equity"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk", description="Solana memecoin paper desk")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="run the paper loop")
    run_cmd.add_argument("--once", action="store_true", help="one cycle, then exit")
    run_cmd.add_argument("--fixture", default="", help="directory of canned reads")
    commands.add_parser("report", help="print performance")
    return parser


def _report(cfg: object) -> int:
    path = journal_path(cfg)  # type: ignore[arg-type]
    if not path.is_file():
        sys.stdout.write("아직 기록이 없습니다.\n")
        return 0
    journal = Journal(path)
    try:
        if not journal.has_account():
            sys.stdout.write("아직 기록이 없습니다.\n")
            return 0
        rows = metrics(
            journal.starting(),
            journal.cash(),
            journal.all_positions(),
            journal.equity_points(),
            slippage_bps=cfg.slippage_bps,  # type: ignore[attr-defined]
            fee_bps=cfg.fee_bps,  # type: ignore[attr-defined]
        )
    finally:
        journal.close()
    sys.stdout.write(render(None, rows))
    return 0


async def _safe_cycle(
    market: object,
    journal: Journal,
    cfg: object,
    queue: asyncio.Queue[Candidate],
) -> None:
    try:
        found = await _discover(market, queue, journal)
        await cycle(market, journal, cfg, found)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        raise
    except BaseException:
        log.exception("cycle failed")
        journal.note("JOURNAL", "사이클 오류로 이번 회차를 건너뜀")


async def _discover(
    market: object, queue: asyncio.Queue[Candidate], journal: Journal
) -> list[Candidate]:
    found: list[Candidate] = []
    while True:
        try:
            found.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    try:
        found.extend(await market.profiles())  # type: ignore[attr-defined]
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        log.warning("dexscreener profile read failed")
    for mint in journal.retry_mints():
        found.append(Candidate(mint, "", "dexscreener"))
    unique: list[Candidate] = []
    seen: set[str] = set()
    for item in found:
        if item.mint in seen:
            continue
        seen.add(item.mint)
        unique.append(item)
    return unique


async def _pump(queue: asyncio.Queue[Candidate], stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with websockets.connect(_PUMP_URL, open_timeout=10, close_timeout=5) as socket:
                await socket.send(json.dumps({"method": "subscribeNewToken"}))
                async for raw in socket:
                    if stop.is_set():
                        return
                    candidate = parse_pumpportal(_json(raw))
                    if candidate is None:
                        continue
                    try:
                        queue.put_nowait(candidate)
                    except asyncio.QueueFull:
                        log.info("scout queue full")
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except BaseException:
            log.warning("pumpportal disconnected")
            await asyncio.sleep(5)


def _book(journal: Journal) -> BookView:
    return BookView(journal.cash(), journal.open_mints(), journal.final_mints())


async def _mark_closed(market: object, journal: Journal, cfg: object) -> None:
    """Keep a mark on closed coins so buy-and-hold can use a later price."""
    for position in journal.all_positions():
        if position.status != "closed" or position.initial_raw <= 0:
            continue
        quote = await market.load_sell(  # type: ignore[attr-defined]
            position.mint,
            position.initial_raw,
            cfg.slippage_bps,  # type: ignore[attr-defined]
            USDC_MINT,
        )
        if quote.ok:
            _remember_mark(
                journal,
                position.mint,
                position.initial_raw,
                position.decimals,
                quote.payload,
                cfg=cfg,
            )


def _remember_mark(
    journal: Journal,
    mint: str,
    qty: int,
    decimals: int,
    payload: dict[str, object],
    *,
    cfg: object,
) -> None:
    try:
        proceeds = proceeds_from_quote(payload, cfg.fee_bps)  # type: ignore[attr-defined]
        journal.mark(mint, fill_price(proceeds, qty, decimals))
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return


def _json(raw: object) -> object:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
