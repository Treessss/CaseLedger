"""Register carrier-confirmed physical barcodes without changing outbound state."""

from concurrent.futures import ThreadPoolExecutor

from flask import current_app
from sqlalchemy import or_

from app import db
from app.models import AfterSale, Order, ShippingParcel, Shop
from app.models.shop import ShopCredentialError
from app.services.fourpx_label_service import fetch_label_data
from app.services.fourpx_client import FourPxClient
from app.utils.errors import ApiError


def enqueue_barcode_sync(shop_id, tracking_numbers):
    """Event-triggered, not scheduled; never hold up Shopify writeback on label I/O."""
    if current_app.config.get("TESTING"):
        return
    ids = [row.id for row in ShippingParcel.query.with_entities(ShippingParcel.id).filter(
        ShippingParcel.shop_id == shop_id, ShippingParcel.source == "4px",
        ShippingParcel.label_number.in_(tracking_numbers), ShippingParcel.scanned_at.is_(None),
        or_(ShippingParcel.label_barcode.is_(None), ShippingParcel.label_barcode == ShippingParcel.label_number),
    ).all()]
    if not ids:
        return
    try:
        from app.tasks import sync_fourpx_label_barcodes_task

        sync_fourpx_label_barcodes_task.apply_async(args=[shop_id, ids], retry=False)
    except Exception:
        current_app.logger.exception("Unable to queue 4PX physical barcode registration for shop %s", shop_id)


def sync_barcodes(shop_id, parcel_ids=None, *, client_factory=FourPxClient):
    """Trusted internal task/backfill. Only fill pending same-shop 4PX parcels."""
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        return {"updated": 0, "failed": [{"reason": "店铺不存在"}]}
    try:
        key, secret = shop.get_fourpx_credentials()
    except ShopCredentialError:
        return {"updated": 0, "failed": [{"reason": "店铺未配置 4PX 凭证"}]}
    query = db.session.query(ShippingParcel.id, ShippingParcel.label_number).join(Order).filter(
        ShippingParcel.shop_id == shop_id, Order.shop_id == shop_id, Order.warehouse_status == "pending",
        ShippingParcel.source == "4px", ShippingParcel.scanned_at.is_(None),
        or_(ShippingParcel.label_barcode.is_(None), ShippingParcel.label_barcode == ShippingParcel.label_number),
    )
    if parcel_ids is not None:
        query = query.filter(ShippingParcel.id.in_(parcel_ids))
    jobs = [(row.id, row.label_number) for row in query.all()]
    environment = shop.fourpx_environment
    db.session.rollback()  # Release the read transaction before waiting for carrier responses.
    updated, failed = 0, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(parcel_id, number, pool.submit(fetch_label_data, key, secret, environment, number,
                                          client_factory=client_factory)) for parcel_id, number in jobs]
        for parcel_id, number, future in futures:
            try:
                barcode = future.result()["label_barcode"]
            except ApiError as exc:
                failed.append({"parcel_id": parcel_id, "reason": exc.message})
                continue
            except Exception:
                failed.append({"parcel_id": parcel_id, "reason": "4PX 条码获取失败"})
                continue
            parcel = ShippingParcel.query.filter_by(id=parcel_id, shop_id=shop_id).with_for_update().one_or_none()
            if (parcel is None or parcel.scanned_at or parcel.order.warehouse_status != "pending"
                    or parcel.order.shop_id != shop_id or parcel.source != "4px" or parcel.label_number != number):
                db.session.rollback()
                continue
            if parcel.label_barcode and parcel.label_barcode not in (parcel.label_number, barcode):
                db.session.rollback()
                failed.append({"parcel_id": parcel_id, "reason": "已有不同条码，请人工核对"})
                continue
            conflict = ShippingParcel.query.filter(
                ShippingParcel.shop_id == shop_id, ShippingParcel.id != parcel.id,
                or_(ShippingParcel.label_number == barcode, ShippingParcel.label_barcode == barcode),
            ).first()
            sale_conflict = AfterSale.query.filter(
                AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None),
                or_(AfterSale.tracking_number == barcode, AfterSale.label_barcode == barcode),
            ).first()
            if conflict or sale_conflict:
                db.session.rollback()
                failed.append({"parcel_id": parcel_id, "reason": "条码已用于其他包裹，请人工核对"})
                continue
            if parcel.label_barcode == barcode:
                db.session.rollback()
                continue
            parcel.label_barcode = barcode
            try:
                db.session.commit()
                updated += 1
            except Exception:
                db.session.rollback()
                failed.append({"parcel_id": parcel_id, "reason": "条码保存冲突，请人工核对"})
    return {"updated": updated, "failed": failed}


