"""Turn fresh reads into a buy or an exit. Nothing here touches the network."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from desk.gates import _ready, risk_gate, rug_gate, scout_gate, whale_gate
from desk.models import BookView, BuyPlan, Candidate, ExitLeg, ExitPlan, Position, Read, Step
from desk.pricing import (
    SOL_MINT,
    USDC_MINT,
    bps_factor,
    buy_cost_usd,
    fill_price,
    portion,
    proceeds_from_quote,
    tokens_from_quote,
    within_pool,
)
from pumpgrok.config.sections.desk import DeskSettings


def evaluate_buy(
    candidate: Candidate,
    reads: dict[str, Read],
    now: float,
    book: BookView,
    cfg: DeskSettings,
    *,
    input_mint: str,
    expected_in: int,
) -> BuyPlan:
    """SCOUT, RUG, WHALE, then RISK veto, then a paper fill."""
    steps: list[Step] = []
    blocked = _screen(
        candidate, reads, now, book, cfg, steps=steps, input_mint=input_mint
    )
    if blocked is not None:
        return blocked
    return _fill(
        candidate,
        reads,
        book,
        cfg,
        steps,
        input_mint=input_mint,
        expected_in=expected_in,
    )


def manual_sell(
    position: Position,
    quote: Read | None,
    pool: Read | None,
    now: float,
    cfg: DeskSettings,
    *,
    size_sol: Decimal,
    size_lamports: int,
) -> ExitPlan:
    """Operator paper sell. The size is a SOL notional, filled from the quote."""
    problem = _sell_ready(position, quote, pool, now, cfg)
    if problem or quote is None or pool is None:
        return ExitPlan((), problem or "매도 거절: 풀 유동성 확인 실패")
    return _sized_sell(
        position,
        quote.payload,
        pool.payload,
        cfg,
        size_sol=size_sol,
        size_lamports=size_lamports,
    )


def _screen(
    candidate: Candidate,
    reads: dict[str, Read],
    now: float,
    book: BookView,
    cfg: DeskSettings,
    *,
    steps: list[Step],
    input_mint: str,
) -> BuyPlan | None:
    ok, reason = scout_gate(candidate)
    if not ok:
        return _reject(steps, "SCOUT", reason)
    steps.append(Step("SCOUT", "gate_pass", reason, _scout_detail(candidate)))
    ok, reason = rug_gate(reads.get("rug"), reads.get("rpc"), now, cfg.stale_seconds)
    if not ok:
        return _reject(steps, "RUG", reason, _authority_detail(reads.get("rug")))
    steps.append(Step("RUG", "gate_pass", reason))
    ok, reason = whale_gate(reads.get("rpc"), now, cfg.stale_seconds, cfg.whale_max_pct)
    if not ok:
        return _reject(steps, "WHALE", reason)
    steps.append(Step("WHALE", "gate_pass", reason, _whale_detail(reads.get("rpc"))))
    if input_mint == USDC_MINT:
        order_usd: Decimal | None = cfg.order_usd
    else:
        order_usd = _preview_cost(reads.get("quote"))
    if order_usd is None:
        return _reject(steps, "RISK", "매수 금지: Jupiter 견적 확인 실패")
    ok, reason = risk_gate(
        reads,
        now,
        cfg.stale_seconds,
        order_usd=order_usd,
        cap_bps=cfg.pool_cap_bps,
        book=book,
        mint=candidate.mint,
    )
    if not ok:
        return _reject(steps, "RISK", reason)
    steps.append(Step("RISK", "gate_pass", reason))
    return None


def _fill(
    candidate: Candidate,
    reads: dict[str, Read],
    book: BookView,
    cfg: DeskSettings,
    steps: list[Step],
    *,
    input_mint: str,
    expected_in: int,
) -> BuyPlan:
    quote = reads["quote"].payload
    mismatch = _quote_mismatch(quote, candidate.mint, input_mint, expected_in)
    if mismatch:
        return _reject(steps, "SNIPER", mismatch)
    try:
        tokens = tokens_from_quote(quote, cfg.fee_bps)
        cost = buy_cost_usd(quote)
        decimals = int(reads["rpc"].payload["decimals"])  # type: ignore[arg-type]
        price = fill_price(cost, tokens, decimals)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return _reject(steps, "SNIPER", "확인 실패: 견적으로 체결가를 계산하지 못함")
    if cost > book.cash_usd:
        return _reject(steps, "RISK", "매수 금지: 가상 잔고 부족")
    symbol = candidate.symbol or str(reads["pool"].payload.get("symbol") or "")
    detail = {
        "fill_price_usd": format(price, "f"),
        "cost_usd": format(cost, "f"),
        "tokens_raw": str(tokens),
        "decimals": decimals,
        "fee_bps": cfg.fee_bps,
        "min_out": str(quote.get("minOut")),
    }
    reason = "모의 매수 체결: 견적의 슬리피지와 수수료를 반영한 가격"
    steps.append(Step("SNIPER", "buy", reason, detail))
    return BuyPlan(tuple(steps), True, tokens, cost, price, decimals, symbol)


def _sell_ready(
    position: Position,
    quote: Read | None,
    pool: Read | None,
    now: float,
    cfg: DeskSettings,
) -> str | None:
    problem = _ready(quote, "Jupiter 견적", now, cfg.stale_seconds)
    if problem:
        return problem.replace("매수 금지", "매도 거절")
    pool_problem = _ready(pool, "풀 유동성", now, cfg.stale_seconds)
    if pool_problem or quote is None or pool is None:
        text = pool_problem or "매도 거절: 풀 유동성 확인 실패"
        return text.replace("매수 금지", "매도 거절")
    return _sell_mints(position, quote.payload)


def _sell_mints(position: Position, payload: dict[str, object]) -> str | None:
    if str(payload.get("inputMint") or "") != position.mint:
        return "매도 거절: 견적 민트가 포지션과 다름"
    if str(payload.get("outputMint") or "") != SOL_MINT:
        return "매도 거절: 견적 출력 자산이 SOL이 아님"
    return None


def _sized_sell(
    position: Position,
    quote: dict[str, object],
    pool: dict[str, object],
    cfg: DeskSettings,
    *,
    size_sol: Decimal,
    size_lamports: int,
) -> ExitPlan:
    try:
        proceeds, price, liquidity, net_sol = _sell_numbers(position, quote, pool, cfg)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return ExitPlan((), "확인 실패: 매도 견적을 계산하지 못함")
    qty = _sell_qty(position, net_sol, size_lamports)
    problem = _qty_problem(proceeds, liquidity, net_sol, qty, size_sol, cfg=cfg)
    if problem:
        return ExitPlan((), problem)
    slice_usd = portion(proceeds, qty, position.qty_raw)
    reason = "수동 모의 매도: 견적의 슬리피지와 수수료를 반영한 가격"
    return ExitPlan((ExitLeg("manual", qty, price, slice_usd, reason),))


def _sell_numbers(
    position: Position,
    quote: dict[str, object],
    pool: dict[str, object],
    cfg: DeskSettings,
) -> tuple[Decimal, Decimal, Decimal, int]:
    proceeds = proceeds_from_quote(quote, cfg.fee_bps)
    price = fill_price(proceeds, position.qty_raw, position.decimals)
    liquidity = Decimal(str(pool["liquidityUsd"]))
    return proceeds, price, liquidity, _net_sol(quote, cfg.fee_bps)


def _sell_qty(position: Position, net_sol: int, size_lamports: int) -> int:
    if net_sol <= 0:
        return 0
    fraction = min(Decimal(1), Decimal(size_lamports) / Decimal(net_sol))
    raw = Decimal(position.qty_raw) * fraction
    qty = int(raw.to_integral_value(rounding=ROUND_DOWN))
    return min(qty, position.qty_raw)


def _qty_problem(
    proceeds: Decimal,
    liquidity: Decimal,
    net_sol: int,
    qty: int,
    size_sol: Decimal,
    *,
    cfg: DeskSettings,
) -> str | None:
    if net_sol <= 0:
        return "확인 실패: 매도 견적 수량이 0"
    if not within_pool(proceeds, liquidity, cfg.pool_cap_bps):
        return "매도 거절: 1회 주문이 풀 유동성의 2%를 초과"
    if qty <= 0 or size_sol <= 0:
        return "매도 거절: 주문 수량이 너무 작음"
    return None


def _scout_detail(candidate: Candidate) -> dict[str, object]:
    return {"mint": candidate.mint, "source": candidate.source}


def _net_sol(quote: dict[str, object], fee_bps: int) -> int:
    minimum = int(quote["minOut"])  # type: ignore[arg-type]
    platform = int(quote.get("platformFeeOut") or 0)
    net = Decimal(minimum - platform) * bps_factor(fee_bps)
    return int(net.to_integral_value(rounding=ROUND_DOWN))


def evaluate_exit(
    position: Position, quote: Read | None, now: float, cfg: DeskSettings
) -> ExitPlan:
    """Sell on the quote. A gap through the stop fills at the quote, not the stop."""
    if quote is None or not quote.ok:
        return ExitPlan((), "청산 보류: 견적 확인 실패")
    age = now - quote.fetched_at
    if age < 0 or age > cfg.stale_seconds:
        return ExitPlan((), f"청산 보류: 견적이 {cfg.stale_seconds:g}초보다 오래됨")
    output = str(quote.payload.get("outputMint") or "")
    if output not in (USDC_MINT, SOL_MINT):
        return ExitPlan((), "청산 보류: 견적 출력 자산이 USDC 또는 SOL이 아님")
    if str(quote.payload.get("inputMint") or "") != position.mint:
        return ExitPlan((), "청산 보류: 견적 민트가 포지션과 다름")
    try:
        proceeds = proceeds_from_quote(quote.payload, cfg.fee_bps)
        price = fill_price(proceeds, position.qty_raw, position.decimals)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return ExitPlan((), "청산 보류: 견적으로 체결가를 계산하지 못함")
    legs = _legs(position, price, proceeds, cfg)
    return ExitPlan(tuple(legs), "")


def _legs(
    position: Position, price: Decimal, proceeds: Decimal, cfg: DeskSettings
) -> list[ExitLeg]:
    entry = position.entry_price
    stop_at = entry * (Decimal(10_000 - cfg.stop_bps) / Decimal(10_000))
    tp1_at = entry * (Decimal(10_000 + cfg.tp1_bps) / Decimal(10_000))
    tp2_at = entry * (Decimal(10_000 + cfg.tp2_bps) / Decimal(10_000))
    if price <= stop_at:
        reason = (
            "손절 체결: 갭을 포함한 실제 견적가로 기록"
            f" (손절선 {stop_at:.8f}, 체결 {price:.8f})"
        )
        return [ExitLeg("stop", position.qty_raw, price, proceeds, reason)]
    remaining = position.qty_raw
    planned: list[tuple[str, int, str]] = []
    if not position.tp1_done and price >= tp1_at:
        half = position.initial_raw // 2
        if 0 < half < remaining:
            planned.append(("tp50", half, "익절 체결: +50% 구간에서 처음 수량의 절반"))
            remaining -= half
    if remaining > 0 and price >= tp2_at:
        planned.append(("tp100", remaining, "익절 체결: +100% 구간에서 남은 절반"))
    legs: list[ExitLeg] = []
    left = proceeds
    for index, (kind, qty, reason) in enumerate(planned):
        last = index == len(planned) - 1
        if last and sum(item[1] for item in planned) == position.qty_raw:
            slice_usd = left
        else:
            slice_usd = portion(proceeds, qty, position.qty_raw)
            left -= slice_usd
        legs.append(ExitLeg(kind, qty, price, slice_usd, reason))
    return legs


def _reject(
    steps: list[Step],
    seat: str,
    reason: str,
    detail: dict[str, object] | None = None,
) -> BuyPlan:
    steps.append(Step(seat, "reject", reason, detail or {}))
    return BuyPlan(tuple(steps), False)


def _preview_cost(quote: Read | None) -> Decimal | None:
    if quote is None or not quote.ok:
        return None
    try:
        return buy_cost_usd(quote.payload)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return None


def _quote_mismatch(
    quote: dict[str, object], mint: str, input_mint: str, expected_in: int
) -> str | None:
    if str(quote.get("outputMint") or "") != mint:
        return "확인 실패: 견적 출력 민트가 다름"
    if str(quote.get("inputMint") or "") != input_mint:
        return "확인 실패: 견적 입력 자산이 다름"
    try:
        quoted_in = int(quote["inAmount"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return "확인 실패: 견적 수량을 읽지 못함"
    if quoted_in != expected_in:
        return "확인 실패: 견적 수량이 주문과 다름"
    return None


def _authority_detail(read: Read | None) -> dict[str, object]:
    if read is None:
        return {}
    payload = read.payload
    return {
        "mintAuthority": payload.get("mintAuthority"),
        "freezeAuthority": payload.get("freezeAuthority"),
        "mintAuthoritySet": payload.get("mintAuthoritySet"),
        "freezeAuthoritySet": payload.get("freezeAuthoritySet"),
    }


def _whale_detail(read: Read | None) -> dict[str, object]:
    if read is None:
        return {}
    holders = read.payload.get("holders")
    if not isinstance(holders, list):
        return {}
    top = []
    for holder in holders[:5]:
        if isinstance(holder, dict):
            top.append({"address": holder.get("address"), "amount": holder.get("amount")})
    return {"top": top}
