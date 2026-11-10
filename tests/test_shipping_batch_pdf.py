"""Bulk printing preserves 4PX page dimensions and never omits a ready parcel."""

from io import BytesIO
from datetime import datetime, timezone

import pytest
from flask import g
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app import db
from app.models import Order, OrderItem, ShippingParcel
from app.services.shipping_batch_pdf_service import (
    _download_pdf, _fetch_optimized, _order_number_sort_key, _product_codes_by_day,
    build_ready_labels_pdf,
)
from app.utils.errors import ValidationError
from tests.test_shipping_parcels import _ready_order


def _pdf(text, size):
    stream = BytesIO()
    page = canvas.Canvas(stream, pagesize=size)
    page.drawString(15, size[1] - 25, text)
    page.save()
    return stream.getvalue()


def test_ready_parcels_merge_in_order_without_resizing(app, client, admin_headers, shop_ids, tmp_path):
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        order = db.session.get(Order, order_id)
        order.expected_parcel_count = 2
        db.session.add_all([
            ShippingParcel(shop_id=order.shop_id, order_id=order_id, label_number="4PX-1", source="4px"),
            ShippingParcel(shop_id=order.shop_id, order_id=order_id, label_number="4PX-2", source="4px"),
        ])
        db.session.commit()
        urls = {parcel.id: f"https://bss-fss.4px.com/{parcel.label_number}.pdf"
                for parcel in ShippingParcel.query.filter_by(order_id=order_id).all()}
        fetched = []

        def fake_label_fetch(selected_order_id, parcel_id):
            assert selected_order_id == order_id
            fetched.append(parcel_id)
            return {"pdf_url": urls[parcel_id]}

        def fake_download(url):
            if "4PX-1" in url:
                return _pdf("FIRST-PARCEL", (100, 150))
            return _pdf("SECOND-PARCEL", (200, 300))

        with app.test_request_context():
            g.current_user = {"authority": "admin"}
            content, filename = build_ready_labels_pdf(
                shop_ids["a"], label_fetch=fake_label_fetch, download_pdf=fake_download
            )
        output = tmp_path / "merged-labels.pdf"
        output.write_bytes(content)
        reader = PdfReader(BytesIO(content))
        assert len(reader.pages) == 2
        assert [round(float(page.mediabox.width)) for page in reader.pages] == [100, 200]
        assert [round(float(page.mediabox.height)) for page in reader.pages] == [150, 300]
        assert [page.extract_text().strip() for page in reader.pages] == ["FIRST-PARCEL", "SECOND-PARCEL"]
        assert len(fetched) == 2
        assert filename.endswith(f"shop-{shop_ids['a']}.pdf")


def test_batch_prints_dismissed_ready_order_without_oversubscribing_shared_stock(app, client, admin_headers, shop_ids):
    order_id = _ready_order(app, client, admin_headers, shop_ids['a'])
    with app.app_context():
        order = db.session.get(Order, order_id)
        order.procurement_excluded_at = datetime.utcnow()
        order.order_date = datetime(2020, 1, 1)
        stamp = order.procurement_excluded_at
        second = Order.query.filter_by(shop_id=shop_ids['a'], order_number='#A1').one()
        second_id = second.id
        db.session.add(OrderItem(shop_id=shop_ids['a'], order_id=second.id,
            shopify_line_item_id='dismiss-second', shopify_variant_id='parcel-test-variant',
            title='Case', variant_title='Blue', quantity=1))
        db.session.add_all([
            ShippingParcel(shop_id=order.shop_id, order_id=order_id, label_number='DISMISSED-LABEL', source='4px'),
            ShippingParcel(shop_id=order.shop_id, order_id=second.id, label_number='SECOND-LABEL', source='4px'),
        ])
        db.session.commit()
        fetched = []
        def label_fetch(selected_order_id, parcel_id):
            assert selected_order_id == order_id  # The oldest complete order gets the one available unit.
            fetched.append((selected_order_id, parcel_id))
            return {'pdf_url': 'https://bss-fss.4px.com/dismissed.pdf'}
        with app.test_request_context():
            g.current_user = {'authority': 'admin'}
            content, _ = build_ready_labels_pdf(shop_ids['a'], label_fetch=label_fetch,
                download_pdf=lambda _: _pdf('DISMISSED-READY-ORDER', (100, 150)))
        assert len(fetched) == 1
        pages = PdfReader(BytesIO(content)).pages
        assert len(pages) == 1 and 'DISMISSED-READY-ORDER' in pages[0].extract_text()
        assert db.session.get(Order, order_id).procurement_excluded_at == stamp
        assert db.session.get(Order, order_id).warehouse_status == 'pending'
        assert db.session.get(Order, second_id).warehouse_status == 'pending'


