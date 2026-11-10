"""One authoritative, shop-scoped resolver for reshipment packing and scanning."""
from sqlalchemy import func, or_

from app.models import AfterSale, ShippingParcel
from app.models.cf_tracking_writeback import CfTrackingWriteback
from app.utils.errors import ValidationError


def _norm(value):
    return str(value or "").strip().upper()


def resolve_after_sale_label(shop_id, code, *, allow_reference=False):
    code = _norm(code)
    conditions = [func.upper(AfterSale.tracking_number) == code,
                  func.upper(AfterSale.label_barcode) == code]
    if allow_reference:
        conditions.append(func.upper(AfterSale.customer_reference) == code)
    records = AfterSale.query.filter(
        AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None), or_(*conditions),
    ).limit(2).all()
    if len(records) > 1:
        raise ValidationError("该面单匹配多张售后单，请核对补发运单")
    if records:
        record = records[0]
        collisions = ShippingParcel.query.filter(
            ShippingParcel.shop_id == shop_id,
            or_(func.upper(ShippingParcel.label_number) == code,
                func.upper(ShippingParcel.label_barcode) == code),
        ).all()
        if any(parcel.order_id != record.order_id for parcel in collisions):
            raise ValidationError("售后面单与其他订单包裹重号，请核对，未执行出库")
        if collisions and record.order.warehouse_status not in ("shipped", "legacy_unverified"):
            raise ValidationError("售后面单与未出库原包裹重号，请先核对物流绑定")
        return record
    # A CF overwrite without a replacement record must not silently pack the
    # original products. Their contents may have changed for an exchange.
    known_cf = CfTrackingWriteback.query.filter(
        CfTrackingWriteback.shop_id == shop_id,
        func.upper(CfTrackingWriteback.tracking_number) == code,
        CfTrackingWriteback.state.in_(("confirmed", "pending", "uncertain")),
    ).first()
    if known_cf:
        raise ValidationError("这是补发运单，但未找到有效售后商品清单；请先创建并绑定售后单，不能使用原订单商品出库")
    return None


def matches_after_sale_label(record, code):
    # An order/CF reference can query contents, but is never an outbound scan.
    value = _norm(code)
    return bool(value and value in {_norm(record.tracking_number), _norm(record.label_barcode)})
