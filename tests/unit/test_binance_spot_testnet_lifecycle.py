from decimal import Decimal

import pytest

from scripts.binance_spot_testnet_lifecycle import (
    child_result,
    commission_asset_mismatches,
    json_safe,
    opoco_capability,
    opoco_result,
    round_down_to_step,
)
from mumin_trader.exchanges.binance.spot_testnet_oco import (
    SQLiteOCOOrderStore,
    SpotExecutionError,
    validate_fixed_exit_risk,
    validate_oco_leg_notionals,
)
from mumin_trader.exchanges.binance.spot_testnet_opoco import BinanceSpotTestnetOPOCO


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


def _opoco_response(working_status="FILLED", above_status="NEW", below_status="NEW",
                    list_status="EXECUTING", working_executed="0.001"):
    return {
        "orderListId": 9001,
        "listClientOrderId": "mumin-opoco-list",
        "listOrderStatus": list_status,
        "orders": [
            {"orderId": 901, "clientOrderId": "mumin-opoco-working"},
            {"orderId": 902, "clientOrderId": "mumin-opoco-above"},
            {"orderId": 903, "clientOrderId": "mumin-opoco-below"},
        ],
        "orderReports": [
            {
                "orderId": 901, "clientOrderId": "mumin-opoco-working",
                "type": "LIMIT", "side": "BUY", "status": working_status,
                "executedQty": working_executed, "origQty": "0.001",
            },
            {
                "orderId": 902, "clientOrderId": "mumin-opoco-above",
                "type": "LIMIT_MAKER", "side": "SELL", "status": above_status,
                "executedQty": "0", "origQty": "0.001",
            },
            {
                "orderId": 903, "clientOrderId": "mumin-opoco-below",
                "type": "STOP_LOSS_LIMIT", "side": "SELL", "status": below_status,
                "executedQty": "0", "origQty": "0.001",
            },
        ],
    }


def test_opoco_working_fill_activates_both_pending_protection_legs():
    result = BinanceSpotTestnetOPOCO._state(_opoco_response())
    assert result == "PROTECTION_ARMED"


def test_opoco_partial_working_fill_requires_manual_review():
    result = BinanceSpotTestnetOPOCO._state(
        _opoco_response(working_status="PARTIALLY_FILLED", working_executed="0.0004")
    )
    assert result == "PARTIAL_FILL_REVIEW"


def test_opoco_proves_filled_protective_leg_and_canceled_sibling():
    response = _opoco_response(
        above_status="FILLED",
        below_status="CANCELED",
        list_status="ALL_DONE",
    )
    result = BinanceSpotTestnetOPOCO._state(response)
    assert result == "COMPLETE"


def test_opoco_does_not_claim_protection_when_pending_leg_is_missing():
    response = _opoco_response()
    response["orderReports"] = response["orderReports"][:2]
    assert BinanceSpotTestnetOPOCO._state(response) == "UNKNOWN"


def test_opoco_order_list_store_persists_all_three_distinct_child_orders(tmp_path):
    store = SQLiteOCOOrderStore(str(tmp_path / "opoco.sqlite3"))
    request = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.001",
        "listClientOrderId": "mumin-opoco-list",
        "workingClientOrderId": "mumin-opoco-working",
        "pendingAboveClientOrderId": "mumin-opoco-above",
        "pendingBelowClientOrderId": "mumin-opoco-below",
    }
    store.register(request)
    assert store.claim("mumin-opoco-list") is True
    response = _opoco_response()
    store.update("mumin-opoco-list", state="PROTECTION_ARMED", response=response)

    saved = store.get("mumin-opoco-list")
    assert saved["working_order_id"] == "901"
    assert saved["above_order_id"] == "902"
    assert saved["below_order_id"] == "903"
    assert store.child_client_order_ids() == {
        "mumin-opoco-working",
        "mumin-opoco-above",
        "mumin-opoco-below",
    }


def test_opoco_result_proves_three_order_lifecycle_and_mutual_cancel():
    response = _opoco_response(
        above_status="FILLED",
        below_status="CANCELED",
        list_status="ALL_DONE",
    )
    result = opoco_result(response)

    assert result["mutual_cancel_proven"] is True
    assert result["working_order"]["side"] == "BUY"
    assert len(result["protective_children"]) == 2
    assert len({item["orderId"] for item in result["children"]}) == 3


def test_opoco_result_does_not_claim_protection_for_partial_working_order():
    response = _opoco_response(
        working_status="PARTIALLY_FILLED",
        working_executed="0.0004",
        above_status="PENDING_NEW",
        below_status="PENDING_NEW",
    )
    assert opoco_result(response)["mutual_cancel_proven"] is False


def test_fixed_exit_risk_enforces_approved_stop_and_take_profit_percentages():
    result = validate_fixed_exit_risk("50000", "50625", "49750")
    assert Decimal(result["stop_loss_percent"]) == Decimal("0.50")
    assert Decimal(result["take_profit_percent"]) == Decimal("1.25")


def test_fixed_exit_risk_rejects_unapproved_exit_percentages():
    with pytest.raises(SpotExecutionError):
        validate_fixed_exit_risk("50000", "51000", "49750")
