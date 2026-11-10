import pytest

from app.services.shopify_fulfillment_service import _active_fulfillment_orders
from app.utils.errors import ValidationError


def record(status, actions=()):
    return {"id": status, "status": status, "supportedActions": [{"action": action} for action in actions]}


def test_closed_history_does_not_block_12004_or_enter_writeback():
    active = record("OPEN", ["CREATE_FULFILLMENT"])
    result = _active_fulfillment_orders({"nodes": [record("CLOSED"), active, record("CANCELLED")]})
    assert result == [active]


@pytest.mark.parametrize("status", ["ON_HOLD", "SCHEDULED", "IN_PROGRESS", None])
def test_unavailable_outstanding_order_is_not_silently_skipped(status):
    with pytest.raises(ValidationError):
        _active_fulfillment_orders({"nodes": [record("OPEN", ["CREATE_FULFILLMENT"]), record(status)]})


@pytest.mark.parametrize("nodes", [[], [record("CLOSED")], [record("CANCELLED")], [record("OPEN")]])
def test_empty_terminal_only_or_no_permission_still_blocks(nodes):
    with pytest.raises(ValidationError):
        _active_fulfillment_orders({"nodes": nodes})


def test_incomplete_pagination_still_blocks():
    with pytest.raises(ValidationError):
        _active_fulfillment_orders({"pageInfo": {"hasNextPage": True}, "nodes": [record("OPEN", ["CREATE_FULFILLMENT"])]})
