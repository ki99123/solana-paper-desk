"""Public read-only market data. Quote URLs only; no swap build or submit."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from desk.models import Candidate, Read
from desk.parse import parse_jupiter, parse_pool, parse_profiles, parse_rpc_batch, parse_rug
from desk.pricing import SOL_MINT, USDC_MINT

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
RUG_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
POOL_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
PROFILE_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
_TIMEOUT = 8.0


class FixtureMarket:
    """Canned normalized reads for one candidate. Used by tests and the smoke run."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def discovery(self) -> list[Candidate]:
        payload = json.loads((self.root / "candidate.json").read_text(encoding="utf-8"))
        return [
            Candidate(
                mint=str(payload["mint"]),
                symbol=str(payload.get("symbol") or ""),
                source=str(payload.get("source") or "dexscreener"),
            )
        ]

    async def profiles(self) -> list[Candidate]:
        return await self.discovery()

    async def load_buy(
        self, mint: str, input_mint: str, amount: int, slippage_bps: int
    ) -> dict[str, Read]:
        del mint, input_mint, amount, slippage_bps
        now = time.time()
        names = ("rug", "rpc", "pool", "quote")
        return {name: _file_read(self.root / f"{name}.json", name, now) for name in names}

    async def pool(self, mint: str) -> Read:
        del mint
        return _file_read(self.root / "pool.json", "pool", time.time())

    async def load_sell(
        self, mint: str, raw_amount: int, slippage_bps: int, output_mint: str
    ) -> Read:
        del mint, raw_amount, slippage_bps, output_mint
        path = self.root / "quote_sell.json"
        if not path.is_file():
            return Read("quote", False, time.time(), {}, "missing sell quote")
        return _file_read(path, "quote", time.time())


class LiveMarket:
    """PumpPortal is handled by the daemon. This client only does HTTPS GETs and one RPC batch."""

    def __init__(
        self,
        *,
        rpc_url: str,
        quote_url: str,
        jupiter_key: str,
        rugcheck_key: str,
    ) -> None:
        self.rpc_url = rpc_url or PUBLIC_RPC
        self.quote_url = quote_url
        self._jupiter_key = jupiter_key
        self._rug_key = rugcheck_key

    async def profiles(self) -> list[Candidate]:
        body = await _get_json(PROFILE_URL, {})
        if not isinstance(body, list):
            raise ValueError("profiles")
        return parse_profiles(body)

    async def load_buy(
        self, mint: str, input_mint: str, amount: int, slippage_bps: int
    ) -> dict[str, Read]:
        rug, rpc, pool, quote = await _gather(
            self._rug(mint),
            self._rpc(mint),
            self._pool(mint),
            self._quote(input_mint, mint, amount, slippage_bps),
        )
        return {"rug": rug, "rpc": rpc, "pool": pool, "quote": quote}

    async def pool(self, mint: str) -> Read:
        return await self._pool(mint)

    async def load_sell(
        self, mint: str, raw_amount: int, slippage_bps: int, output_mint: str
    ) -> Read:
        if output_mint not in (USDC_MINT, SOL_MINT):
            return Read("quote", False, time.time(), {}, "output")
        return await self._quote(mint, output_mint, raw_amount, slippage_bps)

    async def _rug(self, mint: str) -> Read:
        headers = {"X-API-KEY": self._rug_key} if self._rug_key else {}
        return await _parsed("rug", RUG_URL.format(mint=mint), headers, parse_rug)

    async def _pool(self, mint: str) -> Read:
        def parse(body: object) -> dict[str, object]:
            return parse_pool(body, mint)

        return await _parsed("pool", POOL_URL.format(mint=mint), {}, parse)

    async def _quote(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int
    ) -> Read:
        headers = {"x-api-key": self._jupiter_key} if self._jupiter_key else {}
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
        }
        return await _parsed("quote", self.quote_url, headers, parse_jupiter, params=params)

    async def _rpc(self, mint: str) -> Read:
        now = time.time()
        payload = [
            {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [mint]},
            {"jsonrpc": "2.0", "id": 2, "method": "getTokenLargestAccounts", "params": [mint]},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "getAccountInfo",
                "params": [mint, {"encoding": "base64"}],
            },
        ]
        try:
            body = await _post_json(self.rpc_url, payload)
            parsed = parse_rpc_batch(body)
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError) as exc:
            return Read("rpc", False, now, {}, type(exc).__name__)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return Read("rpc", False, time.time(), {}, type(exc).__name__)
        return Read("rpc", True, time.time(), parsed, "")


def _file_read(path: Path, name: str, now: float) -> Read:
    if not path.is_file():
        return Read(name, False, now, {}, "missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Read(name, False, now, {}, type(exc).__name__)
    if not isinstance(payload, dict):
        return Read(name, False, now, {}, "shape")
    return Read(name, True, now, payload, "")


async def _gather(*awaits: object) -> tuple[Read, Read, Read, Read]:
    rug, rpc, pool, quote = await asyncio.gather(*awaits)  # type: ignore[arg-type]
    return rug, rpc, pool, quote


async def _parsed(
    name: str,
    url: str,
    headers: dict[str, str],
    parser: object,
    params: dict[str, str] | None = None,
) -> Read:
    now = time.time()
    try:
        body = await _get_json(url, headers, params)
        if not callable(parser):
            raise TypeError("parser")
        parsed = parser(body)
    except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError) as exc:
        return Read(name, False, now, {}, type(exc).__name__)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return Read(name, False, time.time(), {}, type(exc).__name__)
    if not isinstance(parsed, dict):
        return Read(name, False, time.time(), {}, "shape")
    return Read(name, True, time.time(), parsed, "")


async def _get_json(
    url: str, headers: dict[str, str], params: dict[str, str] | None = None
) -> object:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()


async def _post_json(url: str, payload: list[object]) -> object:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
