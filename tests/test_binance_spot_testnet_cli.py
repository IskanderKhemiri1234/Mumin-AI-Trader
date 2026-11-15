from __future__ import annotations

import pytest

from scripts.binance_spot_testnet import build_parser


def test_submit_cli_requires_explicit_testnet_only_confirmation() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "submit", "--symbol", "BTCUSDT", "--side", "BUY",
            "--quantity", "0.00010", "--price", "50000",
            "--client-order-id", "cli-test-1",
        ])

    args = parser.parse_args([
        "submit", "--symbol", "BTCUSDT", "--side", "BUY",
        "--quantity", "0.00010", "--price", "50000",
        "--client-order-id", "cli-test-1", "--confirm-testnet-order", "TESTNET_ONLY",
    ])
    assert args.command == "submit"
    assert args.confirm_testnet_order == "TESTNET_ONLY"


def test_cancel_cli_requires_explicit_testnet_only_confirmation() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "cancel", "--symbol", "BTCUSDT", "--client-order-id", "cli-test-1",
        ])

    args = parser.parse_args([
        "cancel", "--symbol", "BTCUSDT", "--client-order-id", "cli-test-1",
        "--confirm-testnet-cancel", "TESTNET_ONLY",
    ])
    assert args.command == "cancel"
    assert args.confirm_testnet_cancel == "TESTNET_ONLY"
