# Hyperliquid trade environment

This is a minimal, guarded order executor—an execution layer, not an automated strategy. It defaults to testnet and dry-run mode. It cannot submit an order unless both the environment flag and the command-line flag are deliberately enabled.

## Setup

1. Create an isolated Python environment and install the requirements.
2. Copy `.env.example` to `.env`.
3. Leave `HL_NETWORK=testnet` and `HL_EXECUTION_ENABLED=false` while validating your setup. Add the private key only to `.env`, never to source code or chat.
4. Check public connectivity:

```bash
python -m src.trader status
```

5. Preview an order (does not submit):

```bash
python -m src.trader order --coin ETH --side buy --size 0.01 --type market
```

6. To submit on a funded account, set the environment variables deliberately, then use `--execute` only after reviewing the printed preview:

```bash
python -m src.trader order --coin ETH --side buy --size 0.01 --type market --execute
```

## Built-in controls

- Testnet by default; mainnet requires `HL_NETWORK=mainnet`.
- Two independent execution gates: `HL_EXECUTION_ENABLED=true` and `--execute`.
- Coin allow-list, maximum order notional, maximum resulting position notional, and maximum market-order slippage.
- Market data is fetched immediately before every order.
- A dead-man switch schedules cancellation of open orders after `HL_CANCEL_AFTER_SECONDS`. It is refreshed only when an order is actually submitted.
- Audit records omit private keys and are ignored by Git.

## Before mainnet

- Use a dedicated wallet with an amount you can afford to lose.
- Run a testnet order and validate the exchange response and cancellation behavior.
- Set conservative limits in `.env`; do not use unlimited values.
- Keep the executor running only for the period in which you intend to trade. This project has no autonomous signal generator or recurring task.

Hyperliquid’s API documentation lists mainnet and testnet endpoints and explains order semantics and schedule-cancel behavior: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
