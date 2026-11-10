"""4PX predeclaration reconciliation and explicitly confirmed parcel binding."""

import json
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from app.models import Order, ShippingParcel, Shop
from app.models.shop import ShopCredentialError
from app.services.fourpx_client import FourPxClient, FourPxError
from app.services import shopify_graphql
from app.services.shopify_fulfillment_service import fetch_tracking_by_order_ids
from app.services.shipping_queue_service import _shop_id
from app.services.shipping_service import add_shipping_parcel
from app.utils.errors import ApiError, ValidationError

BEIJING = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def _rows(data):
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise ValidationError("4PX 返回数据格式异常，未绑定任何单号") from exc
    if not isinstance(data, list):
        raise ValidationError("4PX 返回的委托单列表格式异常，未绑定任何单号")
    return data


def _order_number(value):
    return str(value or "").strip().removeprefix("#")


def preview_predeclared(payload, *, client_factory=FourPxClient, selected_pairs=None):
    shop_id = _shop_id(payload.get("shop_id"))
    raw_day = str(payload.get("date") or "")
    try:
        day = date.fromisoformat(raw_day)
    except ValueError as exc:
        raise ValidationError("请选择有效的北京时间日期") from exc
    if day > datetime.now(BEIJING).date():
        raise ValidationError("不能查询未来日期")
    start = datetime.combine(day, time.min, BEIJING).astimezone(UTC)
    end = start + timedelta(days=1)
    shop = Shop.query.get(shop_id)
    if not shop.fourpx_app_key_encrypted or not shop.fourpx_app_secret_encrypted:
        raise ValidationError("该店铺尚未配置 4PX 凭据")
    try:
        app_key, app_secret = shop.get_fourpx_credentials()
    except ShopCredentialError as exc:
        raise ValidationError(str(exc)) from exc
    try:
        data = client_factory(app_key, app_secret, environment=shop.fourpx_environment).get_predeclared_created_between(
            int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        )
    except FourPxError as exc:
        raise ValidationError(str(exc)) from exc
    raw_rows = _rows(data)
    refs = [_order_number(item.get("consignment_info", {}).get("ref_no"))
            for item in raw_rows if isinstance(item, dict) and isinstance(item.get("consignment_info"), dict)]
    refs = [ref for ref in refs if ref]
    candidates = set(refs) | {f"#{ref}" for ref in refs}
    orders = Order.query.filter(Order.shop_id == shop_id, Order.order_number.in_(candidates)).all() if candidates else []
    by_number = {}
    for order in orders:
        number = _order_number(order.order_number)
        if number in refs:
            by_number.setdefault(number, []).append(order)
    tracking_numbers = [str(item.get("consignment_info", {}).get("4px_tracking_no") or "").strip()
                        for item in raw_rows if isinstance(item, dict) and isinstance(item.get("consignment_info"), dict)]
    existing = {parcel.label_number: parcel for parcel in ShippingParcel.query.filter(
        ShippingParcel.shop_id == shop_id, ShippingParcel.label_number.in_(tracking_numbers)
    ).all()} if tracking_numbers else {}
    ref_counts = Counter(refs)
    order_ids = [order.id for matches in by_number.values() for order in matches]
    parcel_counts = Counter(parcel.order_id for parcel in ShippingParcel.query.filter(
        ShippingParcel.order_id.in_(order_ids)
    ).all()) if order_ids else Counter()
    already_bound_in_response = Counter(
        existing[number].order_id for number in tracking_numbers if number in existing
    )
    tracking_counts = Counter(tracking_numbers)
    result = []
    for item in raw_rows:
        info = item.get("consignment_info") if isinstance(item, dict) else None
        if not isinstance(info, dict):
            result.append({"customer_reference": None, "tracking_number": None, "status": "invalid_response"})
            continue
        ref = _order_number(info.get("ref_no"))
        tracking = str(info.get("4px_tracking_no") or "").strip()
        matches = by_number.get(ref, [])
        existing_parcel = existing.get(tracking)
        if not ref or not tracking:
            status = "missing_number"
        elif tracking_counts[tracking] > 1:
            status = "duplicate_4px"
        elif len(matches) > 1:
            status = "ambiguous_order"
        elif not matches:
            status = "order_not_found"
        elif parcel_counts[matches[0].id] + ref_counts[ref] - already_bound_in_response[matches[0].id] > matches[0].expected_parcel_count:
            status = "parcel_count_mismatch"
        elif existing_parcel and existing_parcel.order_id != matches[0].id:
            status = "label_conflict"
        elif existing_parcel:
            # 已绑定过的单（含已出库/已完成 C/F）显示「已绑定」，不算异常
            status = "already_bound"
        elif info.get("consignment_status") not in ("P", "V", "H", "C", "F"):
            status = "not_predeclared"
        elif matches[0].cancelled_at or matches[0].financial_status not in ("paid", "partially_paid", "partially_refunded"):
            status = "ineligible_order"
        elif matches[0].warehouse_status != "pending":
            status = "not_pending"
        else:
            status = "ready_to_bind"
        result.append({
            "customer_reference": ref or None,
            "tracking_number": tracking or None,
            "consignment_status": info.get("consignment_status"),
            "order_id": matches[0].id if len(matches) == 1 else None,
            "order_number": matches[0].order_number if len(matches) == 1 else None,
            "status": status,
        })
    actionable = [row for row in result if row["status"] in ("ready_to_bind", "already_bound")
                  and (selected_pairs is None or (row["customer_reference"], row["tracking_number"]) in selected_pairs)]
    if actionable:
        matched_orders = {row["order_id"]: next(order for order in orders if order.id == row["order_id"])
                          for row in actionable}
        try:
            tracking_by_gid = fetch_tracking_by_order_ids(
                shop, [order.shopify_order_id for order in matched_orders.values()]
            )
        except shopify_graphql.ShopifyGraphQLError as exc:
            raise ValidationError(f"无法实时核对 Shopify 履约，已禁止回填：{exc}") from exc
        for row in actionable:
            order = matched_orders[row["order_id"]]
            remote_numbers = tracking_by_gid[shopify_graphql.gid_for_order(order.shopify_order_id)]
            if row["tracking_number"] in remote_numbers:
                row["status"] = "shopify_already_fulfilled"
            elif remote_numbers:
                row["status"] = "shopify_tracking_conflict"
    return {
        "date": raw_day, "shop_id": shop_id, "total": len(result),
        "ready_to_bind": sum(row["status"] == "ready_to_bind" for row in result),
        "exceptions": sum(row["status"] not in ("ready_to_bind", "already_bound", "shopify_already_fulfilled")
                          for row in result),
        "rows": result,
    }