def test_manual_parcel_uses_verified_fourpx_label(app, client, admin_headers, shop_ids, monkeypatch):
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    response = client.post(f"/api/orders/{order_id}/parcels", headers=admin_headers,
                           json={"shipping_label_number": "MANUAL-1"})
    assert response.status_code == 200
    monkeypatch.setattr("app.models.shop.Shop.get_fourpx_credentials", lambda self: ("key", "secret"))
    fetched = []

    def verified_label(key, secret, environment, tracking):
        fetched.append(tracking)
        return {"pdf_url": "https://bss-fss.4px.com/manual.pdf", "label_barcode": tracking}

    monkeypatch.setattr("app.services.shipping_batch_pdf_service.fetch_label_data", verified_label)
    with app.app_context(), app.test_request_context():
        g.current_user = {"authority": "admin"}
        content, _ = build_ready_labels_pdf(
            shop_ids["a"], download_pdf=lambda url: _pdf("MANUAL-1", (100, 150))
        )
    assert len(PdfReader(BytesIO(content)).pages) == 1
    assert fetched == ["MANUAL-1"]


def test_manual_parcel_rejects_different_fourpx_barcode(app, client, admin_headers, shop_ids, monkeypatch):
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    response = client.post(f"/api/orders/{order_id}/parcels", headers=admin_headers,
                           json={"shipping_label_number": "MANUAL-1"})
    assert response.status_code == 200
    monkeypatch.setattr("app.models.shop.Shop.get_fourpx_credentials", lambda self: ("key", "secret"))
    monkeypatch.setattr(
        "app.services.shipping_batch_pdf_service.fetch_label_data",
        lambda key, secret, environment, tracking: {
            "pdf_url": "https://bss-fss.4px.com/manual.pdf", "label_barcode": "DIFFERENT",
        },
    )
    with app.app_context(), app.test_request_context():
        g.current_user = {"authority": "admin"}
        with pytest.raises(ValidationError, match="面单条码与此前记录不一致"):
            build_ready_labels_pdf(shop_ids["a"], download_pdf=lambda url: _pdf("WRONG", (100, 150)))


def test_batch_includes_ready_orders_beyond_current_page(app, client, admin_headers, shop_ids):
    first_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        second = Order.query.filter_by(shop_id=shop_ids["a"], order_number="#A1").one()
        db.session.add(OrderItem(
            shop_id=second.shop_id, order_id=second.id, shopify_line_item_id="batch-second-line",
            shopify_variant_id="batch-second-variant", title="Case", variant_title="Green", quantity=1,
        ))
        db.session.commit()
        second_id = second.id
        first = db.session.get(Order, first_id)
        first.order_number = "10"
        first.order_date = datetime(2026, 1, 1)
        second.order_number = "2"
        second.order_date = datetime(2026, 2, 1)
        db.session.commit()
    created = client.post("/api/purchase-lists", headers=admin_headers,
                          json={"shop_id": shop_ids["a"], "order_ids": [second_id]}).get_json()["data"]
    assert client.post(f"/api/purchase-lists/{created['id']}/confirm", headers=admin_headers).status_code == 200
    assert client.post(f"/api/purchase-lists/{created['id']}/receive", headers=admin_headers,
                       json={"items": [{"group_key": created["items"][0]["group_key"],
                                        "received_quantity": 1, "damaged_quantity": 0}]}).status_code == 200
    with app.app_context():
        for order_id in (first_id, second_id):
            db.session.add(ShippingParcel(shop_id=shop_ids["a"], order_id=order_id,
                                          label_number=f"4PX-{order_id}", source="4px"))
        db.session.commit()
    page = client.get(f"/api/shipping/ready?shop_id={shop_ids['a']}&page=1&per_page=1",
                      headers=admin_headers).get_json()["data"]
    assert page["pagination"]["total"] == 2
    assert len(page["orders"]) == 1
    with app.app_context(), app.test_request_context():
        g.current_user = {"authority": "admin"}
        content, _ = build_ready_labels_pdf(
            shop_ids["a"],
            label_fetch=lambda order_id, parcel_id: {"pdf_url": f"https://bss-fss.4px.com/{order_id}.pdf"},
            download_pdf=lambda url: _pdf(url.rsplit("/", 1)[-1], (100, 150)),
        )
    pages = PdfReader(BytesIO(content)).pages
    assert len(pages) == 2
    assert [page.extract_text().strip() for page in pages] == [f"{second_id}.pdf", f"{first_id}.pdf"]


