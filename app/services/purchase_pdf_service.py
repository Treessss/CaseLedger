"""Render saved procurement snapshots as shop-scoped, image-rich PDF files."""

import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    Image,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.purchase_service import get_purchase_list
from app.utils.errors import NotFoundError, ValidationError

FONT = "CaseLedgerNotoSansSC"
FONT_FILE = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSansSC-Regular.ttf"
pdfmetrics.registerFont(TTFont(FONT, str(FONT_FILE)))

PAGE_WIDTH, _ = A4
CONTENT_WIDTH = PAGE_WIDTH - 72
MAX_IMAGE_BYTES = 750_000
MAX_TOTAL_IMAGE_BYTES = 20_000_000
MAX_IMAGE_COUNT = 300
IMAGE_DOWNLOAD_ATTEMPTS = 4
ALLOWED_IMAGE_HOSTS = {"cdn.shopify.com", "shopifycdn.net"}
_image_cache = OrderedDict()
_image_cache_lock = Lock()


def _cached_image(url):
    """Bounded public-thumbnail cache; transient failures expire quickly."""
    with _image_cache_lock:
        entry = _image_cache.get(url)
        if entry and time.monotonic() < entry[0]:
            _image_cache.move_to_end(url)
            return entry[1]
        _image_cache.pop(url, None)
    data = _download_image(url)
    with _image_cache_lock:
        _image_cache[url] = (time.monotonic() + (3600 if data else 30), data)
        _image_cache.move_to_end(url)
        while len(_image_cache) > MAX_IMAGE_COUNT or sum(len(value[1] or b"") for value in _image_cache.values()) > MAX_TOTAL_IMAGE_BYTES:
            _image_cache.popitem(last=False)
    return data

TITLE_STYLE = ParagraphStyle(
    "purchase-title", fontName=FONT, fontSize=18, leading=25, textColor=colors.HexColor("#19336b")
)
PRODUCT_STYLE = ParagraphStyle(
    "purchase-product", fontName=FONT, fontSize=13, leading=19, textColor=colors.HexColor("#19336b")
)
COLOR_STYLE = ParagraphStyle(
    "purchase-color", fontName=FONT, fontSize=11, leading=16, textColor=colors.HexColor("#244799")
)
BODY_STYLE = ParagraphStyle(
    "purchase-body", fontName=FONT, fontSize=11, leading=16, wordWrap="CJK", textColor=colors.HexColor("#273044")
)
SMALL_STYLE = ParagraphStyle(
    "purchase-small", parent=BODY_STYLE, fontSize=8, leading=11, textColor=colors.HexColor("#586477")
)
HEADING_STYLE = ParagraphStyle(
    "purchase-heading", parent=BODY_STYLE, fontSize=11, leading=16, textColor=colors.white
)
SKU_STYLE = ParagraphStyle("purchase-sku", parent=BODY_STYLE, fontSize=9, leading=13)
STAT_STYLE = ParagraphStyle("purchase-stat", parent=BODY_STYLE, fontSize=11, leading=18, alignment=TA_CENTER)
COUNT_STYLE = ParagraphStyle(
    "purchase-count", parent=BODY_STYLE, fontSize=15, leading=20, alignment=TA_RIGHT,
    textColor=colors.HexColor("#19336b"),
)
COUNT_HEADING_STYLE = ParagraphStyle("purchase-count-heading", parent=HEADING_STYLE, alignment=TA_RIGHT)


def _paragraph(value, style=BODY_STYLE):
    return Paragraph(escape(str(value or "-")), style)


def _safe_filename(value):
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", str(value or "采购清单"))
    return (name.strip(" .-") or "采购清单")[:100]


