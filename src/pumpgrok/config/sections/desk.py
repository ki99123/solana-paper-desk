"""Paper-desk settings. Public market data only; no wallet and no signer."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

_BPS_FIELDS = ("stop_bps", "tp1_bps", "tp2_bps", "fee_bps", "slippage_bps", "pool_cap_bps")


class DeskSettings(BaseModel):
    """Virtual USD book and pass/fail gates for the paper desk.

    ``jupiter_api_key`` and ``rugcheck_api_key`` are optional. Empty means the
    public endpoint is called with no credential. RPC defaults to the public
    Solana endpoint when ``rpc_url`` is empty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    starting_usd: Decimal = Field(default=Decimal("1000"), gt=0)
    order_usd: Decimal = Field(default=Decimal("20"), gt=0)
    stop_bps: int = 2500
    tp1_bps: int = 5000
    tp2_bps: int = 10000
    fee_bps: int = 30
    slippage_bps: int = 100
    pool_cap_bps: int = 200
    stale_seconds: int = Field(default=10, ge=1, le=10)
    whale_max_pct: int = Field(default=10, ge=1, le=100)
    poll_seconds: int = Field(default=10, ge=1, le=300)
    rpc_url: str = ""
    jupiter_quote_url: str = "https://lite-api.jup.ag/swap/v1/quote"
    jupiter_api_key: SecretStr = SecretStr("")
    rugcheck_api_key: SecretStr = SecretStr("")
    fixture: str = ""
    journal: str = ""

    @field_validator(*_BPS_FIELDS)
    @classmethod
    def _bps(cls, value: int) -> int:
        if not 0 <= int(value) <= 10_000:
            raise ValueError("bps must be between 0 and 10000")
        return int(value)

    @field_validator("rpc_url", "jupiter_quote_url")
    @classmethod
    def _https_or_blank(cls, value: str) -> str:
        text = value.strip()
        if text and not text.startswith("https://"):
            raise ValueError("url must be empty or https")
        return text

    @model_validator(mode="after")
    def _ladder(self) -> DeskSettings:
        if self.order_usd > self.starting_usd:
            raise ValueError("order_usd exceeds starting_usd")
        if not 0 < self.stop_bps < 10_000:
            raise ValueError("stop_bps must be between 1 and 9999")
        if self.tp1_bps <= 0 or self.tp2_bps <= self.tp1_bps:
            raise ValueError("take-profit ladder must rise")
        if self.pool_cap_bps > 200:
            raise ValueError("pool cap cannot exceed 2 percent")
        return self
