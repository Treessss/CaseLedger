"""Explicit, audited replacement of original Shopify tracking with a 4PX CF label."""

from app import db
from app.models import CfTrackingWriteback, Order
from app.services import after_sale_service, shopify_graphql
from app.services.shopify_fulfillment_service import ORDER_FULFILLMENT_STATE, UPDATE_TRACKING, _mutate_once, _tracking_numbers
from app.utils.errors import ValidationError
from app.utils.security import current_user

ELIGIBLE_STATUSES = {"ready_to_bind", "already_bound", "original_order_only"}


def _source(payload):
    preview_data = after_sale_service.preview_fourpx(payload)
    reference = str(payload.get("customer_reference") or "").strip()
    tracking = str(payload.get("tracking_number") or "").strip()
    rows = [row for row in preview_data["rows"] if row["customer_reference"] == reference
            and row["tracking_number"] == tracking]
    if len(rows) != 1 or rows[0]["status"] not in ELIGIBLE_STATUSES or not rows[0].get("order_id"):
        raise ValidationError("CF 客户单号、原订单与 4PX 单号未唯一匹配，请刷新对单结果")
    row = rows[0]
    record = after_sale_service._get(row["after_sale_id"], lock=True) if row.get("after_sale_id") else None
    if record and (record.order_id != row["order_id"] or record.tracking_number not in (None, "", tracking)):
        raise ValidationError("售后绑定信息已变化，请重新核对")
    order = Order.query.filter_by(id=row["order_id"], shop_id=preview_data["shop_id"]).with_for_update().one()
    if order.warehouse_status not in ("shipped", "legacy_unverified"):
        raise ValidationError("CF 补发只能回填已出库或历史发货的原订单")
    return row, order, record


def _remote(order):
    try:
        result = shopify_graphql._execute(order.shop, ORDER_FULFILLMENT_STATE,
                                         {"id": shopify_graphql.gid_for_order(order.shopify_order_id)})
    except shopify_graphql.ShopifyGraphQLError as exc:
        raise ValidationError(str(exc)) from exc
    remote = result.get("order")
    if not isinstance(remote, dict) or remote.get("id") != shopify_graphql.gid_for_order(order.shopify_order_id):
        raise ValidationError("Shopify 原订单 ID 不匹配，已停止覆盖")
    fulfillments = remote.get("fulfillments") or []
    if len(fulfillments) >= 20:
        raise ValidationError("原订单履约记录过多，请在 Shopify 人工核对")
    active = [item for item in fulfillments if item.get("status") != "CANCELLED"]
    if not active:
        raise ValidationError("原订单没有有效的 Shopify 履约记录，不能覆盖补发物流")
    return active


def preview(payload):
    row, order, _record = _source(payload)
    fulfillments = _remote(order)
    return {
        **row, "fulfillments": [{"id": item["id"], "tracking_numbers": _tracking_numbers(item),
                                  "status": item.get("status")} for item in fulfillments],
        "notification_requested_on_confirm": True,
    }


def _cache_fulfillments(order, fulfillments):
    # Keep all original packages. Local warehouse labels and outbound state stay untouched.
    order.fulfillments = [{
        "id": str(item["id"]).rsplit("/", 1)[-1], "status": item.get("status"),
        "tracking_company": next((info.get("company") for info in item.get("trackingInfo") or []), None),
        "tracking_numbers": _tracking_numbers(item),
        "tracking_urls": [info.get("url") for info in item.get("trackingInfo") or [] if info.get("url")],
    } for item in fulfillments]


def writeback(payload):
    if payload.get("confirm_overwrite") is not True:
        raise ValidationError("请确认覆盖原订单运单并向客户发送通知")
    row, order, record = _source(payload)
    fulfillments = _remote(order)
    target_id = str(payload.get("fulfillment_id") or "")
    targets = [item for item in fulfillments if item["id"] == target_id]
    if len(targets) != 1:
        raise ValidationError("请选择原订单要覆盖的具体履约记录")
    target = targets[0]
    existing = _tracking_numbers(target)
    desired = row["tracking_number"]
    if existing == [desired]:
        if record:
            record.tracking_number = desired
        for attempt in CfTrackingWriteback.query.filter_by(
            order_id=order.id, fulfillment_id=target_id, tracking_number=desired, state="uncertain",
        ).all():
            attempt.state = "confirmed"
        _cache_fulfillments(order, fulfillments)
        db.session.commit()
        return {"status": "already_matched", "order_number": order.order_number,
                "tracking_number": desired, "customer_notification_requested": False}
    expected = payload.get("expected_tracking_numbers")
    if not isinstance(expected, list) or sorted(expected) != sorted(existing):
        raise ValidationError("原订单物流已发生变化，请重新预览并确认，禁止覆盖新单号")
    unresolved = CfTrackingWriteback.query.filter_by(
        order_id=order.id, fulfillment_id=target_id, tracking_number=desired,
        state="uncertain",
    ).first()
    if unresolved:
        raise ValidationError("上次回填结果未确认，请先在 Shopify 人工核对，禁止重复发送通知")
    if record:
        record.tracking_number = desired
    audit = CfTrackingWriteback(
        shop_id=order.shop_id, order_id=order.id, after_sale_id=row.get("after_sale_id"),
        customer_reference=row["customer_reference"], tracking_number=desired,
        fulfillment_id=target_id, previous_tracking_numbers=existing,
        state="pending", notification_requested=True,
        user_id=(current_user() or {}).get("user_id"),
    )
    db.session.add(audit)
    db.session.flush()
    try:
        updated = _mutate_once(order.shop, UPDATE_TRACKING, {
            "fulfillmentId": target_id,
            "trackingInfoInput": {"company": "4PX", "numbers": [desired]},
        }, "fulfillmentTrackingInfoUpdate")
    except ValidationError as exc:
        audit.state = "uncertain" if exc.extra.get("result_uncertain") else "failed"
        db.session.commit()
        raise
    if updated.get("id") != target_id or _tracking_numbers(updated) != [desired]:
        audit.state = "uncertain"
        db.session.commit()
        raise ValidationError("Shopify 未确认返回目标单号，请人工核对后再操作")
    audit.state = "confirmed"
    _cache_fulfillments(order, [updated if item["id"] == target_id else item for item in fulfillments])
    db.session.commit()
    return {"status": "updated", "order_number": order.order_number, "tracking_number": desired,
            "customer_notification_requested": True, "warehouse_status_unchanged": True}