def sync_after_sale_barcodes(shop_id, after_sale_ids=None, *, limit=None, client_factory=FourPxClient):
    """补齐售后补发的实体面单条码。

    4PX 的运单号与实体面单条码可能不同（加拿大智选常见：运单 OLS…、面单 GV…）。
    批量「只绑定售后」只写运单号，仓库扫 GV 条码会关联不到，这里按运单号回查面单补齐。
    """
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        return {"updated": 0, "failed": [{"reason": "店铺不存在"}]}
    try:
        key, secret = shop.get_fourpx_credentials()
    except ShopCredentialError:
        return {"updated": 0, "failed": [{"reason": "店铺未配置 4PX 凭证"}]}
    query = db.session.query(AfterSale.id, AfterSale.tracking_number).filter(
        AfterSale.shop_id == shop_id, AfterSale.deleted_at.is_(None),
        AfterSale.tracking_number.isnot(None),
        or_(AfterSale.label_barcode.is_(None), AfterSale.label_barcode == AfterSale.tracking_number),
    )
    if after_sale_ids is not None:
        query = query.filter(AfterSale.id.in_(after_sale_ids))
    if limit:
        query = query.limit(int(limit))
    jobs = [(row.id, row.tracking_number) for row in query.all()]
    environment = shop.fourpx_environment
    db.session.rollback()  # 等 4PX 响应前先释放读事务
    updated, failed = 0, []
    for sale_id, number in jobs:
        try:
            label = fetch_label_data(key, secret, environment, number, client_factory=client_factory)
            barcode = str(label.get("label_barcode") or "").strip()
        except ApiError as exc:
            failed.append({"after_sale_id": sale_id, "reason": exc.message})
            continue
        except Exception:
            failed.append({"after_sale_id": sale_id, "reason": "4PX 条码获取失败"})
            continue
        record = AfterSale.query.filter_by(id=sale_id, shop_id=shop_id).with_for_update().one_or_none()
        if record is None or record.deleted_at or record.tracking_number != number:
            db.session.rollback()
            continue
        if record.label_barcode and record.label_barcode not in (number, barcode):
            db.session.rollback()
            failed.append({"after_sale_id": sale_id, "reason": "已有不同条码，请人工核对"})
            continue
        if not barcode:
            db.session.rollback()
            failed.append({"after_sale_id": sale_id, "reason": "4PX 未返回实体条码"})
            continue
        if (AfterSale.query.filter(AfterSale.id != record.id, AfterSale.label_barcode == barcode).first()
                or ShippingParcel.query.filter(or_(ShippingParcel.label_barcode == barcode,
                                                   ShippingParcel.label_number == barcode)).first()):
            db.session.rollback()
            failed.append({"after_sale_id": sale_id, "reason": "条码已被其他记录占用，请人工核对"})
            continue
        if record.label_barcode == barcode:
            db.session.rollback()
            continue
        record.label_barcode = barcode
        try:
            db.session.commit()
            updated += 1
        except Exception:
            db.session.rollback()
            failed.append({"after_sale_id": sale_id, "reason": "条码保存冲突，请人工核对"})
    return {"updated": updated, "failed": failed}
