"""Explicit, shop-scoped Shopify tracking writeback; never marks warehouse outbound."""

import requests

from app import db
from app.models import Order, ShippingParcel
from app.services import shopify_graphql
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import can_access_shop


ORDER_FULFILLMENT_STATE = """
query OrderFulfillmentState($id: ID!) {
  order(id: $id) {
    id name
    fulfillments(first: 20) { id status trackingInfo { company number url } }
    fulfillmentOrders(first: 100) {
      pageInfo { hasNextPage }
      nodes {
        id status
        assignedLocation { location { id } }
        supportedActions { action }
      }
    }
  }
}
"""

ORDERS_TRACKING_STATE = """
query OrdersTrackingState($ids: [ID!]!) {
  nodes(ids: $ids) {
    id
    ... on Order {
      fulfillments(first: 20) { status trackingInfo { number } }
    }
  }
}
"""


def fetch_tracking_by_order_ids(shop, order_ids):
    """Read live Shopify tracking in bounded GraphQL batches; never trust local sync state here."""
    ids = list(dict.fromkeys(shopify_graphql.gid_for_order(value) for value in order_ids))
    result = {}
    for offset in range(0, len(ids), 50):
        batch = ids[offset:offset + 50]
        data = shopify_graphql._execute(shop, ORDERS_TRACKING_STATE, {"ids": batch})
        nodes = data.get("nodes")
        if not isinstance(nodes, list) or len(nodes) != len(batch):
            raise shopify_graphql.ShopifyGraphQLError("Shopify 履约核对结果不完整，已禁止回填")
        for expected, node in zip(batch, nodes):
            if not isinstance(node, dict) or node.get("id") != expected or not isinstance(node.get("fulfillments"), list):
                raise shopify_graphql.ShopifyGraphQLError("Shopify 订单履约信息缺失，已禁止回填")
            numbers = {
                str(info["number"]).strip()
                for fulfillment in node["fulfillments"]
                if fulfillment.get("status") != "CANCELLED"
                for info in fulfillment.get("trackingInfo") or []
                if isinstance(info, dict) and info.get("number")
            }
            result[expected] = numbers
    return result

CREATE_FULFILLMENT = """
mutation CreateTrackedFulfillment($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status trackingInfo { company number url } }
    userErrors { field message }
  }
}
"""

UPDATE_TRACKING = """
mutation UpdateFulfillmentTracking($fulfillmentId: ID!, $trackingInfoInput: FulfillmentTrackingInput!) {
  fulfillmentTrackingInfoUpdate(fulfillmentId: $fulfillmentId, trackingInfoInput: $trackingInfoInput,
    notifyCustomer: true) {
    fulfillment { id status trackingInfo { company number url } }
    userErrors { field message }
  }
}
"""


def _mutate_once(shop, query, variables, key):
    """No automatic retry: a timeout could mean Shopify already applied a write."""
    try:
        token = shopify_graphql._token_for(shop)
    except shopify_graphql.ShopifyGraphQLError as exc:
        raise ValidationError(str(exc)) from exc
    try:
        response = requests.post(
            shopify_graphql.endpoint_for(shop),
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json", "Accept": "application/json"},
            json={"query": query, "variables": variables}, timeout=30,
        )
    except requests.RequestException:
        raise ValidationError("Shopify 回填结果不确定，请先核对 Shopify 订单，禁止盲目重试", extra={"result_uncertain": True}) from None
    if response.status_code != 200:
        raise ValidationError(f"Shopify 未确认回填成功（HTTP {response.status_code}），请核对订单", extra={"result_uncertain": True})
    try:
        payload = response.json()
    except ValueError:
        raise ValidationError("Shopify 回填响应无法解析，请核对 Shopify 订单", extra={"result_uncertain": True}) from None
    if payload.get("errors"):
        raise ValidationError("Shopify 拒绝回填，请检查应用履约权限与订单状态")
    result = (payload.get("data") or {}).get(key) or {}
    errors = result.get("userErrors") or []
    if errors:
        raise ValidationError("Shopify 回填失败：" + "；".join(str(item.get("message") or "未知错误") for item in errors)[:300])
    fulfillment = result.get("fulfillment")
    if not isinstance(fulfillment, dict) or not fulfillment.get("id"):
        raise ValidationError("Shopify 未返回履约记录，请核对订单后再操作", extra={"result_uncertain": True})
    return fulfillment


def _tracking_numbers(fulfillment):
    return [str(item.get("number")) for item in fulfillment.get("trackingInfo") or [] if item.get("number")]