def _image_url_for_pdf(url):
    try:
        parsed = urlparse(url or "")
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (host not in ALLOWED_IMAGE_HOSTS and not host.endswith(".shopifycdn.net")):
        return None
    if port not in (None, 443) or parsed.username or parsed.password:
        return None
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "width"]
    query.append(("width", "120"))
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def _download_image(url):
    safe_url = _image_url_for_pdf(url)
    if not safe_url:
        return None
    for attempt in range(IMAGE_DOWNLOAD_ATTEMPTS):
        try:
            with requests.get(safe_url, timeout=(3, 8), stream=True, allow_redirects=False) as response:
                if response.status_code in (429, 500, 502, 503, 504):
                    pass
                elif response.status_code != 200 or not response.headers.get("Content-Type", "").lower().startswith("image/"):
                    return None
                else:
                    if int(response.headers.get("Content-Length") or 0) > MAX_IMAGE_BYTES:
                        return None
                    data = bytearray()
                    for chunk in response.iter_content(chunk_size=32_768):
                        data.extend(chunk)
                        if len(data) > MAX_IMAGE_BYTES:
                            return None
                    return bytes(data) if data else None
        except (requests.RequestException, ValueError):
            pass
        if attempt < IMAGE_DOWNLOAD_ATTEMPTS - 1:
            time.sleep(0.25 * (attempt + 1))
    return None


def _prefetch_images(products):
    urls = list(
        dict.fromkeys(
            color.get("image_url")
            for product in products
            for color in product["colors"]
            if color.get("image_url")
        )
    )
    image_cache = {url: None for url in urls}
    total_bytes = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for url, data in zip(urls[:MAX_IMAGE_COUNT], pool.map(_cached_image, urls[:MAX_IMAGE_COUNT])):
            if data and total_bytes + len(data) <= MAX_TOTAL_IMAGE_BYTES:
                image_cache[url] = data
                total_bytes += len(data)
    return image_cache


def _thumbnail(url, image_cache):
    if not url:
        return _paragraph("无图", SMALL_STYLE)
    data = image_cache.get(url)
    if not data:
        return _paragraph("图片暂不可用", SMALL_STYLE)
    try:
        return Image(BytesIO(data), width=42, height=42, kind="proportional")
    except (OSError, ValueError):
        return _paragraph("图片格式不支持", SMALL_STYLE)


def _beijing_created_at(value):
    if not value:
        return "-"
    utc_time = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    local_time = utc_time.astimezone(timezone(timedelta(hours=8)))
    return local_time.strftime("%Y-%m-%d %H:%M")


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#dce3ed"))
    canvas.line(36, 31, PAGE_WIDTH - 36, 31)
    canvas.setFont(FONT, 8)
    canvas.setFillColor(colors.HexColor("#697587"))
    canvas.drawString(36, 18, "CaseLedger - 采购清单")
    canvas.drawRightString(PAGE_WIDTH - 36, 18, f"第 {doc.page} 页")
    canvas.restoreState()


def _summary(data, products):
    order_count = data["order_count"] if len(products) == data["product_count"] else len(products[0]["order_numbers"])
    total_quantity = sum(product["quantity"] for product in products)
    entries = [
        ("订单", f"{order_count} 笔"),
        ("商品大类", f"{len(products)} 类"),
        ("采购总数", f"{total_quantity} 件"),
    ]
    cells = [_paragraph(f"{label}: {value}") for label, value in entries]
    table = Table([cells], colWidths=[CONTENT_WIDTH / len(cells)] * len(cells))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f2f6fc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dce3ed")),
                ("TOPPADDING", (0, 0), (-1, -1), 11),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
            ]
        )
    )
    return table


def _product_summary(product):
    cells = [
        _paragraph(f"颜色 / 配置  {product['color_count']} 组", STAT_STYLE),
        _paragraph(f"具体变体  {product['variant_count']} 款", STAT_STYLE),
        Paragraph(f"合计 <font color='#b45309' size='15'>{int(product['quantity'])} 件</font>", STAT_STYLE),
    ]
    table = Table([cells], colWidths=[CONTENT_WIDTH / 3] * 3)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef3fc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def _variant_description(variant, color):
    title = variant.get("model_title") or variant.get("variant_title") or "默认款"
    # Display SKU on its own line, rather than crowding out model and quantity.
    uncolored = color.get("base_color") == "未标注颜色" or color.get("name") == "未标注颜色"
    if uncolored and " · SKU: " in title:
        title = title.rsplit(" · SKU: ", 1)[0]
    description = [Paragraph(
        f"{escape(str(title))}  <font color='#b45309' size='15'>× {int(variant.get('quantity') or 0)} 件</font>",
        BODY_STYLE,
    )]
    if uncolored:
        description.append(_paragraph(f"SKU: {(variant.get('sku') or '').strip() or '未设置'}", SKU_STYLE))
    return description


