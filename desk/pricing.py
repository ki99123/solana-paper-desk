"""Paper fill math. Quotes stay integers; USD and prices stay Decimal."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_DECIMALS = 6
SOL_DECIMALS = 9
USD_QUANTUM = Decimal("0.000001")


def bps_factor(bps: int) -> Decimal:
    """Multiplier that keeps ``bps`` of cost inside the fill."""
    return Decimal(10_000 - bps) / Decimal(10_000)


def usdc_raw(amount: Decimal) -> int:
    """Whole USDC base units. 1 USDC is 1_000_000."""
    return int((amount * Decimal(10**USDC_DECIMALS)).to_integral_value(rounding=ROUND_DOWN))


def tokens_from_quote(quote: dict[str, object], fee_bps: int) -> int:
    """Token base units after the quote minimum and the paper fee."""
    net = _net_out(quote, fee_bps)
    raw = int(net.to_integral_value(rounding=ROUND_DOWN))
    if raw <= 0:
        raise ValueError("non-positive token fill")
    return raw


def proceeds_from_quote(quote: dict[str, object], fee_bps: int) -> Decimal:
    """USD received for a sell quote, after slippage minimum and fee."""
    net = _net_out(quote, fee_bps)
    output = str(quote["outputMint"])
    if output == USDC_MINT:
        units = int(net.to_integral_value(rounding=ROUND_DOWN))
        if units <= 0:
            raise ValueError("non-positive proceeds")
        return Decimal(units) / Decimal(10**USDC_DECIMALS)
    swap = quote.get("swapUsd")
    out_amount = int(quote["outAmount"])  # type: ignore[arg-type]
    if swap is None or out_amount <= 0:
        raise ValueError("usd unverified")
    per_unit = Decimal(str(swap)) / Decimal(out_amount)
    proceeds = (net * per_unit).quantize(USD_QUANTUM, rounding=ROUND_DOWN)
    if proceeds <= 0:
        raise ValueError("non-positive proceeds")
    return proceeds


def buy_cost_usd(quote: dict[str, object]) -> Decimal:
    """USD debited for a buy. USDC spends the quoted input. SOL needs swap USD."""
    if str(quote["inputMint"]) == USDC_MINT:
        return Decimal(int(quote["inAmount"])) / Decimal(10**USDC_DECIMALS)  # type: ignore[arg-type]
    swap = quote.get("swapUsd")
    if swap is None:
        raise ValueError("usd unverified")
    cost = Decimal(str(swap)).quantize(USD_QUANTUM, rounding=ROUND_DOWN)
    if cost <= 0:
        raise ValueError("non-positive cost")
    return cost


def fill_price(notional: Decimal, raw_tokens: int, decimals: int) -> Decimal:
    """USD per whole token."""
    tokens = Decimal(raw_tokens) / (Decimal(10) ** decimals)
    if tokens <= 0 or notional <= 0:
        raise ValueError("price undefined")
    return notional / tokens


def portion(total: Decimal, qty: int, whole: int) -> Decimal:
    """Slice of a USD amount. The last slice should use the remainder."""
    if qty <= 0 or whole <= 0 or qty > whole:
        raise ValueError("bad portion")
    if qty == whole:
        return total
    return (total * Decimal(qty) / Decimal(whole)).quantize(USD_QUANTUM, rounding=ROUND_DOWN)


def within_pool(order_usd: Decimal, liquidity_usd: Decimal, cap_bps: int) -> bool:
    """True when the order is at most ``cap_bps`` of pool liquidity."""
    if liquidity_usd <= 0 or order_usd <= 0:
        return False
    return order_usd <= liquidity_usd * Decimal(cap_bps) / Decimal(10_000)


def is_fresh(fetched_at: float, now: float, limit_seconds: float) -> bool:
    """True when a read is not from the future and not older than the limit."""
    age = now - fetched_at
    return 0 <= age <= limit_seconds


def haircut(price: Decimal, slippage_bps: int, fee_bps: int) -> Decimal:
    """Sell-side mark after slippage and fee."""
    return price * bps_factor(slippage_bps) * bps_factor(fee_bps)


def _net_out(quote: dict[str, object], fee_bps: int) -> Decimal:
    minimum = int(quote["minOut"])  # type: ignore[arg-type]
    platform = int(quote.get("platformFeeOut") or 0)
    net = Decimal(minimum - platform) * bps_factor(fee_bps)
    if net <= 0:
        raise ValueError("non-positive net output")
    return net
