"""Normalize public API payloads. Unknown fields are dropped on purpose."""

from __future__ import annotations

import base64
from decimal import Decimal

from desk.models import Candidate

TOKEN_PROGRAMS = frozenset(
    {
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    }
)
_BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def looks_like_mint(value: str) -> bool:
    return 32 <= len(value) <= 44 and all(char in _BASE58 for char in value)


def parse_jupiter(body: object) -> dict[str, object]:
    """Keep the quote numbers. Do not keep route or transaction blobs."""
    if not isinstance(body, dict):
        raise ValueError("quote body")
    required = ("inputMint", "outputMint", "inAmount", "outAmount", "otherAmountThreshold")
    if any(key not in body for key in required):
        raise ValueError("quote fields")
    fee_out = 0
    platform = body.get("platformFee")
    if isinstance(platform, dict) and platform.get("amount") not in (None, ""):
        fee_mint = platform.get("feeMint")
        if fee_mint in (None, body.get("outputMint")):
            fee_out = int(platform["amount"])
    swap = body.get("swapUsdValue")
    return {
        "inputMint": str(body["inputMint"]),
        "outputMint": str(body["outputMint"]),
        "inAmount": int(body["inAmount"]),
        "outAmount": int(body["outAmount"]),
        "minOut": int(body["otherAmountThreshold"]),
        "platformFeeOut": fee_out,
        "swapUsd": None if swap is None else str(swap),
    }


def parse_rug(body: object) -> dict[str, object]:
    if not isinstance(body, dict):
        raise ValueError("rug body")
    for key in ("rugged", "mintAuthority", "freezeAuthority", "risks"):
        if key not in body:
            raise ValueError(f"rug missing {key}")
    risks = body["risks"]
    if not isinstance(risks, list) or any(not isinstance(item, dict) for item in risks):
        raise ValueError("rug risks")
    return {
        "rugged": bool(body["rugged"]),
        "mintAuthority": body["mintAuthority"],
        "freezeAuthority": body["freezeAuthority"],
        "risks": [
            {"level": str(item.get("level", "")), "name": str(item.get("name", ""))}
            for item in risks
        ],
    }


def parse_pool(body: object, mint: str) -> dict[str, object]:
    if not isinstance(body, dict) or not isinstance(body.get("pairs"), list):
        raise ValueError("pool body")
    best: dict[str, object] | None = None
    best_liq = Decimal("-1")
    for pair in body["pairs"]:
        if not isinstance(pair, dict) or str(pair.get("chainId", "")).lower() != "solana":
            continue
        base = pair.get("baseToken")
        if not isinstance(base, dict) or base.get("address") != mint:
            continue
        liquidity = pair.get("liquidity")
        usd = liquidity.get("usd") if isinstance(liquidity, dict) else None
        if usd is None:
            continue
        amount = Decimal(str(usd))
        if amount > best_liq:
            best_liq = amount
            best = pair
    if best is None:
        raise ValueError("no solana pool")
    base = best.get("baseToken")
    symbol = str(base.get("symbol") or "") if isinstance(base, dict) else ""
    price = best.get("priceUsd")
    return {
        "liquidityUsd": format(best_liq, "f"),
        "priceUsd": None if price is None else str(price),
        "symbol": symbol,
        "pairAddress": str(best.get("pairAddress") or ""),
    }


def parse_mint_bytes(raw: bytes) -> dict[str, object]:
    """SPL mint header. Longer Token-2022 accounts still start with these 82 bytes."""
    if len(raw) < 82:
        raise ValueError("short mint account")
    mint_opt = int.from_bytes(raw[0:4], "little")
    freeze_opt = int.from_bytes(raw[46:50], "little")
    if mint_opt not in (0, 1) or freeze_opt not in (0, 1):
        raise ValueError("mint authority flag")
    decimals = raw[44]
    if not 0 <= decimals <= 18:
        raise ValueError("decimals")
    return {
        "mintAuthoritySet": mint_opt == 1,
        "freezeAuthoritySet": freeze_opt == 1,
        "supply": int.from_bytes(raw[36:44], "little"),
        "decimals": decimals,
    }


def parse_rpc_batch(rows: object) -> dict[str, object]:
    """Read supply, largest accounts and the mint account. Ids are 1, 2 and 3."""
    by_id = _rpc_rows(rows)
    header = _mint_header(_value(by_id.get(3)), _value(by_id.get(1)))
    return {
        "supply": str(header["supply"]),
        "decimals": int(header["decimals"]),
        "mintAuthoritySet": bool(header["mintAuthoritySet"]),
        "freezeAuthoritySet": bool(header["freezeAuthoritySet"]),
        "holders": _holder_rows(_value(by_id.get(2))),
    }


def _rpc_rows(rows: object) -> dict[int, object]:
    if not isinstance(rows, list):
        raise ValueError("rpc batch")
    by_id: dict[int, object] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("error"):
            raise ValueError("rpc error")
        by_id[int(row["id"])] = row.get("result")
    return by_id


def _mint_header(account: object, supply_value: object) -> dict[str, object]:
    if not isinstance(supply_value, dict):
        raise ValueError("rpc shape")
    if not isinstance(account, dict):
        raise ValueError("mint account missing")
    owner = str(account.get("owner") or "")
    if owner not in TOKEN_PROGRAMS:
        raise ValueError("mint owner")
    header = parse_mint_bytes(_account_bytes(account.get("data")))
    if int(str(supply_value["amount"])) != int(header["supply"]):
        raise ValueError("supply mismatch")
    if int(supply_value["decimals"]) != int(header["decimals"]):
        raise ValueError("decimals mismatch")
    return header


def _holder_rows(holders_value: object) -> list[dict[str, str]]:
    if not isinstance(holders_value, list):
        raise ValueError("rpc shape")
    holders: list[dict[str, str]] = []
    for item in holders_value:
        if not isinstance(item, dict):
            raise ValueError("holder shape")
        holders.append(
            {"address": str(item.get("address") or ""), "amount": str(item["amount"])}
        )
    return holders


def parse_pumpportal(message: object) -> Candidate | None:
    if not isinstance(message, dict):
        return None
    if str(message.get("txType") or "") != "create":
        return None
    mint = str(message.get("mint") or "")
    if not looks_like_mint(mint):
        return None
    symbol = str(message.get("symbol") or message.get("name") or "")
    return Candidate(mint=mint, symbol=symbol, source="pumpportal")


def parse_profiles(body: object) -> list[Candidate]:
    if not isinstance(body, list):
        raise ValueError("profiles")
    found: list[Candidate] = []
    for item in body:
        if not isinstance(item, dict) or str(item.get("chainId", "")).lower() != "solana":
            continue
        mint = str(item.get("tokenAddress") or "")
        if looks_like_mint(mint):
            found.append(Candidate(mint=mint, symbol="", source="dexscreener"))
    return found


def _value(result: object) -> object:
    if not isinstance(result, dict):
        raise ValueError("rpc result")
    return result.get("value")


def _account_bytes(data: object) -> bytes:
    if not isinstance(data, list) or len(data) != 2 or data[1] != "base64":
        raise ValueError("mint encoding")
    try:
        return base64.b64decode(str(data[0]), validate=True)
    except ValueError as exc:
        raise ValueError("mint base64") from exc
