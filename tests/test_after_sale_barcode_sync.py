"""售后补发：绑定 4PX 后要回填实体面单条码（运单号与条码可能不同）。"""

from datetime import datetime, timedelta

import pytest
from flask import g

from app import db
from app.models import AfterSale, Shop, ShippingParcel
from app.services import after_sale_service
from app.services.after_sale_label_service import resolve_after_sale_label
from app.services.fourpx_barcode_sync_service import sync_after_sale_barcodes
from tests.test_after_sale_archive_and_cf import create_case


class FakeLabelFourPx:
    rows = []

    def __init__(self, app_key, app_secret, *, environment):
        pass

    def get_predeclared_created_between(self, start_ms, end_ms):
        return FakeLabelFourPx.rows

    def get_label(self, request_no):
        return {
            "label_barcode": "GV26CAA0U0TEST001",
            "label_url_info": {"logistics_label": "https://label.4px.com/test.pdf"},
        }


def _set_creds(shop_id):
    shop = db.session.get(Shop, shop_id)
    shop.set_fourpx_credentials("test-key", "test-secret")
    db.session.commit()
    return shop


def test_bind_4px_fills_physical_barcode_so_gv_scan_resolves(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        _set_creds(shop_ids["a"])
        FakeLabelFourPx.rows = [{
            "consignment_info": {"ref_no": sale["customer_reference"], "4px_tracking_no": "OLS-TEST-0001",
                                 "consignment_status": "P"},
        }]
        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            bound = after_sale_service.bind_fourpx(
                sale["id"],
                {"date": datetime.utcnow().date().isoformat(), "tracking_number": "OLS-TEST-0001"},
                client_factory=FakeLabelFourPx,
            )
        assert bound["tracking_number"] == "OLS-TEST-0001"
        assert bound["label_barcode"] == "GV26CAA0U0TEST001"
        record = db.session.get(AfterSale, sale["id"])
        assert record.label_barcode == "GV26CAA0U0TEST001"
        resolved = resolve_after_sale_label(shop_ids["a"], "GV26CAA0U0TEST001")
        assert resolved is not None and resolved.id == sale["id"]


def test_sync_after_sale_barcodes_backfills_missing_barcode_and_is_idempotent(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        _set_creds(shop_ids["a"])
        record = db.session.get(AfterSale, sale["id"])
        record.tracking_number = "OLS-SYNC-0001"
        record.label_barcode = None
        db.session.commit()
        first = sync_after_sale_barcodes(shop_ids["a"], [sale["id"]], client_factory=FakeLabelFourPx)
        assert first == {"updated": 1, "failed": []}
        db.session.refresh(record)
        assert record.label_barcode == "GV26CAA0U0TEST001"
        second = sync_after_sale_barcodes(shop_ids["a"], [sale["id"]], client_factory=FakeLabelFourPx)
        assert second["updated"] == 0


def test_sync_skips_when_barcode_already_used_by_another_parcel(app, client, admin_headers, shop_ids):
    sale = create_case(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        _set_creds(shop_ids["a"])
        record = db.session.get(AfterSale, sale["id"])
        record.tracking_number = "OLS-SYNC-0002"
        record.label_barcode = None
        db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=record.order_id,
                                      label_number="OTHER-1", label_barcode="GV26CAA0U0TEST001",
                                      source="manual"))
        db.session.commit()
        result = sync_after_sale_barcodes(shop_ids["a"], [sale["id"]], client_factory=FakeLabelFourPx)
        assert result["updated"] == 0
        assert "占用" in result["failed"][0]["reason"]
        assert db.session.get(AfterSale, sale["id"]).label_barcode is None