def test_order_number_sort_is_numeric_and_places_cf_after_original():
    values = ["#10", "CF2", "#2", "1", "CF10", "#11"]
    assert sorted(values, key=_order_number_sort_key) == ["1", "#2", "CF2", "#10", "CF10", "#11"]


def test_interleaved_product_batches_reassemble_in_order(app, client, admin_headers, shop_ids, monkeypatch):
    order_id = _ready_order(app, client, admin_headers, shop_ids["a"])
    with app.app_context():
        order = db.session.get(Order, order_id)
        order.expected_parcel_count = 3
        db.session.add_all([
            ShippingParcel(shop_id=order.shop_id, order_id=order_id,
                           label_number=f"4PX-{index}", label_barcode=f"BAR-{index}", source="4px")
            for index in range(3)
        ])
        db.session.commit()
    monkeypatch.setattr("app.models.shop.Shop.get_fourpx_credentials", lambda self: ("key", "secret"))

    def grouped_pdf(jobs, credentials, environment, download_pdf):
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.append(BytesIO(_pdf("FIRST", (100, 150))))
        writer.append(BytesIO(_pdf("THIRD", (100, 150))))
        batch = BytesIO()
        writer.write(batch)
        return [
            (0, [jobs[0], jobs[2]], batch.getvalue(), ["BAR-0", "BAR-2"]),
            (1, [jobs[1]], _pdf("SECOND", (100, 150)), ["BAR-1"]),
        ]

    monkeypatch.setattr("app.services.shipping_batch_pdf_service._fetch_optimized", grouped_pdf)
    with app.app_context(), app.test_request_context():
        g.current_user = {"authority": "admin"}
        content, _ = build_ready_labels_pdf(shop_ids["a"])
    assert [page.extract_text().strip() for page in PdfReader(BytesIO(content)).pages] == [
        "FIRST", "SECOND", "THIRD",
    ]


def test_batch_download_is_shop_scoped(app, client, sub_a_headers, shop_ids):
    response = client.get(f"/api/shipping/ready-labels/pdf?shop_id={shop_ids['b']}", headers=sub_a_headers)
    assert response.status_code == 403


def test_batch_download_requires_currently_ready_orders(app, client, admin_headers, shop_ids):
    response = client.get(f"/api/shipping/ready-labels/pdf?shop_id={shop_ids['a']}", headers=admin_headers)
    assert response.status_code == 400
    assert "没有面单齐全" in response.get_json()["message"]


def test_batch_pdf_route_returns_one_download(app, client, admin_headers, shop_ids, monkeypatch):
    content = _pdf("ONE-LABEL", (100, 150))
    monkeypatch.setattr("app.services.shipping_batch_pdf_service.build_ready_labels_pdf",
                        lambda shop_id: (content, "4px-ready-labels-test.pdf"))
    response = client.get(f"/api/shipping/ready-labels/pdf?shop_id={shop_ids['a']}", headers=admin_headers)
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert "attachment" in response.headers["Content-Disposition"]
    assert PdfReader(BytesIO(response.data)).pages[0].extract_text().strip() == "ONE-LABEL"


def test_pdf_download_rejects_untrusted_hosts_and_redirects():
    with pytest.raises(ValidationError, match="可信下载域名"):
        _download_pdf("https://evil.example/label.pdf", http_get=lambda *args, **kwargs: None)

    class Redirect:
        status_code = 302

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Redirect()

    with pytest.raises(ValidationError, match="HTTP 302"):
        _download_pdf("https://bss-fss.4px.com/label.pdf", http_get=fake_get)
    assert calls[0][1]["allow_redirects"] is False


