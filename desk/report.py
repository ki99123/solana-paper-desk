"""Performance: win rate, return, drawdown, and a same-window buy-and-hold."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from desk.models import Position
from desk.pricing import haircut

_KST = ZoneInfo("Asia/Seoul")


def metrics(
    starting: Decimal,
    cash: Decimal,
    positions: list[Position],
    snapshots: list[Decimal],
    *,
    slippage_bps: int,
    fee_bps: int,
) -> dict[str, Decimal | int | None]:
    """Strategy numbers against holding every entry to the last mark."""
    closed = [item for item in positions if item.status == "closed"]
    wins = [item for item in closed if item.proceeds_usd > item.cost_usd]
    win_rate = None if not closed else Decimal(len(wins)) / Decimal(len(closed))
    equity = _strategy_equity(cash, positions, slippage_bps, fee_bps)
    bh = _hold_equity(starting, positions, slippage_bps, fee_bps)
    return {
        "starting": starting,
        "equity": equity,
        "cash": cash,
        "return": _ratio(equity, starting),
        "hold_equity": bh,
        "hold_return": _ratio(bh, starting),
        "max_drawdown": _drawdown(starting, snapshots),
        "closed": len(closed),
        "wins": len(wins),
        "win_rate": win_rate,
        "open": sum(1 for item in positions if item.status == "open"),
    }


def render(title_time: datetime | None, rows: dict[str, Decimal | int | None]) -> str:
    stamp = (title_time or datetime.now(_KST)).astimezone(_KST)
    win = rows["win_rate"]
    win_text = "청산된 거래 없음" if win is None else f"{_pct(Decimal(win))}"
    lines = [
        f"모의투자 성과  ({stamp.strftime('%Y-%m-%d %H:%M:%S')} KST)",
        f"시작 잔고     {_usd(rows['starting'])}",
        f"현재 평가액   {_usd(rows['equity'])}  (현금 {_usd(rows['cash'])})",
        f"총수익률      {_pct(Decimal(rows['return']))}",
        f"단순 보유     {_usd(rows['hold_equity'])}  ({_pct(Decimal(rows['hold_return']))})",
        f"최대낙폭      {_pct(Decimal(rows['max_drawdown']))}",
        f"승률          {win_text}  ({rows['wins']}/{rows['closed']})",
        f"보유 중       {rows['open']}",
        "단순 보유는 같은 매수 수량을 마지막 가격으로 평가하고,",
        "매도 슬리피지와 수수료를 뺀 값이다.",
        "손절과 익절을 하지 않고 그 시점까지 들고 있는 경우와 비교한다.",
    ]
    return "\n".join(lines) + "\n"


def _strategy_equity(
    cash: Decimal, positions: list[Position], slippage_bps: int, fee_bps: int
) -> Decimal:
    total = cash
    for position in positions:
        if position.status != "open":
            continue
        bid = haircut(position.last_price, slippage_bps, fee_bps)
        total += _tokens(position.qty_raw, position.decimals) * bid
    return total


def _hold_equity(
    starting: Decimal, positions: list[Position], slippage_bps: int, fee_bps: int
) -> Decimal:
    spent = sum((item.cost_usd for item in positions), Decimal(0))
    held = Decimal(0)
    for position in positions:
        bid = haircut(position.last_price, slippage_bps, fee_bps)
        held += _tokens(position.initial_raw, position.decimals) * bid
    return starting - spent + held


def _drawdown(starting: Decimal, snapshots: list[Decimal]) -> Decimal:
    peak = starting
    worst = Decimal(0)
    for equity in snapshots:
        peak = max(peak, equity)
        if peak > 0:
            drop = (peak - equity) / peak
            worst = max(worst, drop)
    return worst


def _ratio(equity: Decimal, starting: Decimal) -> Decimal:
    if starting == 0:
        return Decimal(0)
    return (equity - starting) / starting


def _tokens(raw: int, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** decimals)


def _usd(value: object) -> str:
    return f"${Decimal(str(value)):,.2f}"


def _pct(value: Decimal) -> str:
    return f"{(value * Decimal(100)):.2f}%"
