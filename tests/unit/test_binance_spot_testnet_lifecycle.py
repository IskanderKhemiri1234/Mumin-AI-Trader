from decimal import Decimal

from scripts.binance_spot_testnet_lifecycle import (
    child_result,
    json_safe,
    round_down_to_step,
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
