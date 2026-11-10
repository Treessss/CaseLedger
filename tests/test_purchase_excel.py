"""Whole-list and single-product Excel exports preserve scope and exact totals."""
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

from tests.test_purchase_pdf import _create_list
from app.services import purchase_order_refresh_service

NS = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


@pytest.fixture(autouse=True)
def local_purchase(monkeypatch):
    monkeypatch.setattr(purchase_order_refresh_service, 'fetch_purchase_orders', lambda *_: [])


def workbook_parts(content):
    with ZipFile(BytesIO(content)) as book:
        strings = [''.join(item.itertext()) for item in ET.fromstring(book.read('xl/sharedStrings.xml'))]
        sheets = [ET.fromstring(book.read(f'xl/worksheets/sheet{index}.xml')) for index in (1, 2)]
        assert not any(name.startswith('xl/media/') for name in book.namelist())
    return strings, sheets


def value(strings, sheet, address):
    cell = sheet.find(f".//x:c[@r='{address}']", NS)
    raw = cell.find('x:v', NS).text
    return strings[int(raw)] if cell.get('t') == 's' else float(raw)


def test_excel_full_and_single_category_have_correct_rows_and_totals(app, client, admin_headers, shop_ids):
    purchase_id = _create_list(app, client, admin_headers, shop_ids['a'])
    full = client.get(f'/api/purchase-lists/{purchase_id}/excel', headers=admin_headers)
    single = client.get(f'/api/purchase-lists/{purchase_id}/excel?product_index=2', headers=admin_headers)
    assert full.status_code == single.status_code == 200
    assert single.mimetype == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    strings, (detail, summary) = workbook_parts(single.data)
    assert value(strings, detail, 'A5') == 'Tablet Case'
    assert value(strings, detail, 'F2') == value(strings, detail, 'F5') == 1
    assert value(strings, summary, 'A5') == 'Tablet Case'
    assert value(strings, summary, 'B5') == value(strings, summary, 'B6') == 1
    assert 'Phone Case' not in strings
    assert 'Tablet Case.xlsx' in single.headers['Content-Disposition']
    full_strings, (full_detail, full_summary) = workbook_parts(full.data)
    assert value(full_strings, full_detail, 'F2') == 4
    assert 'Phone Case' in full_strings and 'Tablet Case' in full_strings
    assert value(full_strings, full_detail, 'E4') == 'SKU'
    assert full_detail.find(".//x:c[@r='F2']/x:f", NS).text == 'SUM(F5:F7)'
    assert detail.find(".//x:c[@r='F2']/x:f", NS).text == 'SUM(F5:F5)'


@pytest.mark.parametrize('index,status', [('0', 400), ('-1', 400), ('abc', 400), ('1.5', 400), ('', 400), ('99', 404)])
def test_single_excel_rejects_invalid_categories(app, client, admin_headers, shop_ids, index, status):
    purchase_id = _create_list(app, client, admin_headers, shop_ids['a'])
    response = client.get(f'/api/purchase-lists/{purchase_id}/excel?product_index={index}', headers=admin_headers)
    assert response.status_code == status


def test_single_excel_cannot_bypass_shop_or_module_permissions(app, client, admin_headers, limited_headers, sub_a_headers, shop_ids):
    purchase_id = _create_list(app, client, admin_headers, shop_ids['a'])
    path = f'/api/purchase-lists/{purchase_id}/excel?product_index=1'
    assert client.get(path).status_code == 401
    assert client.get(path, headers=limited_headers).status_code == 403
    other_id = _create_list(app, client, admin_headers, shop_ids['b'])
    assert client.get(f'/api/purchase-lists/{other_id}/excel?product_index=1', headers=sub_a_headers).status_code == 403
