"""Small, guarded Hyperliquid execution CLI.

Dry-run is the default. A real order requires both HL_EXECUTION_ENABLED=true and
the --execute command flag. This module intentionally has no autonomous strategy.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


MAINNET_URL = "https://api.hyperliquid.xyz"
TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
ROOT = Path(__file__).resolve().parents[1]
AUDIT_FILE = ROOT / "data" / "orders.jsonl"


class GuardrailError(ValueError):
    """Raised when an order fails a mandatory local safety check."""


@dataclass(frozen=True)
class Settings:
    network: str
    execution_enabled: bool
    max_order_notional: Decimal
    max_position_notional: Decimal
    allowed_coins: frozenset[str]
    max_slippage: Decimal
    cancel_after_seconds: int
    private_key: str | None

    @property
    def base_url(self) -> str:
        return MAINNET_URL if self.network == "mainnet" else TESTNET_URL

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        network = os.getenv("HL_NETWORK", "testnet").lower()
        if network not in {"mainnet", "testnet"}:
            raise GuardrailError("HL_NETWORK must be 'mainnet' or 'testnet'.")
        allowed = frozenset(
            coin.strip().upper()
            for coin in os.getenv("HL_ALLOWED_COINS", "BTC,ETH,SOL").split(",")
            if coin.strip()
        )
        return cls(
            network=network,
            execution_enabled=os.getenv("HL_EXECUTION_ENABLED", "false").lower() == "true",
            max_order_notional=Decimal(os.getenv("HL_MAX_ORDER_NOTIONAL_USD", "25")),
            max_position_notional=Decimal(os.getenv("HL_MAX_POSITION_NOTIONAL_USD", "100")),
            allowed_coins=allowed,
            max_slippage=Decimal(os.getenv("HL_MAX_SLIPPAGE", "0.01")),
            cancel_after_seconds=int(os.getenv("HL_CANCEL_AFTER_SECONDS", "30")),
            private_key=os.getenv("HL_WALLET_PRIVATE_KEY") or None,
        )


def decimal(value: str, label: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise GuardrailError(f"{label} must be numeric.") from exc
    if number <= 0:
        raise GuardrailError(f"{label} must be greater than zero.")
    return number


def audit(event: dict[str, Any]) -> None:
    AUDIT_FILE.parent.mkdir(exist_ok=True)
    with AUDIT_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"at": int(time.time() * 1000), **event}, sort_keys=True) + "\n")


def clients(settings: Settings):
    """Create SDK clients lazily so a dry-run status check needs no key."""
    from hyperliquid.info import Info

    return Info(settings.base_url, skip_ws=True)


def current_mid(info: Any, coin: str) -> Decimal:
    mids = info.all_mids()
    if coin not in mids:
        raise GuardrailError(f"{coin} is not available from this Hyperliquid endpoint.")
    return decimal(str(mids[coin]), "Market mid price")


def current_position_notional(info: Any, address: str, coin: str, mid: Decimal) -> Decimal:
    state = info.user_state(address)
    for position in state.get("assetPositions", []):
        payload = position.get("position", {})
        if payload.get("coin") == coin:
            return abs(Decimal(str(payload.get("szi", "0")))) * mid
    return Decimal("0")


def require_execution(settings: Settings, execute: bool) -> None:
    if not execute:
        raise GuardrailError("Dry run only. Re-run with --execute after reviewing the output.")
    if not settings.execution_enabled:
        raise GuardrailError("Set HL_EXECUTION_ENABLED=true in the local .env before real execution.")
    if not settings.private_key:
        raise GuardrailError("HL_WALLET_PRIVATE_KEY is required for real execution.")


def submit_order(settings: Settings, args: argparse.Namespace, mid: Decimal, notional: Decimal) -> dict[str, Any]:
    from eth_account import Account
    from hyperliquid.exchange import Exchange

    wallet = Account.from_key(settings.private_key)
    exchange = Exchange(wallet, settings.base_url)
    if settings.cancel_after_seconds:
        exchange.schedule_cancel(int(time.time() * 1000) + settings.cancel_after_seconds * 1000)
    is_buy = args.side == "buy"
    if args.type == "market":
        if args.reduce_only:
            response = exchange.market_close(args.coin, float(args.size), None, float(settings.max_slippage))
        else:
            response = exchange.market_open(args.coin, is_buy, float(args.size), None, float(settings.max_slippage))
    else:
        response = exchange.order(
            args.coin,
            is_buy,
            float(args.size),
            float(args.price),
            {"limit": {"tif": args.tif.capitalize()}},
            args.reduce_only,
        )
    audit({"kind": "submitted", "network": settings.network, "coin": args.coin, "side": args.side,
           "type": args.type, "size": str(args.size), "mid": str(mid), "notional": str(notional),
           "reduce_only": args.reduce_only, "response": response})
    return response


def order(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    args.coin = args.coin.upper()
    args.size = decimal(args.size, "Size")
    if args.coin not in settings.allowed_coins:
        raise GuardrailError(f"{args.coin} is not in HL_ALLOWED_COINS.")
    if args.type == "limit":
        if args.price is None:
            raise GuardrailError("--price is required for a limit order.")
        args.price = decimal(args.price, "Limit price")

    info = clients(settings)
    mid = current_mid(info, args.coin)
    notional = args.size * (args.price if args.type == "limit" else mid)
    if notional > settings.max_order_notional:
        raise GuardrailError(f"Order notional ${notional} exceeds ${settings.max_order_notional} limit.")

    if args.execute and settings.private_key:
        from eth_account import Account
        address = Account.from_key(settings.private_key).address
        existing = current_position_notional(info, address, args.coin, mid)
        if not args.reduce_only and existing + notional > settings.max_position_notional:
            raise GuardrailError(f"Resulting position ${existing + notional} exceeds ${settings.max_position_notional} limit.")

    preview = {"network": settings.network, "coin": args.coin, "side": args.side, "type": args.type,
               "size": str(args.size), "mid": str(mid), "estimated_notional_usd": str(notional),
               "reduce_only": args.reduce_only, "will_execute": bool(args.execute and settings.execution_enabled)}
    if not args.execute:
        audit({"kind": "dry_run", **preview})
        print(json.dumps(preview, indent=2))
        return 0
    require_execution(settings, args.execute)
    print(json.dumps({**preview, "result": submit_order(settings, args, mid, notional)}, indent=2, default=str))
    return 0


def status(_: argparse.Namespace) -> int:
    settings = Settings.from_env()
    info = clients(settings)
    mids = info.all_mids()
    print(json.dumps({"network": settings.network, "base_url": settings.base_url,
                      "execution_enabled": settings.execution_enabled,
                      "allowed_coins": sorted(settings.allowed_coins),
                      "sample_mids": {coin: mids.get(coin) for coin in sorted(settings.allowed_coins)}}, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Guarded Hyperliquid order executor")
    sub = command.add_subparsers(required=True)
    status_parser = sub.add_parser("status", help="Read public market connectivity")
    status_parser.set_defaults(handler=status)
    order_parser = sub.add_parser("order", help="Preview or submit one guarded order")
    order_parser.add_argument("--coin", required=True)
    order_parser.add_argument("--side", required=True, choices=["buy", "sell"])
    order_parser.add_argument("--size", required=True)
    order_parser.add_argument("--type", choices=["market", "limit"], default="market")
    order_parser.add_argument("--price", help="Required for a limit order")
    order_parser.add_argument("--tif", choices=["alo", "ioc", "gtc"], default="gtc")
    order_parser.add_argument("--reduce-only", action="store_true")
    order_parser.add_argument("--execute", action="store_true", help="Second execution gate")
    order_parser.set_defaults(handler=order)
    return command


if __name__ == "__main__":
    try:
        arguments = parser().parse_args()
        raise SystemExit(arguments.handler(arguments))
    except GuardrailError as exc:
        raise SystemExit(f"Blocked: {exc}")
