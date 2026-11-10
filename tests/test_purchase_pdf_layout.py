"""Printed procurement sheets stay readable and traceable in portrait format."""

from copy import deepcopy
from io import BytesIO

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate

from app.services import purchase_pdf_service as pdf
from app.services.purchase_service import _group_products


def _data():
    products = _group_products([
        {"title": "Gradient Case", "shopify_product_id": "gradient", "variant_title": "iPhone 18 Pro Max / Pink Lime",
         "group_key": "plain", "quantity": 2, "order_numbers": ["12979", "12907"], "image_url": ""},
        {"title": "Gradient Case", "shopify_product_id": "gradient", "variant_title": "iPhone 18 Pro Max / Pink Lime + Wrist Chain",
         "group_key": "bundle", "quantity": 2, "order_numbers": ["13047", "13028"], "image_url": ""},
        {"title": "Uncolored Case", "shopify_product_id": "uncolored", "variant_title": "iPhone 14 Pro", "sku": "CAT-14P",
         "group_key": "14", "quantity": 1, "order_numbers": ["CF9379"], "image_url": ""},
    ])
    return {"shop_id": 1, "shop_name": "CASEEASE", "name": "采购清单测试", "created_at": "2026-10-12T08:00:00",
            "order_count": 5, "product_count": len(products), "products": products}


def _prepare(monkeypatch, data):
    monkeypatch.setattr(pdf, "get_purchase_list", lambda _id: data)
    monkeypatch.setattr("app.services.product_translation_service.display_payload", lambda source, _shop: source)
    monkeypatch.setattr(pdf, "_prefetch_images", lambda _products: {})


def test_purchase_pdf_portrait_sku_orders_and_distinct_bundles(monkeypatch):
    data = _data()
    before = deepcopy(data)
    _prepare(monkeypatch, data)
    content, filename = pdf.build_purchase_pdf(31)
    reader = PdfReader(BytesIO(content))
    assert filename.endswith('.pdf')
    for page in reader.pages:
        assert abs(float(page.mediabox.width) - A4[0]) < 1
        assert abs(float(page.mediabox.height) - A4[1]) < 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert 'CASEEASE' not in text
    assert '店铺' not in text
    for expected in ('型号 / 数量', '关联订单号', 'SKU: CAT-14P', '12979', '12907', '13047', '13028', 'CF9379', 'Wrist Chain'):
        assert expected in text
    last_page = reader.pages[-1].extract_text()
    assert '采购对数单' in last_page
    assert 'Gradient Case' in last_page and 'Uncolored Case' in last_page
    assert '型号 / 数量' not in last_page
    assert '总计' in last_page
    assert data == before


def test_single_product_export_and_large_order_column_paginate(monkeypatch):
    data = _data()
    data['products'][1]['colors'][0]['variants'] *= 35
    for variant in data['products'][1]['colors'][0]['variants']:
        variant['order_numbers'] = [str(number) for number in range(12000, 12025)]
        variant['sku'] = 'long_sku_' * 12
    _prepare(monkeypatch, data)
    content, _filename = pdf.build_purchase_pdf(31, product_index=2)
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) > 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert 'Gradient Case' not in text
    assert '12024' in text
    assert 'SKU: long_sku_' in text
    assert text.count('关联订单号') >= 2
    assert 'Uncolored Case' in reader.pages[-1].extract_text()
    assert '采购对数单' in reader.pages[-1].extract_text()


def test_one_variant_with_hundreds_of_orders_can_split_across_pages(monkeypatch):
    data = _data()
    data['products'][1]['colors'][0]['variants'][0]['order_numbers'] = [str(n) for n in range(12000, 12600)]
    _prepare(monkeypatch, data)
    content, _filename = pdf.build_purchase_pdf(31, product_index=2)
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) > 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert '12000' in text and '12599' in text


def test_count_sheet_uses_quantities_and_does_not_merge_different_products():
    products = [
        {'title': '同名中文产品', 'quantity': 75},
        {'title': '同名中文产品', 'quantity': 42},
        {'title': '其他产品', 'quantity': 22},
    ]
    table = pdf._count_sheet(products)
    values = [[cell.getPlainText() for cell in row] for row in table._cellvalues]
    assert values == [
        ['产品大类', '数量（件）'], ['同名中文产品', '75'],
        ['同名中文产品', '42'], ['其他产品', '22'], ['总计', '139'],
    ]


def test_count_sheet_continues_with_headers_and_total_on_last_page():
    output = BytesIO()
    products = [{'title': f'商品分类 {n}', 'quantity': 1} for n in range(90)]
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=36, rightMargin=36)
    doc.build([pdf._count_sheet(products)])
    reader = PdfReader(BytesIO(output.getvalue()))
    assert len(reader.pages) > 1
    assert all('产品大类' in page.extract_text() for page in reader.pages)
    assert '商品分类 89' in reader.pages[-1].extract_text()
    assert '总计' in reader.pages[-1].extract_text()
    assert '90' in reader.pages[-1].extract_text()