def bind_predeclared(payload, *, client_factory=FourPxClient):
    """Bind one explicitly selected pair after a fresh remote lookup."""
    ref = _order_number(payload.get("customer_reference"))
    tracking = str(payload.get("tracking_number") or "").strip()
    if not ref or not tracking:
        raise ValidationError("请选择完整的客户单号与 4PX 单号")
    result = preview_predeclared(payload, client_factory=client_factory, selected_pairs={(ref, tracking)})
    matching = [row for row in result["rows"] if row["customer_reference"] == ref
                and row["tracking_number"] == tracking]
    if len(matching) != 1 or matching[0]["status"] != "ready_to_bind":
        raise ValidationError("4PX 对单结果已变化或存在异常，请刷新预览后核对")
    saved = add_shipping_parcel(matching[0]["order_id"], {"shipping_label_number": tracking}, source="4px")
    from app.services.fourpx_barcode_sync_service import enqueue_barcode_sync

    enqueue_barcode_sync(result["shop_id"], [tracking])
    return {"order_id": matching[0]["order_id"], "order_number": matching[0]["order_number"],
            "tracking_number": tracking, "shipping": saved}


def bind_and_writeback(payload, *, client_factory=FourPxClient):
    """One explicit click. A successful local binding survives Shopify failure."""
    from app.services.shopify_fulfillment_service import writeback_tracking

    bound = bind_predeclared(payload, client_factory=client_factory)
    shipping = bound["shipping"]
    if len(shipping["parcels"]) < shipping["expected_parcel_count"]:
        return {**bound, "shopify_status": "awaiting_parcels"}
    try:
        writeback = writeback_tracking(bound["order_id"])
    except ApiError as exc:
        return {**bound, "shopify_status": "failed", "shopify_error": exc.message}
    except Exception:
        current_app.logger.exception("Shopify writeback failed after 4PX pair was saved")
        return {**bound, "shopify_status": "failed",
                "shopify_error": "回填结果不确定；请先核对 Shopify 订单，再从订单详情重试"}
    return {**bound, "shopify_status": writeback["status"], "shopify_writeback": writeback}


def batch_bind_and_writeback(payload, *, client_factory=FourPxClient):
    """Explicitly selected 4PX pairs: refresh once, bind safely, fulfill once per complete order."""
    return _batch_bind(payload, client_factory=client_factory, bind_only=False)


