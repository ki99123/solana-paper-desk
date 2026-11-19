"""Public payload parsers drop anything that is not a price input."""

import base64

import pytest
from desk.parse import parse_jupiter, parse_mint_bytes, parse_pumpportal, parse_rpc_batch
from desk.pricing import USDC_MINT

MINT = "Mint11111111111111111111111111111111"


def test_jupiter_keeps_the_quote_and_drops_the_route() -> None:
    parsed = parse_jupiter(
        {
            "inputMint": USDC_MINT,
            "outputMint": MINT,
            "inAmount": "20000000",
            "outAmount": "1000000",
            "otherAmountThreshold": "990000",
            "swapUsdValue": "20",
            "routePlan": [{"swapInfo": {"label": "ignored"}}],
            "transaction": "not-a-quote",
        }
    )
    assert "routePlan" not in parsed
    assert "transaction" not in parsed
    assert parsed["minOut"] == 990_000
    assert parsed["swapUsd"] == "20"


def test_mint_header_reads_supply_and_authorities() -> None:
    raw = bytearray(82)
    raw[36:44] = (1_000).to_bytes(8, "little")
    raw[44] = 6
    parsed = parse_mint_bytes(bytes(raw))
    assert parsed == {
        "mintAuthoritySet": False,
        "freezeAuthoritySet": False,
        "supply": 1_000,
        "decimals": 6,
    }


def test_rpc_batch_fails_when_supply_does_not_match_the_account() -> None:
    raw = bytearray(82)
    raw[36:44] = (1_000).to_bytes(8, "little")
    raw[44] = 6
    encoded = base64.b64encode(bytes(raw)).decode("ascii")
    rows = [
        {"id": 1, "result": {"value": {"amount": "999", "decimals": 6}}},
        {"id": 2, "result": {"value": [{"address": "a", "amount": "1"}]}},
        {
            "id": 3,
            "result": {
                "value": {
                    "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "data": [encoded, "base64"],
                }
            },
        },
    ]
    with pytest.raises(ValueError, match="supply mismatch"):
        parse_rpc_batch(rows)


def test_pumpportal_create_is_a_candidate_and_other_messages_are_not() -> None:
    created = parse_pumpportal({"txType": "create", "mint": MINT, "symbol": "PAPER"})
    assert created is not None
    assert created.source == "pumpportal"
    assert parse_pumpportal({"txType": "buy", "mint": MINT}) is None