def _color_table(color):
    rows = [[_paragraph(label, HEADING_STYLE) for label in ("型号 / 数量", "关联订单号")]]
    for variant in color["variants"]:
        rows.append(
            [
                _variant_description(variant, color),
                _paragraph("、".join(str(number) for number in variant.get("order_numbers") or [])),
            ]
        )
    table = LongTable(
        rows, colWidths=[CONTENT_WIDTH * 0.56, CONTENT_WIDTH * 0.44],
        repeatRows=1, splitInRow=1, hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244799")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#e4e9f2")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8faff")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def _count_sheet(products):
    """Reconcile exactly the exported product groups, not order or variant counts."""
    rows = [[_paragraph("产品大类", HEADING_STYLE), _paragraph("数量（件）", COUNT_HEADING_STYLE)]]
    rows.extend([
        _paragraph(product['title']), _paragraph(int(product['quantity']), COUNT_STYLE),
    ] for product in products)
    total = sum(int(product['quantity']) for product in products)
    rows.append([_paragraph("总计", PRODUCT_STYLE), _paragraph(total, COUNT_STYLE)])
    table = LongTable(
        rows, colWidths=[CONTENT_WIDTH - 85, 85], repeatRows=1,
        splitInRow=1, hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#244799")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eef3fc")),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.HexColor("#dce3ed")),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#244799")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return table


def build_purchase_pdf(purchase_id, product_index=None):
    """Return PDF bytes and a download name; product_index is one-based."""
    data = get_purchase_list(purchase_id)
    from app.services.product_translation_service import display_payload
    data = display_payload(data, data["shop_id"])
    products = data["products"]
    if product_index is not None:
        try:
            index = int(product_index)
        except (TypeError, ValueError) as exc:
            raise ValidationError("商品大类编号不合法") from exc
        if index < 1:
            raise ValidationError("商品大类编号不合法")
        if index > len(products):
            raise NotFoundError("商品大类不存在")
        products = [products[index - 1]]

    filename = _safe_filename(data["name"])
    if product_index is not None:
        filename += f"-{_safe_filename(products[0]['title'])}"
    filename += ".pdf"

    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=38,
        bottomMargin=46,
        title=data["name"],
        author="CaseLedger",
    )
    story = [
        _paragraph(data["name"], TITLE_STYLE),
        Spacer(1, 5),
        _paragraph(f"生成时间 (北京时间): {_beijing_created_at(data['created_at'])}", SMALL_STYLE),
        Spacer(1, 14),
        _summary(data, products),
    ]
    image_cache = _prefetch_images(products)
    for position, product in enumerate(products, start=1):
        if position > 1:
            story.append(CondPageBreak(190))
            story.append(Spacer(1, 14))
        else:
            story.append(Spacer(1, 20))
        story.extend(
            [
                _paragraph(f"{position}. {product['title']}", PRODUCT_STYLE),
                Spacer(1, 5),
                _product_summary(product),
                Spacer(1, 12),
            ]
        )
        for color in product["colors"]:
            story.append(CondPageBreak(155))
            color_header = Table(
                [[
                    _thumbnail(color.get("image_url"), image_cache),
                    _paragraph(
                        f"{color['name']}  /  {color['variant_count']} 款  /  小计 {color['quantity']} 件",
                        COLOR_STYLE,
                    ),
                ]],
                colWidths=[55, CONTENT_WIDTH - 55],
                hAlign="LEFT",
            )
            color_header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            story.append(color_header)
            story.append(Spacer(1, 6))
            story.append(_color_table(color))
            story.append(Spacer(1, 14))
    story.extend([
        PageBreak(),
        _paragraph("采购对数单", TITLE_STYLE),
        Spacer(1, 7),
        _paragraph(data['name'], BODY_STYLE),
        Spacer(1, 16),
        _count_sheet(products),
    ])
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue(), filename
