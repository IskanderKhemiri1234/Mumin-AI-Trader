from decimal import Decimal

import pytest

from scripts.binance_spot_testnet_lifecycle import (
    child_result,
    commission_asset_mismatches,
    json_safe,
    opoco_capability,
    round_down_to_step,
)
from mumin_trader.exchanges.binance.spot_testnet_oco import (
    SpotExecutionError,
    validate_oco_leg_notionals,
)


def test_child_result_proves_one_filled_leg_and_canceled_sibling():
    result = child_result({
        "orderListId": 77,
        "listClientOrderId": "mumin-oco-proof",
        "listOrderStatus": "ALL_DONE",
        "orderReports": [
            {"orderId": 701, "clientOrderId": "tp-701", "status": "FILLED",
             "origQty": "0.001", "executedQty": "0.001"},
            {"orderId": 702, "clientOrderId": "sl-702", "status": "CANCELED",
             "origQty": "0.001", "executedQty": "0"},
        ],
    })
    assert result["mutual_cancel_proven"] is True
    assert result["orderListId"] == "77"
    assert {item["orderId"] for item in result["children"]} == {"701", "702"}


def test_child_result_does_not_claim_mutual_cancel_for_manual_cancel():
    result = child_result({
        "orderListId": 78,
        "listOrderStatus": "ALL_DONE",
        "orderReports": [
            {"orderId": 711, "status": "CANCELED", "executedQty": "0"},
            {"orderId": 712, "status": "CANCELED", "executedQty": "0"},
        ],
    })
    assert result["mutual_cancel_proven"] is False


def test_round_down_quantity_never_exceeds_exchange_net_fill():
    assert round_down_to_step(Decimal("0.00014990"), Decimal("0.00001")) == Decimal("0.00014")


def test_evidence_sanitizer_removes_secret_fields_recursively():
    result = json_safe({
        "order": {"orderId": 12, "signature": "hidden"},
        "apiKey": "hidden-too",
        "fills": [{"commission": "0.01", "api_secret": "also-hidden"}],
    })
    assert result == {
        "order": {"orderId": 12},
        "fills": [{"commission": "0.01"}],
    }



def test_child_result_requires_terminal_list_and_two_distinct_exchange_ids():
    active = child_result({
        "orderListId": 79,
        "listOrderStatus": "EXECUTING",
        "orderReports": [
            {"orderId": 721, "status": "FILLED", "executedQty": "0.001"},
            {"orderId": 722, "status": "CANCELED", "executedQty": "0"},
        ],
    })
    missing_id = child_result({
        "orderListId": 80,
        "listOrderStatus": "ALL_DONE",
        "orderReports": [
            {"orderId": 0, "status": "FILLED", "executedQty": "0.001"},
            {"orderId": 0, "status": "CANCELED", "executedQty": "0"},
        ],
    })
    assert active["mutual_cancel_proven"] is False
    assert missing_id["mutual_cancel_proven"] is False



def test_oco_leg_minimum_is_checked_independently_for_take_profit_and_stop():
    with pytest.raises(SpotExecutionError, match="stop leg"):
        validate_oco_leg_notionals(
            Decimal("0.001"),
            Decimal("60000"),
            Decimal("4900"),
            Decimal("5"),
        )
    with pytest.raises(SpotExecutionError, match="take-profit leg"):
        validate_oco_leg_notionals(
            Decimal("0.001"),
            Decimal("4900"),
            Decimal("60000"),
            Decimal("5"),
        )


def test_oco_leg_maximum_is_checked_independently():
    with pytest.raises(SpotExecutionError, match="stop leg"):
        validate_oco_leg_notionals(
            Decimal("1"),
            Decimal("100"),
            Decimal("150"),
            Decimal("5"),
            Decimal("120"),
        )


def test_fee_asset_mismatch_is_detected_without_exposing_fill_secrets():
    result = commission_asset_mismatches([
        {"trade_id": 1, "commission": "0.00001", "commission_asset": "BNB"},
        {"trade_id": 2, "commission": "0.00002", "commission_asset": "BTC"},
        {"trade_id": 3, "commission": "0", "commission_asset": "USDT"},
    ])
    assert result == [{
        "trade_id": "2",
        "commission": "0.00002",
        "commission_asset": "BTC",
        "reason": "unexpected_commission_asset",
    }]


def test_fee_asset_mismatch_rejects_invalid_negative_commission():
    result = commission_asset_mismatches([
        {"trade_id": 4, "commission": "-0.1", "commission_asset": "BNB"},
    ])
    assert result[0]["reason"] == "invalid_commission_amount"


def test_fee_asset_mismatch_rejects_non_decimal_commission():
    result = commission_asset_mismatches([
        {"trade_id": 5, "commission": "not-a-number", "commission_asset": "BNB"},
    ])
    assert result[0]["reason"] == "invalid_commission_amount"



def test_opoco_capability_requires_all_exchange_flags():
    result = opoco_capability({
        "otoAllowed": True,
        "opoAllowed": True,
        "ocoAllowed": True,
    })
    assert result["supported_by_exchange_info"] is True
    assert result["evidence_scope"] == "EXCHANGE_INFO_CAPABILITY_ONLY"


def test_opoco_capability_fails_closed_when_any_flag_is_missing_or_false():
    for metadata in (
        {"otoAllowed": True, "opoAllowed": True},
        {"otoAllowed": True, "opoAllowed": True, "ocoAllowed": False},
        {"otoAllowed": "true", "opoAllowed": True, "ocoAllowed": True},
    ):
        assert opoco_capability(metadata)["supported_by_exchange_info"] is False
