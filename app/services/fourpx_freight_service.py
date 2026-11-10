"""4PX 实际出账费用同步：计费重 + 费用明细 + 总费用（只读 4PX）。"""

import json
from datetime import datetime

from app import db
from app.models import FourPxFreight, Order, Shop, ShippingParcel
from app.services import fourpx_client
from app.services.fourpx_client import FourPxClient, FourPxError
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import can_access_shop


def _parse_payload(raw):
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise ValidationError("4PX 费用响应格式异常") from None
    if not isinstance(data, dict) or not data:
        raise ValidationError("4PX 尚未出账（可能还没揽收/称重），请稍后再同步")
    subs = data.get("subs") or []
    if not isinstance(subs, list):
        subs = []
    try:
        total = float(data.get("total_fee")) if data.get("total_fee") not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    try:
        weight = float(data.get("charge_weight")) if data.get("charge_weight") not in (None, "") else None
    except (TypeError, ValueError):
        weight = None
    if total is None:
        raise ValidationError("4PX 费用响应缺少总费用")
    return {
        "tracking_number": str(data.get("4px_tracking_no") or "").strip(),
        "ref_no": str(data.get("ref_no") or "").strip(),
        "charge_weight_kg": weight,
        "currency": str(data.get("currency") or "CNY").strip(),
        "total_fee": total,
        "subs": [
            {
                "fee_name": str(row.get("fee_name") or ""),
                "fee_amount": row.get("fee_amount"),
                "currency": str(row.get("currency") or data.get("currency") or "CNY"),
            }
            for row in subs if isinstance(row, dict)
        ],
        "billed_at": _parse_time(data.get("create_time")),
        "raw": data,
    }


def _parse_time(value):
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], pattern)
        except ValueError:
            continue
    return None


def _get_shop_credentials(shop_id):
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise NotFoundError("店铺不存在")
    try:
        return shop, shop.get_fourpx_credentials()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(str(exc)) from exc


def sync_order_freight(order_id, *, client_factory=FourPxClient):
    """同步单个订单的 4PX 实际费用；未出账时返回明确提示。"""
    order = db.session.get(Order, order_id)
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权操作该店铺订单")
    parcel = ShippingParcel.query.filter_by(order_id=order.id).order_by(ShippingParcel.id).first()
    if parcel is None:
        raise ValidationError("订单还没有 4PX 包裹，无法查询费用")
    shop, (app_key, app_secret) = _get_shop_credentials(order.shop_id)
    client = client_factory(app_key, app_secret, environment=shop.fourpx_environment)
    ref_no = str(order.order_number).lstrip("#")
    try:
        raw = client.get_freight(ref_no)
    except FourPxError as exc:
        detail = getattr(exc, "details", None)
        raise ValidationError(detail or "4PX 尚未出账（可能还没揽收/称重），请稍后再同步") from exc
    parsed = _parse_payload(raw)
    row = FourPxFreight.query.filter_by(order_id=order.id).one_or_none()
    if row is None:
        row = FourPxFreight(shop_id=order.shop_id, order_id=order.id)
        db.session.add(row)
    row.tracking_number = parsed["tracking_number"] or parcel.label_number
    row.ref_no = parsed["ref_no"] or ref_no
    row.charge_weight_kg = parsed["charge_weight_kg"]
    row.currency = parsed["currency"]
    row.total_fee = parsed["total_fee"]
    row.subs = parsed["subs"]
    row.billed_at = parsed["billed_at"]
    row.raw = parsed["raw"]
    row.synced_at = datetime.utcnow()
    db.session.commit()
    return row.to_dict()


def sync_order_freights(payload, *, client_factory=FourPxClient):
    """批量同步（最多 50 单）；返回每单结果，失败的不会中断其它订单。"""
    raw_ids = payload.get("order_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValidationError("请选择要同步费用的订单")
    order_ids = []
    for value in raw_ids:
        try:
            order_id = int(value)
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in order_ids:
            order_ids.append(order_id)
    if not order_ids:
        raise ValidationError("请选择要同步费用的订单")
    if len(order_ids) > 50:
        raise ValidationError("一次最多同步 50 单，请分批处理")
    results = []
    synced = failed = 0
    for order_id in order_ids:
        try:
            row = sync_order_freight(order_id, client_factory=client_factory)
            synced += 1
            results.append({"order_id": order_id, "status": "synced", **row})
        except Exception as exc:  # noqa: BLE001 - 单笔失败不影响其它订单
            db.session.rollback()
            failed += 1
            results.append({"order_id": order_id, "status": "failed",
                            "error": getattr(exc, "message", str(exc))[:300]})
    return {"summary": {"requested": len(order_ids), "synced": synced, "failed": failed},
            "results": results}


def get_order_freight(order_id):
    order = db.session.get(Order, order_id)
    if order is None:
        raise NotFoundError("订单不存在")
    if not can_access_shop(order.shop_id):
        raise ForbiddenError("无权查看该订单")
    row = FourPxFreight.query.filter_by(order_id=order.id).one_or_none()
    return row.to_dict() if row else None


def serialize_for_order(order_id):
    row = FourPxFreight.query.filter_by(order_id=order_id).one_or_none()
    return row.to_dict() if row else None
