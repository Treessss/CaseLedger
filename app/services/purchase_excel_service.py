"""Image-free, shop-authorized procurement workbook exports."""

from io import BytesIO
from math import ceil
import re

import xlsxwriter

from app.services.purchase_service import get_purchase_list
from app.services.product_translation_service import display_payload
from app.utils.errors import ValidationError, NotFoundError

MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
HEADERS = ['商品名称', '颜色 / 配置', '型号', '完整变体', 'SKU', '数量', '关联订单号']
WIDTHS = [48, 26, 24, 52, 46, 10, 36]


def _text(sheet, row, col, value, fmt):
    value = str(value or '')
    if len(value) > 32767:
        raise ValidationError('导出字段过长，超过 Excel 单元格长度限制')
    sheet.write_string(row, col, value, fmt)


def build_purchase_excel(purchase_id, product_index=None):
    data = get_purchase_list(purchase_id)
    data = display_payload(data, data['shop_id'])
    products = data['products']
    export_name = data['name']
    if product_index is not None:
        try:
            index = int(product_index)
        except (TypeError, ValueError) as exc:
            raise ValidationError('商品大类编号不合法') from exc
        if index < 1:
            raise ValidationError('商品大类编号不合法')
        if index > len(products):
            raise NotFoundError('商品大类不存在')
        products = [products[index - 1]]
        export_name += '-'+products[0]['title']
    output = BytesIO()
    book = xlsxwriter.Workbook(output, {'in_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False})
    body = book.add_format({'font_name': 'Arial', 'font_size': 11, 'text_wrap': True,
                            'valign': 'vcenter', 'align': 'left', 'num_format': '@'})
    number = book.add_format({'font_name': 'Arial', 'font_size': 11, 'num_format': '#,##0', 'align': 'right', 'valign': 'vcenter'})
    header = book.add_format({'font_name': 'Arial', 'font_size': 11, 'bold': True, 'bg_color': '#244799',
                              'font_color': '#FFFFFF', 'align': 'center', 'valign': 'vcenter'})
    title = book.add_format({'font_name': 'Arial', 'font_size': 15, 'bold': True})
    detail = book.add_worksheet('采购明细')
    summary = book.add_worksheet('对数单')
    for sheet in (detail, summary):
        sheet.hide_gridlines(2)
        sheet.set_row(0, 25)
        sheet.set_row(3, 24)
    _text(detail, 0, 0, export_name, title)
    for col, (label, width) in enumerate(zip(HEADERS, WIDTHS)):
        detail.set_column(col, col, width)
        _text(detail, 3, col, label, header)
    row = 4
    product_ranges = []
    for product in products:
        start = row + 1
        quantity = 0
        for color in product['colors']:
            for item in color['variants']:
                model = (item.get('model_title') or item.get('variant_title') or '默认款').rsplit(' · SKU: ', 1)[0]
                values = [product['title'], color['name'], model, item.get('variant_title') or '默认款',
                          item.get('sku') or '未设置', int(item.get('quantity') or 0),
                          '、'.join(str(n) for n in item.get('order_numbers') or [])]
                quantity += values[5]
                for col, value in enumerate(values):
                    if col == 5:
                        detail.write_number(row, col, value, number)
                    else:
                        _text(detail, row, col, value, body)
                height = max(2, max(ceil(sum(2 if ord(c) > 127 else 1 for c in str(v)) / WIDTHS[c])
                                    for c, v in enumerate(values)))
                detail.set_row(row, min(409, height * 16))
                row += 1
        product_ranges.append((product['title'], start, row, quantity))
    total = sum(value[3] for value in product_ranges)
    _text(detail, 1, 0, '采购总件数', body)
    detail.write_formula(1, 5, f'=SUM(F5:F{row})' if row > 4 else '=0', number, total)
    detail.freeze_panes(4, 1)
    detail.autofilter(3, 0, max(row - 1, 3), 6)
    summary.set_column(0, 0, 65)
    summary.set_column(1, 1, 16)
    _text(summary, 0, 0, f"{export_name} 对数单", title)
    _text(summary, 3, 0, '产品大类', header)
    _text(summary, 3, 1, '数量（件）', header)
    for index, (name, start, end, quantity) in enumerate(product_ranges, 4):
        _text(summary, index, 0, name, body)
        summary.write_formula(index, 1, f"=SUM('采购明细'!F{start}:F{end})" if end >= start else '=0', number, quantity)
        summary.set_row(index, 32)
    final_row = 4 + len(product_ranges)
    _text(summary, final_row, 0, '总计', body)
    summary.write_formula(final_row, 1, f'=SUM(B5:B{final_row})' if product_ranges else '=0', number, total)
    summary.freeze_panes(4, 0)
    book.close()
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '-', export_name).strip(' .-') or '采购清单'
    return output.getvalue(), filename[:100] + '.xlsx'
