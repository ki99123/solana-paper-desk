"""Pass/fail gates. A RugCheck score is never an input."""

from __future__ import annotations

from decimal import Decimal

from desk.models import BookView, Candidate, Read
from desk.parse import looks_like_mint
from desk.pricing import is_fresh, within_pool

_DANGER = frozenset({"danger", "critical", "high"})
_SOURCES = frozenset({"pumpportal", "dexscreener", "cli"})


def scout_gate(candidate: Candidate) -> tuple[bool, str]:
    if candidate.source not in _SOURCES:
        return False, "발굴 실패: 출처가 PumpPortal, DexScreener, 수동 명령이 아님"
    if not looks_like_mint(candidate.mint):
        return False, "발굴 실패: 민트 형식이 아님"
    return True, "발굴 통과"


def rug_gate(
    rug: Read | None, rpc: Read | None, now: float, limit: float
) -> tuple[bool, str]:
    for read, label in ((rug, "RugCheck"), (rpc, "민트 계정")):
        problem = _ready(read, label, now, limit)
        if problem:
            return False, problem
    if rug is None or rpc is None:
        return False, "매수 금지: RugCheck 확인 실패"
    return _rug_body(rug.payload, rpc.payload)


def whale_gate(read: Read | None, now: float, limit: float, max_pct: int) -> tuple[bool, str]:
    problem = _ready(read, "고래 RPC", now, limit)
    if problem or read is None:
        return False, problem or "매수 금지: 고래 RPC 확인 실패"
    ranked = _holders(read.payload)
    if isinstance(ranked, str):
        return False, ranked
    return _whale_limit(ranked[0], ranked[1], max_pct)


def risk_gate(
    reads: dict[str, Read],
    now: float,
    limit: float,
    *,
    order_usd: Decimal,
    cap_bps: int,
    book: BookView,
    mint: str,
) -> tuple[bool, str]:
    """Veto. Stale or failed inputs forbid a buy even when every other seat passed."""
    problem = _inputs_ready(reads, now, limit)
    if problem:
        return False, problem
    held = _cash_gate(book, mint, order_usd)
    if held:
        return False, held
    return _liquidity_gate(reads["pool"].payload, order_usd, cap_bps)


def _rug_body(payload: dict[str, object], rpc_payload: dict[str, object]) -> tuple[bool, str]:
    structural = _rug_structural(payload, rpc_payload)
    if structural:
        return False, structural
    return _rug_risks(payload.get("risks"))


def _rug_structural(payload: dict[str, object], rpc_payload: dict[str, object]) -> str | None:
    if "mintAuthoritySet" not in rpc_payload or "freezeAuthoritySet" not in rpc_payload:
        return "러그 점검 실패: 온체인 권한을 확인하지 못함"
    if payload.get("rugged") is True:
        return "러그 점검 실패: 이미 러그로 표시됨"
    if payload.get("mintAuthority") or rpc_payload.get("mintAuthoritySet") is True:
        return "러그 점검 실패: 민트 권한이 남아 있음"
    if payload.get("freezeAuthority") or rpc_payload.get("freezeAuthoritySet") is True:
        return "러그 점검 실패: 동결 권한이 남아 있음"
    return None


def _rug_risks(risks: object) -> tuple[bool, str]:
    if not isinstance(risks, list):
        return False, "러그 점검 실패: 위험 목록을 확인하지 못함"
    for risk in risks:
        if not isinstance(risk, dict):
            return False, "러그 점검 실패: 위험 목록을 확인하지 못함"
        level = str(risk.get("level", "")).lower()
        if level in _DANGER:
            name = str(risk.get("name") or level)
            return False, f"러그 점검 실패: {name}"
    return True, "러그 점검 통과"


def _holders(payload: dict[str, object]) -> tuple[int, list[tuple[str, int]]] | str:
    try:
        supply = int(str(payload["supply"]))
        holders = payload["holders"]
    except (KeyError, TypeError, ValueError):
        return "고래 점검 실패: 공급량 또는 보유 계정을 확인하지 못함"
    if supply <= 0 or not isinstance(holders, list) or len(holders) < 2:
        return "고래 점검 실패: 공급량 또는 보유 계정을 확인하지 못함"
    ranked = _amounts(holders)
    if isinstance(ranked, str):
        return ranked
    ranked.sort(key=lambda item: item[1], reverse=True)
    return supply, ranked


def _amounts(holders: list[object]) -> list[tuple[str, int]] | str:
    ranked: list[tuple[str, int]] = []
    for holder in holders:
        if not isinstance(holder, dict):
            return "고래 점검 실패: 보유 계정 형식이 아님"
        try:
            amount = int(str(holder["amount"]))
        except (KeyError, TypeError, ValueError):
            return "고래 점검 실패: 보유 수량을 확인하지 못함"
        ranked.append((str(holder.get("address") or ""), amount))
    return ranked


def _whale_limit(
    supply: int, ranked: list[tuple[str, int]], max_pct: int
) -> tuple[bool, str]:
    cap = Decimal(max_pct)
    for address, amount in ranked[1:]:
        pct = Decimal(amount) / Decimal(supply) * Decimal(100)
        if pct > cap:
            return False, f"고래 점검 실패: {address} 가 공급량의 {pct:.2f}% 를 보유"
    return True, "고래 점검 통과: 가장 큰 계정을 풀로 보고, 나머지 지갑은 한도 이하"


def _inputs_ready(reads: dict[str, Read], now: float, limit: float) -> str | None:
    labels = {
        "rug": "RugCheck",
        "rpc": "고래 RPC",
        "pool": "풀 유동성",
        "quote": "Jupiter 견적",
    }
    for name, label in labels.items():
        problem = _ready(reads.get(name), label, now, limit)
        if problem:
            return problem
    return None


def _cash_gate(book: BookView, mint: str, order_usd: Decimal) -> str | None:
    if mint in book.open_mints:
        return "매수 금지: 이미 보유 중인 코인"
    if mint in book.final_mints:
        return "매수 금지: 이 민트는 이미 판단이 끝남"
    if order_usd > book.cash_usd:
        return "매수 금지: 가상 잔고 부족"
    return None


def _liquidity_gate(
    pool: dict[str, object], order_usd: Decimal, cap_bps: int
) -> tuple[bool, str]:
    try:
        liquidity = Decimal(str(pool["liquidityUsd"]))
    except (KeyError, ArithmeticError, ValueError):
        return False, "매수 금지: 풀 유동성을 확인하지 못함"
    if not within_pool(order_usd, liquidity, cap_bps):
        return False, "매수 금지: 1회 주문이 풀 유동성의 2%를 초과"
    return True, "리스크 통과"


def _ready(read: Read | None, label: str, now: float, limit: float) -> str | None:
    if read is None or not read.ok:
        return f"매수 금지: {label} 확인 실패"
    if not is_fresh(read.fetched_at, now, limit):
        return f"매수 금지: {label} 데이터가 {limit:g}초보다 오래됨"
    return None