def batch_bind_predeclared(payload, *, client_factory=FourPxClient):
    """Bind selected 4PX pairs; let the caller write back each ready order separately."""
    return _batch_bind(payload, client_factory=client_factory, bind_only=True)


def _batch_bind(payload, *, client_factory, bind_only):
    from app.services.shopify_fulfillment_service import writeback_tracking

    selected = payload.get("pairs")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 50:
        raise ValidationError("每批请选择 1–50 条 4PX 对单记录")
    pairs = []
    seen = set()
    for item in selected:
        if not isinstance(item, dict):
            raise ValidationError("批量对单内容格式错误")
        ref = _order_number(item.get("customer_reference"))
        tracking = str(item.get("tracking_number") or "").strip()
        if not ref or not tracking or len(ref) > 120 or len(tracking) > 120:
            raise ValidationError("批量对单中有缺失或过长的单号")
        pair = (ref, tracking)
        if pair in seen:
            raise ValidationError("批量对单中有重复的客户单号与 4PX 单号")
        seen.add(pair)
        pairs.append(pair)

    fresh = preview_predeclared(payload, client_factory=client_factory, selected_pairs=set(pairs))
    by_pair = {}
    for row in fresh["rows"]:
        by_pair.setdefault((row["customer_reference"], row["tracking_number"]), []).append(row)
    groups = {}
    skipped = []
    for ref, tracking in pairs:
        matching = by_pair.get((ref, tracking), [])
        if len(matching) != 1 or matching[0]["status"] not in ("ready_to_bind", "already_bound"):
            skipped.append({"customer_reference": ref, "tracking_number": tracking,
                            "reason": matching[0]["status"] if len(matching) == 1 else "pair_not_unique"})
            continue
        row = matching[0]
        group = groups.setdefault(row["order_id"], {"order_id": row["order_id"],
            "order_number": row["order_number"], "pairs": [], "newly_bound": 0, "already_bound": 0,
            "shopify_status": None, "shopify_error": None})
        group["pairs"].append(row)

    results = []
    for group in groups.values():
        for row in group["pairs"]:
            if row["status"] == "already_bound":
                group["already_bound"] += 1
                continue
            try:
                add_shipping_parcel(group["order_id"], {"shipping_label_number": row["tracking_number"]}, source="4px")
                group["newly_bound"] += 1
            except ApiError as exc:
                skipped.append({"customer_reference": row["customer_reference"],
                                "tracking_number": row["tracking_number"], "reason": exc.message})
                group["shopify_status"] = "failed"
                group["shopify_error"] = "部分包裹绑定失败，请核对后从订单详情回填"
        if group["shopify_status"] != "failed":
            order = Order.query.get(group["order_id"])
            count = ShippingParcel.query.filter_by(order_id=order.id).count()
            if count < order.expected_parcel_count:
                group["shopify_status"] = "awaiting_parcels"
            elif bind_only:
                group["shopify_status"] = "ready_to_writeback"
            else:
                try:
                    writeback = writeback_tracking(order.id)
                    group["shopify_status"] = writeback["status"]
                except ApiError as exc:
                    group["shopify_status"] = "failed"
                    group["shopify_error"] = exc.message
                except Exception:
                    current_app.logger.exception("Batch Shopify writeback failed after 4PX pairs were saved")
                    group["shopify_status"] = "failed"
                    group["shopify_error"] = "结果不确定；请先核对 Shopify 订单，再从订单详情重试"
        group.pop("pairs")
        results.append(group)
    summary = {"selected_pairs": len(pairs), "newly_bound": sum(row["newly_bound"] for row in results),
               "already_bound": sum(row["already_bound"] for row in results), "skipped_pairs": len(skipped),
               "updated_orders": sum(row["shopify_status"] == "updated" for row in results),
               "already_fulfilled_orders": sum(row["shopify_status"] == "already_fulfilled" for row in results),
               "ready_to_writeback_orders": sum(row["shopify_status"] == "ready_to_writeback" for row in results),
               "awaiting_parcels_orders": sum(row["shopify_status"] == "awaiting_parcels" for row in results),
               "failed_orders": sum(row["shopify_status"] == "failed" for row in results)}
    from app.services.fourpx_barcode_sync_service import enqueue_barcode_sync

    enqueue_barcode_sync(fresh["shop_id"], [tracking for _, tracking in pairs])
    return {"shop_id": fresh["shop_id"], "date": fresh["date"], "summary": summary,
            "orders": results, "skipped": skipped}