def _active_fulfillment_orders(connection):
    """Ignore terminal history without bypassing holds on outstanding items."""
    if (connection.get("pageInfo") or {}).get("hasNextPage"):
        raise ValidationError("Shopify 待履约单超过 100 条，需人工处理")
    orders = [
        item for item in connection.get("nodes") or []
        if item.get("status") not in {"CLOSED", "CANCELLED"}
    ]
    if not orders:
        raise ValidationError("Shopify 没有可回填的有效待履约单，请核对订单")
    if any(
        item.get("status") != "OPEN" or "CREATE_FULFILLMENT" not in {
            action.get("action") for action in item.get("supportedActions") or []
        } for item in orders
    ):
        raise ValidationError("Shopify 有待履约商品被暂停或不可创建履约，请核对订单状态与应用权限")
    return orders


def _save_local_order(order, fulfillment):
    order.fulfillment_status = "fulfilled"
    order.fulfillments = [{
        "id": str(fulfillment["id"]).rsplit("/", 1)[-1],
        "status": fulfillment.get("status"),
        "tracking_company": "4PX",
        "tracking_numbers": _tracking_numbers(fulfillment),
        "tracking_urls": [item.get("url") for item in fulfillment.get("trackingInfo") or [] if item.get("url")],
    }]
    db.session.commit()


def writeback_tracking(order_id):
    order = Order.query.filter_by(id=order_id).with_for_update().one_or_none()
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权操作该店铺订单")
    if order.warehouse_status.startswith("legacy_"):
        raise ValidationError("历史订单出库状态待核实，不能自动回填 Shopify")
    if order.cancelled_at or order.financial_status not in ("paid", "partially_paid", "partially_refunded"):
        raise ValidationError("已取消或不可采购的订单不能回填 Shopify 发货")
    parcels = ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).all()
    if len(parcels) != order.expected_parcel_count or not parcels:
        raise ValidationError("面单尚未齐全，不能回填 Shopify")
    if any(parcel.source != "4px" for parcel in parcels):
        raise ValidationError("请先从 4PX 对单并绑定全部包裹，不能将手工标签冒充 4PX 回填")
    labels = [parcel.label_number for parcel in parcels]
    try:
        data = shopify_graphql._execute(
            order.shop, ORDER_FULFILLMENT_STATE, {"id": shopify_graphql.gid_for_order(order.shopify_order_id)}
        )
    except shopify_graphql.ShopifyGraphQLError as exc:
        raise ValidationError(str(exc)) from exc
    remote = data.get("order")
    if not isinstance(remote, dict) or remote.get("id") != shopify_graphql.gid_for_order(order.shopify_order_id):
        raise ValidationError("Shopify 订单 ID 不匹配，已停止回填")
    fulfillments = [item for item in remote.get("fulfillments") or [] if item.get("status") != "CANCELLED"]
    if fulfillments:
        if len(fulfillments) != 1:
            raise ValidationError("Shopify 有多条履约记录，请人工核对后回填")
        current = fulfillments[0]
        existing = _tracking_numbers(current)
        if set(existing) == set(labels) and len(existing) == len(labels):
            _save_local_order(order, current)
            return {"order_id": order.id, "order_number": order.order_number, "status": "already_fulfilled",
                    "tracking_numbers": labels, "warehouse_status": order.warehouse_status,
                    "customer_notification_requested": False}
        if existing:
            raise ValidationError("Shopify 已有不同运单号，禁止自动覆盖，请人工核对")
        fulfillment = _mutate_once(order.shop, UPDATE_TRACKING, {
            "fulfillmentId": current["id"],
            "trackingInfoInput": {"company": "4PX", "numbers": labels},
        }, "fulfillmentTrackingInfoUpdate")
    else:
        connection = remote.get("fulfillmentOrders") or {}
        fulfillment_orders = _active_fulfillment_orders(connection)
        locations = {((item.get("assignedLocation") or {}).get("location") or {}).get("id")
                     for item in fulfillment_orders}
        if None in locations or len(locations) != 1:
            raise ValidationError("Shopify 待履约单分属不同地点，需人工拆单处理")
        fulfillment = _mutate_once(order.shop, CREATE_FULFILLMENT, {
            "fulfillment": {
                "notifyCustomer": True,
                "trackingInfo": {"company": "4PX", "numbers": labels},
                "lineItemsByFulfillmentOrder": [
                    {"fulfillmentOrderId": item["id"]} for item in fulfillment_orders
                ],
            },
        }, "fulfillmentCreate")
    _save_local_order(order, fulfillment)
    return {"order_id": order.id, "order_number": order.order_number, "status": "updated",
            "tracking_numbers": labels, "warehouse_status": order.warehouse_status,
            "customer_notification_requested": True}
