"""CF 售后补发的 4PX 真实物流费：缓存 + 写入售后费用。"""

import json
from datetime import datetime
from decimal import Decimal

from app import db
from app.models import AfterSale, AfterSaleCost, FourPxAfterSaleFreight, Shop
from app.services.fourpx_freight_service import _parse_payload
from app.utils.errors import NotFoundError, ValidationError
from app.services.fourpx_client import FourPxClient, FourPxError


def _client(shop_id, client_factory=None):
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise NotFoundError("店铺不存在")
    try:
        key, secret = shop.get_fourpx_credentials()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(str(exc)) from exc
    factory = client_factory or FourPxClient
    return factory(key, secret, environment=shop.fourpx_environment)


def _ensure_cost(record, freight):
    """写/更新一条 source=4px 的售后物流费；人工登记的费用不动。"""
    if (freight.currency or "CNY").upper() != "CNY":
        raise ValidationError(f"4PX 售后费用币种为 {freight.currency}，需人工换算后再记")
    amount = Decimal(str(freight.total_fee or 0)).quantize(Decimal("0.01"))
    if amount <= 0:
        return None
    cost = AfterSaleCost.query.filter_by(
        after_sale_id=record.id, cost_type="shipping", source="4px",
    ).order_by(AfterSaleCost.id).first()
    if cost is None:
        cost = AfterSaleCost(
            after_sale_id=record.id, shop_id=record.shop_id, cost_type="shipping",
            amount_cny=amount, source="4px",
            note="4PX 真实补发运费（自动）",
            fourpx_tracking_no=freight.tracking_number,
            fourpx_synced_at=datetime.utcnow(),
            incurred_at=record.shipped_at or freight.billed_at or datetime.utcnow(),
        )
        db.session.add(cost)
    else:
        cost.amount_cny = amount
        cost.fourpx_tracking_no = freight.tracking_number
        cost.fourpx_synced_at = datetime.utcnow()
        cost.note = "4PX 真实补发运费（自动）"
        cost.incurred_at = record.shipped_at or freight.billed_at or cost.incurred_at
    return cost


def sync_after_sale_freight(after_sale_id, *, client_factory=None):
    record = db.session.get(AfterSale, after_sale_id)
    if record is None or record.deleted_at:
        raise NotFoundError("售后单不存在")
    if not record.tracking_number:
        raise ValidationError("售后单还没有 4PX 运单号")
    client = _client(record.shop_id, client_factory)
    ref = record.customer_reference or record.tracking_number
    try:
        raw = client.get_freight(ref)
    except FourPxError as exc:
        raise ValidationError(getattr(exc, "details", None) or "4PX 尚未出账") from exc
    parsed = _parse_payload(raw)
    row = FourPxAfterSaleFreight.query.filter_by(after_sale_id=record.id).one_or_none()
    if row is None:
        row = FourPxAfterSaleFreight(shop_id=record.shop_id, after_sale_id=record.id)
        db.session.add(row)
    row.tracking_number = parsed["tracking_number"] or record.tracking_number
    row.ref_no = parsed["ref_no"] or ref
    row.charge_weight_kg = parsed["charge_weight_kg"]
    row.currency = parsed["currency"]
    row.total_fee = parsed["total_fee"]
    row.subs = parsed["subs"]
    row.billed_at = parsed["billed_at"]
    row.raw = parsed["raw"]
    row.synced_at = datetime.utcnow()
    cost = _ensure_cost(record, row)
    db.session.commit()
    return {"after_sale_id": record.id, "customer_reference": record.customer_reference,
            "freight": row.to_dict(), "cost": cost.to_dict() if cost else None}
