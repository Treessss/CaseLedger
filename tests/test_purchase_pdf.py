"""PDF exports are scoped, selectable by product, and safe around image URLs."""

from io import BytesIO

from PIL import Image
import requests

from app import db
from app.models import Order, OrderItem
from app.services import purchase_pdf_service


def _create_list(app, client, headers, shop_id):
    with app.app_context():
        order = Order.query.filter_by(shop_id=shop_id).first()
        order.fulfillment_status = "unfulfilled"
        db.session.add_all(
            [
                OrderItem(
                    shop_id=shop_id,
                    order_id=order.id,
                    shopify_line_item_id="pdf-blue",
                    shopify_product_id="pdf-product-a",
                    shopify_variant_id="pdf-variant-a",
                    title="Phone Case",
                    variant_title="Blue / iPhone 18 Pro",
                    sku="CASE-BLUE",
                    image_url="https://cdn.shopify.com/example.jpg",
                    quantity=2,
                ),
                OrderItem(
                    shop_id=shop_id,
                    order_id=order.id,
                    shopify_line_item_id="pdf-blue-plus",
                    shopify_product_id="pdf-product-a",
                    shopify_variant_id="pdf-variant-a-plus",
                    title="Phone Case",
                    variant_title="Blue / iPhone 18 Pro Max",
                    sku="CASE-BLUE-MAX",
                    image_url="https://cdn.shopify.com/example-other.jpg",
                    quantity=1,
                ),
                OrderItem(
                    shop_id=shop_id,
                    order_id=order.id,
                    shopify_line_item_id="pdf-red",
                    shopify_product_id="pdf-product-b",
                    shopify_variant_id="pdf-variant-b",
                    title="Tablet Case",
                    variant_title="Red / iPad Pro",
                    quantity=1,
                ),
            ]
        )
        db.session.commit()
        order_id = order.id
    return client.post(
        "/api/purchase-lists",
        headers=headers,
        json={"shop_id": shop_id, "order_ids": [order_id], "name": "今日采购"},
    ).get_json()["data"]["id"]


def test_purchase_pdf_full_and_single_product(app, client, admin_headers, shop_ids, monkeypatch):
    sample = BytesIO()
    Image.new("RGB", (4, 4), "red").save(sample, format="PNG")
    downloaded = []

    def download(url):
        downloaded.append(url)
        return sample.getvalue()

    monkeypatch.setattr(purchase_pdf_service, "_download_image", download)
    purchase_id = _create_list(app, client, admin_headers, shop_ids["a"])

    full = client.get(f"/api/purchase-lists/{purchase_id}/pdf", headers=admin_headers)
    single = client.get(
        f"/api/purchase-lists/{purchase_id}/pdf?product_index=2", headers=admin_headers
    )
    assert full.status_code == single.status_code == 200
    assert full.mimetype == single.mimetype == "application/pdf"
    assert full.data.startswith(b"%PDF-") and single.data.startswith(b"%PDF-")
    assert b"/Subtype /Image" in full.data
    assert b".pdf" in single.headers["Content-Disposition"].encode()
    assert len(full.data) > len(single.data)
    assert "https://cdn.shopify.com/example-other.jpg" not in downloaded

    invalid = client.get(
        f"/api/purchase-lists/{purchase_id}/pdf?product_index=3", headers=admin_headers
    )
    assert invalid.status_code == 404
    assert client.get(
        f"/api/purchase-lists/{purchase_id}/pdf?product_index=0", headers=admin_headers
    ).status_code == 400


def test_purchase_pdf_respects_shop_scope(app, client, admin_headers, sub_a_headers, shop_ids, monkeypatch):
    monkeypatch.setattr(purchase_pdf_service, "_download_image", lambda _url: None)
    purchase_id = _create_list(app, client, admin_headers, shop_ids["b"])
    response = client.get(f"/api/purchase-lists/{purchase_id}/pdf", headers=sub_a_headers)
    assert response.status_code == 403


def test_purchase_pdf_image_urls_are_restricted():
    assert purchase_pdf_service._image_url_for_pdf("http://cdn.shopify.com/x.jpg") is None
    assert purchase_pdf_service._image_url_for_pdf("https://cdn.shopify.com.evil.test/x.jpg") is None
    assert purchase_pdf_service._image_url_for_pdf("https://127.0.0.1/x.jpg") is None
    assert purchase_pdf_service._image_url_for_pdf("https://cdn.shopify.com:bad/x.jpg") is None
    assert purchase_pdf_service._image_url_for_pdf("https://cdn.shopify.com/x.jpg").endswith("width=120")


def test_pdf_thumbnail_cache_reuses_downloads_and_expires_failures(monkeypatch):
    purchase_pdf_service._image_cache.clear()
    now = [10]
    calls = []
    monkeypatch.setattr(purchase_pdf_service.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(purchase_pdf_service, '_download_image', lambda url: calls.append(url) or (b'png' if url == 'ok' else None))
    try:
        assert purchase_pdf_service._cached_image('ok') == b'png'
        assert purchase_pdf_service._cached_image('ok') == b'png'
        assert calls == ['ok']
        assert purchase_pdf_service._cached_image('failed') is None
        assert purchase_pdf_service._cached_image('failed') is None
        assert calls == ['ok', 'failed']
        now[0] += 31
        assert purchase_pdf_service._cached_image('failed') is None
        assert calls == ['ok', 'failed', 'failed']
    finally:
        purchase_pdf_service._image_cache.clear()


def test_purchase_pdf_retries_transient_image_connection_errors(monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Type": "image/png", "Content-Length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_content(self, chunk_size):
            assert chunk_size > 0
            yield b"png"

    attempts = []

    def get(_url, **kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise requests.ConnectionError("transient CDN disconnect")
        return Response()

    monkeypatch.setattr(purchase_pdf_service.requests, "get", get)
    monkeypatch.setattr(purchase_pdf_service.time, "sleep", lambda _seconds: None)
    assert purchase_pdf_service._download_image("https://cdn.shopify.com/image.png") == b"png"
    assert len(attempts) == 3
    assert all(attempt["allow_redirects"] is False for attempt in attempts)


def test_purchase_pdf_distinguishes_missing_from_failed_image():
    assert "无图" in purchase_pdf_service._thumbnail("", {}).text
    assert "图片暂不可用" in purchase_pdf_service._thumbnail("https://cdn.shopify.com/image.png", {}).text