def test_optimized_batch_uses_4px_getlist_only_for_saved_barcodes(monkeypatch):
    calls = []

    class FakeFourPx:
        def __init__(self, key, secret, *, environment):
            assert (key, secret, environment) == ("key", "secret", "production")

        def get_order(self, number):
            return [{"consignment_info": {"4px_tracking_no": number,
                                           "logistics_product_code": "S5058"}}]

        def get_labels_pdf(self, numbers, code):
            calls.append((numbers, code))
            return "https://bss-fss.i4px.com/batch.pdf"

    def fake_single(key, secret, environment, number):
        return {"label_barcode": "BAR-3", "pdf_url": "https://bss-fss.4px.com/single.pdf"}

    def fake_download(url):
        if "batch" in url:
            return _two_page_pdf()
        return _pdf("THIRD", (100, 150))

    def _two_page_pdf():
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.append(BytesIO(_pdf("T-0 BAR-0", (100, 150))))
        writer.append(BytesIO(_pdf("T-1 BAR-1", (100, 150))))
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    monkeypatch.setattr("app.services.shipping_batch_pdf_service.FourPxClient", FakeFourPx)
    monkeypatch.setattr("app.services.shipping_batch_pdf_service.fetch_label_data", fake_single)
    jobs = [{"index": index, "tracking": f"T-{index}", "barcode": f"BAR-{index}" if index < 2 else None,
             "source": "4px" if index < 2 else "manual", "order_number": f"#{index}"}
            for index in range(3)]
    groups = _fetch_optimized(jobs, ("key", "secret"), "production", fake_download)
    assert calls == [(["T-0", "T-1"], "S5058")]
    assert [group[0] for group in groups] == [0, 2]
    assert sum(len(PdfReader(BytesIO(group[2])).pages) for group in groups) == 3
    assert groups[1][3] == ["BAR-3"]


def test_missing_barcodes_are_fetched_then_printed_in_one_verified_batch(monkeypatch):
    calls = []

    class FakeFourPx:
        def __init__(self, key, secret, *, environment):
            pass

        def get_labels_pdf(self, numbers, code):
            calls.append((numbers, code))
            return "https://bss-fss.i4px.com/batch.pdf"

    def fake_label(key, secret, environment, number):
        calls.append(("label", number))
        return {"label_barcode": f"BAR-{number}", "pdf_url": "https://bss-fss.4px.com/single.pdf"}

    def fake_download(url):
        assert "batch" in url
        from pypdf import PdfWriter
        writer = PdfWriter()
        for number in ("T-0", "T-1"):
            writer.append(BytesIO(_pdf(f"{number} BAR-{number}", (100, 150))))
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    monkeypatch.setattr("app.services.shipping_batch_pdf_service.FourPxClient", FakeFourPx)
    monkeypatch.setattr("app.services.shipping_batch_pdf_service.fetch_label_data", fake_label)
    monkeypatch.setattr("app.services.shipping_batch_pdf_service._product_codes_by_day",
                        lambda jobs, credentials, environment: {"T-0": "S5058", "T-1": "S5058"})
    jobs = [{"index": index, "tracking": f"T-{index}", "barcode": None,
             "source": "4px", "order_number": f"#{index}"} for index in range(2)]
    groups = _fetch_optimized(jobs, ("key", "secret"), "production", fake_download)
    assert (["T-0", "T-1"], "S5058") in calls
    assert len(groups) == 1
    assert groups[0][3] == ["BAR-T-0", "BAR-T-1"]


def test_bulk_pdf_host_is_exactly_allowlisted():
    from app.services.fourpx_label_service import _trusted_pdf_url
    assert _trusted_pdf_url("https://bss-fss.i4px.com/batch.pdf")
    with pytest.raises(ValidationError):
        _trusted_pdf_url("https://evil.i4px.com/batch.pdf")


def test_product_codes_are_resolved_by_beijing_day_instead_of_per_order(monkeypatch):
    calls = []

    class FakeFourPx:
        def __init__(self, key, secret, *, environment):
            pass

        def get_predeclared_created_between(self, start, end):
            calls.append((start, end))
            return [{"consignment_info": {"4px_tracking_no": "T-1",
                                          "logistics_product_code": "S5058"}}]

    monkeypatch.setattr("app.services.shipping_batch_pdf_service.FourPxClient", FakeFourPx)
    jobs = [{"tracking": "T-1", "created_at": datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)}]
    assert _product_codes_by_day(jobs, ("key", "secret"), "production") == {"T-1": "S5058"}
    assert len(calls) == 1  # No redundant prior-day request when the binding day matches.
    assert all(end - start == 86_400_000 for start, end in calls)


def test_product_codes_fall_back_to_previous_beijing_day(monkeypatch):
    calls = []

    class FakeFourPx:
        def __init__(self, key, secret, *, environment):
            pass

        def get_predeclared_created_between(self, start, end):
            calls.append(start)
            return ([{"consignment_info": {"4px_tracking_no": "T-1",
                                            "logistics_product_code": "S5058"}}]
                    if len(calls) == 2 else [])

    monkeypatch.setattr("app.services.shipping_batch_pdf_service.FourPxClient", FakeFourPx)
    jobs = [{"tracking": "T-1", "created_at": datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)}]
    assert _product_codes_by_day(jobs, ("key", "secret"), "production") == {"T-1": "S5058"}
    assert len(calls) == 2
