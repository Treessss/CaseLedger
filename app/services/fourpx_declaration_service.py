"""4PX 预申报检查与建单。

检查部分是只读的：按业务规则给待出库订单推荐物流产品、申报品名/单价和拦截原因。
建单部分（create_declarations）会真实调用 4PX 创建直发委托单，必须带 confirm 且由页面二次确认；
发件人/揽收信息使用 4PX 账号默认值（2026-10-31 生产试探确认）。
"""

import csv
import io
import json
import re
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from flask import g
from sqlalchemy.orm import selectinload

from app import db
from app.models import (FourPxDeclarationJob, FourPxDeclarationSetting, FourPxSmartRejection,
                       Order, Payment)
from app.services import address_validation
from app.services.shipping_queue_service import _shop_id
from app.utils.errors import ValidationError
from app.utils.order_lines import is_tip_line
from app.utils.timezone import beijing_date_from_utc

# 物流产品代码来自店铺 4PX 账号的实时产品列表（2026-10-30 查询确认）。
PRODUCTS = {
    "light": {"code": "S5058", "name": "联邮通轻小件-普货", "tracking": False},
    "standard": {"code": "QC", "name": "联邮通标准挂号-普货", "tracking": True},
    "smart": {"code": "S5191", "name": "联邮通智选产品-标准普货", "tracking": True},
    "island": {"code": "S5118", "name": "美国岛屿专线-普货", "tracking": True},
}

# 带支架/手机链的套装按标准挂号（QC）走：
# 1) 两件都是 Gradient Magnetic iPhone Case with Matching Stand；
# 2) Silicone iPhone Case with Stand & Camera Control + Gradient Magnetic 套装，
#    且每条商品变体都带 + Stand / + 手机链（Wrist Chain）。
SPECIAL_TITLE = "gradient magnetic iphone case with matching stand"
SPECIAL_SILICONE_TITLE = "silicone iphone case with stand"
STAND_CHAIN_MARKERS = ("+ stand", "+stand", "手机链", "wrist chain", "wristchain", "+ chain")


def _variant_has_stand_or_chain(variant_title):
    text = (variant_title or "").casefold()
    return any(marker in text for marker in STAND_CHAIN_MARKERS)

# 预报重按件数估算（克）。加拿大智选按业务口径 1 件 0.12kg、2 件 0.18kg，
# 其他产品只是预估值，4PX 到仓会实测（可用 ds.xms.order.weight 更新）。
WEIGHT_ONE_ITEM = 120
WEIGHT_TWO_ITEMS = 180
WEIGHT_EXTRA_ITEM = 120

# 目前只对这几条业务规则覆盖的国家出建议；其他国家提示人工处理。
SUPPORTED_COUNTRIES = {"US", "GB", "AU", "NZ", "CA", "NL"}
COUNTRY_NAMES = {
    "US": "美国", "GB": "英国", "AU": "澳大利亚", "NZ": "新西兰",
    "CA": "加拿大", "NL": "荷兰",
}

DECLARE_CURRENCY = "USD"
DEFAULT_DUTY_TYPE = "U"  # DDU：收件人支付关税

# 申报口径来自实际预报数据：2026-09 的 158 条 4PX 预报单 + 账单明细里的申报品名和单价。
# 手机壳：US/AU 1.20 USD，CA/GB/NZ 1.00 USD；手机链统一 1.00；手机保护膜 1.20。
# 钱包在账单明细里只出现过品名（Wallet），没有单价记录，先沿用同一国家口径并标注待确认。
DECLARE_CATEGORIES = (
    {"match": ("wallet", "card holder"), "cn": "钱包", "en": "Wallet",
     "prices": {"default": 1.00}},
    {"match": ("screen protector", "protective film", "tempered glass"),
     "cn": "手机保护膜", "en": "Mobile phone protective film", "prices": {"default": 1.20}},
    {"match": ("case",), "cn": "手机壳", "en": "Phone case",
     "prices": {"US": 1.20, "AU": 1.20, "CA": 1.00, "GB": 1.00, "NZ": 1.00, "NL": 1.00, "default": 1.00}},
    {"match": ("chain", "lanyard", "strap"), "cn": "手机链", "en": "phone chain",
     "prices": {"default": 1.00}},
    {"match": ("bracket", "stand"), "cn": "磁吸支架", "en": "Magnetic support bracket",
     "prices": {"default": 1.20}},
)
DECLARE_FALLBACK = {"cn": "手机壳", "en": "Phone case", "prices": {"default": 1.00}}

_POSTAL_DATA = None


def _postal_data():
    """加载随仓库发布的邮编规则（加拿大智选不可达 / 美国岛屿可服务）。"""
    global _POSTAL_DATA
    if _POSTAL_DATA is None:
        path = Path(__file__).resolve().parent.parent / "data" / "fourpx_postal_codes.json"
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        _POSTAL_DATA = {
            "CA": {str(code).upper() for code in raw.get("CA", {}).get("unreachable", [])},
            "US": {str(code).upper() for code in raw.get("US", {}).get("serviceable", [])},
        }
    return _POSTAL_DATA


def normalize_postal_code(country, raw):
    """邮编规范化：加拿大去空格，美国取前 5 位，其他统一大写去空格。"""
    value = re.sub(r"\s+", "", str(raw or "")).upper()
    if country == "CA":
        return value
    if country == "US":
        return value[:5] if value.isdigit() or value[:5].isdigit() else value
    return value


def is_us_island(country, postal):
    return country == "US" and normalize_postal_code(country, postal) in _postal_data()["US"]


def is_canada_smart_unreachable(country, postal):
    return country == "CA" and normalize_postal_code(country, postal) in _postal_data()["CA"]


def _items(order):
    lines = []
    for item in order.items or []:
        if is_tip_line(item):
            continue
        quantity = max(int(item.quantity or 0), 0)
        if not quantity:
            continue
        lines.append({
            "title": item.title or "未命名商品",
            "variant_title": item.variant_title or "",
            "sku": item.sku or "",
            "quantity": quantity,
            "unit_price": float(item.price or 0),
            "image_url": item.image_url or "",
        })
    return lines


def _quantity(lines):
    return sum(line["quantity"] for line in lines)


def _has_special_pair(lines):
    """带支架/手机链的套装按标准挂号，不按轻小件。"""
    positive = [line for line in lines if line["quantity"] > 0]
    if not positive:
        return False
    titles = [(line["title"] or "").casefold() for line in positive]
    gradient_matches = [line for line in positive
                        if SPECIAL_TITLE in (line["title"] or "").casefold()]
    # 两件都是 Gradient 套装（沿用原规则，不要求变体带标记）
    if _quantity(lines) == 2 and sum(line["quantity"] for line in gradient_matches) == 2:
        return True
    # Silicone 支架款 + Gradient 套装同单，且每条变体都带 + Stand / + 手机链
    has_silicone = any(SPECIAL_SILICONE_TITLE in title for title in titles)
    has_gradient = any(SPECIAL_TITLE in title for title in titles)
    variants_ok = all(_variant_has_stand_or_chain(line.get("variant_title")) for line in positive)
    return has_silicone and has_gradient and variants_ok


def _is_stand_set(lines):
    """两件以上、且每条都是支架款/手机链款（Silicone 支架款或 Gradient 套装款）。"""
    positive = [line for line in lines if line["quantity"] > 0]
    if not positive or _quantity(lines) < 2:
        return False
    titles_ok = all(
        any(keyword in (line["title"] or "").casefold()
            for keyword in (SPECIAL_SILICONE_TITLE, SPECIAL_TITLE))
        for line in positive
    )
    variants_ok = all(_variant_has_stand_or_chain(line.get("variant_title")) for line in positive)
    return titles_ok and variants_ok


def _normalized_postal(postal):
    return re.sub(r"\s+", "", str(postal or "")).upper()


def _smart_rejected(shop_id, country, postal):
    """该邮编之前被 4PX 以「无法提供派送服务」拒绝过，就直接按智选不可达处理。"""
    if not shop_id:
        return False
    return FourPxSmartRejection.query.filter_by(
        shop_id=shop_id, country=country, postal_code=_normalized_postal(postal),
        product_code=PRODUCTS["smart"]["code"],
    ).first() is not None


def _record_smart_rejection(shop_id, country, postal, detail):
    code = None
    message = str(detail or "")[:500]
    match = re.search(r"010109008", message)
    if match:
        code = "010109008"
    row = FourPxSmartRejection.query.filter_by(
        shop_id=shop_id, country=country, postal_code=_normalized_postal(postal),
        product_code=PRODUCTS["smart"]["code"],
    ).first()
    if row is None:
        row = FourPxSmartRejection(shop_id=shop_id, country=country,
                                   postal_code=_normalized_postal(postal),
                                   product_code=PRODUCTS["smart"]["code"])
        db.session.add(row)
    row.error_code = code
    row.error_message = message
    try:
        db.session.commit()
    except Exception:  # noqa: BLE001 - 缓存失败不影响建单
        db.session.rollback()


def select_product(country, quantity, special_pair, postal, stand_set=False, smart_blocked=False):
    """返回 (产品 key, 说明)；无法建议时 key 为 None。"""
    if not country:
        return None, "缺少收货国家"
    if country not in SUPPORTED_COUNTRIES:
        return None, f"「{country}」不在当前发货规则内，请人工处理"
    if quantity <= 0:
        return None, "订单没有可申报的商品件数"
    if country == "US":
        if is_us_island(country, postal):
            return "island", "邮编命中美国岛屿服务清单"
        if special_pair:
            return "standard", "带支架/手机链的套装按标准挂号"
        return ("light", "1–2 件普货走轻小件") if quantity <= 2 else ("standard", "3 件及以上走标准挂号")
    if country in ("GB", "AU"):
        if special_pair:
            return "standard", "带支架/手机链的套装按标准挂号"
        return ("light", "1–2 件普货走轻小件") if quantity <= 2 else ("standard", "3 件及以上走标准挂号")
    if country == "CA":
        # 两件以上且都是支架款/手机链款 → 标准挂号（不再走智选）
        if stand_set:
            return "standard", "两件以上带支架/手机链的套装走标准挂号"
        if smart_blocked or is_canada_smart_unreachable(country, postal):
            return ("light", "智选不可达邮编，1–2 件回退轻小件") if quantity <= 2 else (
                "standard", "智选不可达邮编，3 件及以上回退标准挂号")
        return "smart", "加拿大优先智选（邮编可服务），需要填重量"
    if country in ("NZ", "NL"):
        return "standard", "该国家统一标准挂号"
    return None, "没有匹配的发货规则"


def estimate_weight(quantity):
    if quantity <= 0:
        return 0
    if quantity == 1:
        return WEIGHT_ONE_ITEM
    if quantity == 2:
        return WEIGHT_TWO_ITEMS
    return WEIGHT_TWO_ITEMS + WEIGHT_EXTRA_ITEM * (quantity - 2)


def declaration_category(title):
    text = (title or "").casefold()
    for rule in DECLARE_CATEGORIES:
        if any(keyword in text for keyword in rule["match"]):
            return rule
    return DECLARE_FALLBACK


def declared_unit_price(rule, country):
    prices = rule["prices"]
    return float(prices.get(country, prices.get("default", 1.00)))


def declare_lines(lines, country):
    """按申报口径把订单商品归并成申报行：品名、数量、单价、金额。"""
    grouped = {}
    for line in lines:
        rule = declaration_category(line["title"])
        row = grouped.setdefault(rule["en"], {
            "name_cn": rule["cn"], "name_en": rule["en"],
            "quantity": 0, "unit_price": declared_unit_price(rule, country), "amount": 0.0,
        })
        row["quantity"] += line["quantity"]
        row["amount"] = round(row["quantity"] * row["unit_price"], 2)
    return list(grouped.values())


def declared_total(declare_rows):
    total = sum((Decimal(str(row["amount"])) for row in declare_rows), Decimal("0"))
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _recipient(order):
    address = dict(order.shipping_address or {})
    name = str(address.get("name") or order.customer_name or "").strip()
    # 4PX 面单按 last_name + first_name 的顺序打印；店铺后台人工建单时收件人姓名
    # 是整串填在 first_name（last_name 为空），所以这里也整串填，避免打印成
    # 「姓 名」倒序（例如 Helen Berry 打成 Berry Helen）。
    return {
        "first_name": name,
        "last_name": "",
        "company": str(address.get("company") or "").strip(),
        "phone": str(address.get("phone") or order.customer_phone or "").strip(),
        "email": str(address.get("email") or order.customer_email or "").strip(),
        "post_code": str(address.get("zip") or "").strip(),
        "country": str(address.get("country_code") or "").strip().upper(),
        "state": str(address.get("province") or address.get("province_code") or "").strip(),
        "city": str(address.get("city") or "").strip(),
        "district": "",
        "street": str(address.get("address1") or "").strip(),
        "house_number": "",
        "address2": str(address.get("address2") or "").strip(),
    }


def _address_issues(order, country):
    """4PX 收件地址校验：街道必须同时包含门牌号（数字）和街道名（字母）。"""
    address = order.shipping_address or {}
    street = " ".join(str(address.get(key) or "").strip()
                      for key in ("address1", "address2")).strip()
    issues = []
    if not street:
        issues.append("缺少收货地址")
    elif not (re.search(r"[0-9]", street) and re.search(r"[A-Za-z]", street)):
        issues.append("收货地址需同时包含门牌号和街道名（4PX 要求数字+字母），请核对")
    if country == "GB" and not (address.get("email") or order.customer_email):
        issues.append("英国订单需要收件人邮箱（4PX 要求电话+邮箱）")
    # 邮编库/州一致性/占位信息（GeoNames 邮编库 + 各国规则）
    for issue in address_validation.validate_recipient(order):
        if issue not in issues:
            issues.append(issue)
    return issues


def _reason_group(reasons):
    text = "；".join(reasons)
    if "国家" in text:
        return "country"
    if any(word in text for word in ("地址", "街道", "门牌", "邮编", "州", "省", "城市")):
        return "address"
    if "件数" in text:
        return "items"
    if "电话" in text or "邮箱" in text:
        return "contact"
    return "other"


def _blockers(order, country, quantity):
    reasons = []
    address = order.shipping_address or {}
    if not address.get("zip"):
        reasons.append("缺少收货邮编")
    if not (address.get("phone") or order.customer_phone):
        reasons.append("缺少收货电话（4PX 必填）")
    if country not in SUPPORTED_COUNTRIES:
        reasons.append("国家不在发货规则内，需人工处理")
    if quantity <= 0:
        reasons.append("订单没有可申报的商品件数")
    if _is_fully_refunded(order):
        reasons.append("订单已全额退款，无需申报")
    reasons.extend(_address_issues(order, country))
    return reasons


def _refund_amounts(order_ids):
    """批量取有效退款金额（shopify_refund 且 pending/success），按订单汇总绝对值。"""
    totals = {}
    if not order_ids:
        return totals
    rows = db.session.query(Payment.order_id, Payment.amount).filter(
        Payment.order_id.in_(order_ids),
        Payment.payment_method == "shopify_refund",
        Payment.status.in_(("pending", "success")),
        Payment.amount.isnot(None),
    ).all()
    for order_id, amount in rows:
        totals[order_id] = totals.get(order_id, Decimal("0")) + abs(Decimal(str(amount)))
    return totals


def _refund_covers_total(total, refunded):
    if refunded <= 0:
        return False
    total = Decimal(str(total or 0))
    return total <= 0 or refunded >= total


def _setting_row(shop_id):
    row = db.session.get(FourPxDeclarationSetting, shop_id)
    if row is None:
        row = FourPxDeclarationSetting(shop_id=shop_id, auto_writeback=False)
        db.session.add(row)
        db.session.commit()
    return row


def get_settings(args):
    shop_id = _shop_id(args.get("shop_id"))
    return _setting_row(shop_id).to_dict()


def save_settings(args):
    shop_id = _shop_id(args.get("shop_id"))
    enabled = str(args.get("auto_writeback")).lower() in ("1", "true", "yes", "on")
    row = _setting_row(shop_id)
    row.auto_writeback = enabled
    db.session.commit()
    return row.to_dict()


def _auto_writeback_enabled(shop_id):
    try:
        row = db.session.get(FourPxDeclarationSetting, shop_id)
        return bool(row and row.auto_writeback)
    except Exception:  # noqa: BLE001 - 开关读取失败不阻断建单
        return False


def _run_auto_writeback(shop_id, results):
    """建单成功后，按店铺开关把已齐全包裹回填 Shopify；失败只记录，不影响已建单结果。"""
    summary = {"enabled": False, "updated": 0, "already_fulfilled": 0, "awaiting": 0, "failed": 0}
    if not _auto_writeback_enabled(shop_id):
        return summary
    from app.services.shopify_fulfillment_service import writeback_tracking

    summary["enabled"] = True
    for item in results:
        if item.get("status") not in ("created", "created_after_retry", "already_exists"):
            continue
        if not item.get("parcel_id"):
            continue
        try:
            writeback = writeback_tracking(item["order_id"])
            db.session.commit()
            status = str(writeback.get("status") or "")
            item["shopify_status"] = status
            if status == "updated":
                summary["updated"] += 1
            elif status == "already_fulfilled":
                summary["already_fulfilled"] += 1
        except Exception as exc:  # noqa: BLE001 - 回填失败不回滚建单
            db.session.rollback()
            message = str(exc)
            item["shopify_status"] = "failed"
            item["shopify_error"] = message[:200]
            if "面单尚未齐全" in message:
                summary["awaiting"] += 1
            else:
                summary["failed"] += 1
    return summary


def _is_fully_refunded(order):
    totals = _refund_amounts([order.id])
    return _refund_covers_total(order.total_price, totals.get(order.id, Decimal("0")))


def _candidate_orders(shop_id, search=""):
    """待出库、已付款、未取消、无包裹、且没有全额退款的订单。"""
    orders = _candidate_query(shop_id, search).all()
    totals = _refund_amounts([order.id for order in orders])
    return [order for order in orders
            if not _refund_covers_total(order.total_price, totals.get(order.id, Decimal("0")))]


def _candidate_query(shop_id, search=""):
    """待出库、已付款、未取消、还没有包裹记录的订单。"""
    query = Order.query.options(selectinload(Order.items)).filter(
        Order.shop_id == shop_id,
        Order.warehouse_status == "pending",
        Order.cancelled_at.is_(None),
        Order.financial_status.in_(("paid", "partially_paid", "partially_refunded")),
        ~Order.parcels.any(),
    )
    if search:
        query = query.filter(Order.order_number.ilike(f"%{search}%"))
    return query.order_by(Order.order_date.asc(), Order.id.asc())


def _build_row(order):
    lines = _items(order)
    quantity = _quantity(lines)
    address = order.shipping_address or {}
    country = str(address.get("country_code") or "").strip().upper()
    postal = str(address.get("zip") or "").strip()
    special_pair = _has_special_pair(lines)
    stand_set = _is_stand_set(lines)
    smart_blocked = country == "CA" and _smart_rejected(order.shop_id, country, postal)
    product_key, reason = select_product(country, quantity, special_pair, postal, stand_set, smart_blocked)
    blockers = _blockers(order, country, quantity)
    if blockers:
        product_key = None
    weight = estimate_weight(quantity)
    declare_rows = declare_lines(lines, country)
    value = declared_total(declare_rows)
    is_ready = product_key is not None and not blockers
    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "order_date": order.order_date.isoformat() if order.order_date else None,
        "country": country,
        "country_name": COUNTRY_NAMES.get(country, country or "未知"),
        "postal_code": postal,
        "quantity": quantity,
        "special_pair": special_pair,
        "product": (
            {"key": product_key, **PRODUCTS[product_key]} if product_key else None
        ),
        "product_reason": reason,
        "weight_grams": weight,
        "declared_value": value,
        "declared_currency": DECLARE_CURRENCY,
        "declared_items": declare_rows,
        "duty_type": DEFAULT_DUTY_TYPE,
        "ready": is_ready,
        "reason_group": None if is_ready else _reason_group(blockers),
        "blockers": blockers,
        "items": lines,
        "recipient": _recipient(order),
        "payload_preview": _payload_preview(order, lines, product_key, weight, value, declare_rows) if is_ready else None,
    }


def _rules():
    return {
        "products": {key: {"code": product["code"], "name": product["name"]} for key, product in PRODUCTS.items()},
        "weight": {"one_item": WEIGHT_ONE_ITEM, "two_items": WEIGHT_TWO_ITEMS, "extra_item": WEIGHT_EXTRA_ITEM},
        "declare": {
            "currency": DECLARE_CURRENCY,
            "note": "按 158 条历史 4PX 预报单与账单明细：US/AU 1.20、CA/GB/NZ 1.00；手机链 1.00、保护膜 1.20；钱包待确认",
            "categories": [
                {"cn": rule["cn"], "en": rule["en"], "prices": rule["prices"]}
                for rule in (*DECLARE_CATEGORIES, DECLARE_FALLBACK)
            ],
        },
        "duty_type": DEFAULT_DUTY_TYPE,
        "special_title": SPECIAL_TITLE,
        "reason_groups": {"address": "地址问题", "contact": "联系方式/邮编缺失",
                          "country": "国家不在规则内", "items": "商品件数为 0", "other": "其他"},
    }


def _notices():
    return []


def _summary(rows):
    ready = sum(1 for row in rows if row["ready"])
    groups = {}
    for row in rows:
        if row["reason_group"]:
            groups[row["reason_group"]] = groups.get(row["reason_group"], 0) + 1
    return {"candidates": len(rows), "ready": ready, "blocked": len(rows) - ready,
            "groups": groups}


def _order_range(args):
    def parse(key):
        try:
            return int(str(args.get(key)).strip())
        except (TypeError, ValueError):
            return None
    return parse("order_from"), parse("order_to")


def _in_order_range(number, low, high):
    try:
        value = int(str(number).lstrip("#"))
    except (TypeError, ValueError):
        return False
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def _filter_range(rows, args):
    low, high = _order_range(args)
    if low is None and high is None:
        return rows
    return [row for row in rows if _in_order_range(row["order_number"], low, high)]


def _filter_blocked(rows, args):
    if str(args.get("blocked_only") or "").lower() in ("1", "true", "yes"):
        rows = [row for row in rows if not row["ready"]]
    group = str(args.get("reason_group") or "").strip()
    if group:
        rows = [row for row in rows if row.get("reason_group") == group]
    return rows


def _positive(raw, default):
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return default


def check(args):
    """返回待出库订单的预申报检查结果；不调用 4PX、不写数据库。"""
    shop_id = _shop_id(args.get("shop_id"))
    page = _positive(args.get("page"), 1)
    per_page = min(_positive(args.get("per_page"), 20), 100)
    search = str(args.get("search") or "").strip()[:120]
    ranged = _filter_range([_build_row(order) for order in _candidate_orders(shop_id, search)], args)
    summary = _summary(ranged)
    status = str(args.get("status") or "").strip().lower()
    if status == "ready":
        rows = [row for row in ranged if row["ready"]]
    elif status == "blocked" or str(args.get("blocked_only") or "").lower() in ("1", "true", "yes"):
        rows = [row for row in ranged if not row["ready"]]
    else:
        rows = list(ranged)
    group = str(args.get("reason_group") or "").strip()
    if group:
        rows = [row for row in rows if row["reason_group"] == group]
    total = len(rows)
    pages = (total + per_page - 1) // per_page if total else 1
    start = (page - 1) * per_page
    return {
        "summary": summary,
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages},
        "rules": _rules(),
        "notices": _notices(),
        "orders": rows[start:start + per_page],
    }


def export_csv(args):
    """把检查结果导出成 CSV（人工处理用），最多 2000 行。"""
    shop_id = _shop_id(args.get("shop_id"))
    search = str(args.get("search") or "").strip()[:120]
    rows = _filter_range([_build_row(order) for order in _candidate_orders(shop_id, search)[:2000]], args)
    status = str(args.get("status") or "").strip().lower()
    if status == "ready":
        rows = [row for row in rows if row["ready"]]
    elif status == "blocked" or str(args.get("blocked_only") or "").lower() in ("1", "true", "yes"):
        rows = [row for row in rows if not row["ready"]]
    group = str(args.get("reason_group") or "").strip()
    if group:
        rows = [row for row in rows if row["reason_group"] == group]
    buffer = io.StringIO()
    buffer.write("\ufeff")  # 让 Excel 正确识别 UTF-8
    writer = csv.writer(buffer)
    writer.writerow(["订单号", "订单日期", "国家", "邮编", "件数", "建议产品代码",
                     "建议产品名称", "规则依据", "预报重(克)", "申报价值", "币种", "状态", "拦截原因"])
    for row in rows:
        product = row["product"] or {}
        writer.writerow([
            row["order_number"],
            (row["order_date"] or "")[:10],
            row["country"] or "",
            row["postal_code"],
            row["quantity"],
            product.get("code", ""),
            product.get("name", ""),
            row["product_reason"],
            row["weight_grams"],
            row["declared_value"],
            row["declared_currency"],
            "可预申报" if row["ready"] else "需人工处理",
            "；".join(row["blockers"]),
        ])
    filename = f"4px-declaration-check-{beijing_date_from_utc(datetime.utcnow())}.csv"
    return buffer.getvalue().encode("utf-8"), filename


def plan_batch(payload):
    """批量预申报计划：只汇总产品/重量/申报价值，不调用 4PX。"""
    shop_id = _shop_id(payload.get("shop_id"))
    raw_ids = payload.get("order_ids") or []
    if not isinstance(raw_ids, list):
        raise ValidationError("请选择要预申报的订单")
    selected_ids = []
    for value in raw_ids:
        try:
            order_id = int(value)
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in selected_ids:
            selected_ids.append(order_id)
    if not selected_ids:
        raise ValidationError("请选择要预申报的订单")
    if len(selected_ids) > 200:
        raise ValidationError("一次最多生成 200 单的预申报清单，请分批处理")
    orders = Order.query.options(selectinload(Order.items)).filter(
        Order.id.in_(selected_ids),
        Order.shop_id == shop_id,
        Order.warehouse_status == "pending",
        Order.cancelled_at.is_(None),
        Order.financial_status.in_(("paid", "partially_paid", "partially_refunded")),
        ~Order.parcels.any(),
    ).all()
    found = {order.id for order in orders}
    rows = [_build_row(order) for order in orders]
    groups = {}
    for row in rows:
        if not row["ready"]:
            continue
        code = row["product"]["code"]
        group = groups.setdefault(code, {
            "product": row["product"], "order_numbers": [], "order_count": 0,
            "quantity": 0, "weight_grams": 0, "declared_value": 0.0,
        })
        group["order_numbers"].append(row["order_number"])
        group["order_count"] += 1
        group["quantity"] += row["quantity"]
        group["weight_grams"] += row["weight_grams"]
        group["declared_value"] = round(group["declared_value"] + row["declared_value"], 2)
    eligible = [row for row in rows if row["ready"]]
    return {
        "summary": {
            "requested": len(selected_ids), "matched": len(rows), "eligible": len(eligible),
            "blocked": len(rows) - len(eligible),
            "invalid": len([order_id for order_id in selected_ids if order_id not in found]),
        },
        "invalid_order_ids": [order_id for order_id in selected_ids if order_id not in found],
        "groups": sorted(groups.values(), key=lambda group: group["product"]["code"]),
        "orders": rows,
    }




def _declared_product_list(declare_rows, lines):
    """商品信息也按申报口径（品名/申报单价），不再抄订单商品原名和原价。

    sku_code 仍优先用订单实际 SKU，方便对货；同一申报品名合并多行时取第一个 SKU。
    """
    by_category = {}
    for line in lines:
        rule = declaration_category(line["title"])
        by_category.setdefault(rule["en"], []).append(line)
    product_list = []
    for row in declare_rows:
        sku = ""
        for line in by_category.get(row["name_en"], []):
            sku = str(line.get("sku") or line.get("variant_title") or line.get("title") or "").strip()
            if sku:
                break
        product_list.append({
            "sku_code": sku or row["name_en"].lower().replace(" ", "-"),
            "product_name": row["name_en"],
            "product_unit_price": row["unit_price"],
            "currency": DECLARE_CURRENCY,
            "qty": row["quantity"],
        })
    return product_list


def _payload_preview(order, lines, product_key, weight, value, declare_rows):
    """给页面展开看的请求体骨架；真实下单前还要补发件人和税号。"""
    return {
        "ref_no": order.order_number,
        "logistics_product_code": PRODUCTS[product_key]["code"],
        "duty_type": DEFAULT_DUTY_TYPE,
        "parcel_list": [{
            "weight": weight,
            "parcel_value": value,
            "currency": DECLARE_CURRENCY,
            "include_battery": "N",
            "product_list": _declared_product_list(declare_rows, lines),
            "declare_product_info": [{
                "declare_product_name_cn": row["name_cn"],
                "declare_product_name_en": row["name_en"],
                "declare_product_code_qty": row["quantity"],
                "declare_unit_price_import": row["unit_price"],
                "currency_import": DECLARE_CURRENCY,
                "declare_unit_price_export": row["unit_price"],
                "currency_export": DECLARE_CURRENCY,
                "origin_country": "CN",
            } for row in declare_rows],
        }],
        "recipient_info": _recipient(order),
        "deliver_type_info": {"deliver_type": "2", "pick_up_info": "（待配置揽收时间与地址）"},
        "sender": "（待配置发件人信息）",
        "is_insure": "N",
    }


# ---------------------------------------------------------------------------
# 真实预申报（向 4PX 创建直发委托单）
#
# 2026-10-31 在生产账号上有一次可取消的字段试探确认：
#   - sender / return_info / deliver_to_recipient_info 允许传空对象，4PX 用账号默认值；
#   - deliver_type_info 传 {"deliver_type": "2"}（上门揽收）即可；
#   - declare_product_info 至少要 中英文品名、数量、进口/出口申报单价与币种；
#   - 成功后立即取消，4PX 端查询为空，未留下委托单。
# ---------------------------------------------------------------------------

# 发件人 / 退件地址：4PX 后台设置（2026-11-01 提供）。
# 4PX 要求发件人省/州、城市只能填英文，所以用 Guangdong / Shenzhen。
SENDER_INFO = {
    "first_name": "MAI", "last_name": "", "company": "",
    "phone": "1111111111", "email": "", "post_code": "518000", "country": "CN",
    "state": "Guangdong", "city": "Shenzhen", "district": "",
    "street": "SHENZHENSHIHUANYUXINGCHENG GFY TINGYUJU", "house_number": "",
}
RETURN_ADDRESS = {
    "first_name": "LIU", "last_name": "", "company": "",
    "phone": "15814459815", "email": "", "post_code": "518000", "country": "CN",
    "state": "Guangdong", "city": "Shenzhen", "district": "Longgang",
    "street": "Buji Street Wenjing Community Bulong Road 31 Leshun Building 317",
    "house_number": "",
}
RETURN_INFO = {"is_return_on_domestic": "Y", "domestic_return_addr": RETURN_ADDRESS,
               "is_return_on_oversea": "N"}

PARCEL_LENGTH_CM = 10
PARCEL_WIDTH_CM = 10
PARCEL_HEIGHT_CM = 2


RECIPIENT_FIELDS = ("first_name", "last_name", "company", "phone", "email",
                    "post_code", "country", "state", "city", "district", "street", "house_number")


def _create_payload(order, row):
    """按检查结果拼 4PX 创建直发委托单请求体（发件人走 4PX 账号默认值）。"""
    items = row["items"]
    recipient = {key: row["recipient"].get(key, "") for key in RECIPIENT_FIELDS}
    return {
        "ref_no": str(order.order_number).lstrip("#"),
        "business_type": "BDS",
        "duty_type": DEFAULT_DUTY_TYPE,
        "cargo_type": "5",
        "logistics_service_info": {
            "logistics_product_code": row["product"]["code"],
            "customs_service": "N",
            "signature_service": "N",
            "value_added_services": "",
        },
        "parcel_list": [{
            "weight": row["weight_grams"],
            "length": PARCEL_LENGTH_CM,
            "width": PARCEL_WIDTH_CM,
            "height": PARCEL_HEIGHT_CM,
            "parcel_value": row["declared_value"],
            "currency": DECLARE_CURRENCY,
            "include_battery": "N",
            "product_list": _declared_product_list(row["declared_items"], items),
            "declare_product_info": [{
                "declare_product_name_cn": declared["name_cn"],
                "declare_product_name_en": declared["name_en"],
                "declare_product_code_qty": declared["quantity"],
                "declare_unit_price_import": declared["unit_price"],
                "currency_import": DECLARE_CURRENCY,
                "declare_unit_price_export": declared["unit_price"],
                "currency_export": DECLARE_CURRENCY,
            } for declared in row["declared_items"]],
        }],
        "is_insure": "N",
        "insurance_info": {},
        "recipient_info": recipient,
        "sender": dict(SENDER_INFO),
        "return_info": {**RETURN_INFO, "domestic_return_addr": dict(RETURN_ADDRESS)},
        "deliver_type_info": {"deliver_type": "2"},
        "deliver_to_recipient_info": {},
    }



def _existing_tracking(client, ref_no):
    """创建报错/超时后按 ref_no 回查 4PX，避免重复建单。"""
    try:
        result = client.get_order(ref_no)
    except Exception:  # noqa: BLE001 - 回查失败只影响幂等判断，不吞掉原错误
        return None
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    for item in result or []:
        if not isinstance(item, dict):
            continue
        info = item.get("consignment_info") or {}
        tracking = str(info.get("4px_tracking_no") or "").strip()
        if tracking:
            return {"4px_tracking_no": tracking, "label_barcode": tracking,
                    "ref_no": info.get("ref_no")}
    return None


def start_declaration_job(args):
    """创建异步预申报任务：后台按 50 一批建单，页面可轮询进度、关页面不丢。"""
    shop_id = _shop_id(args.get("shop_id"))
    if str(args.get("confirm")).lower() not in ("1", "true", "yes"):
        raise ValidationError("真实预申报会向 4PX 创建委托单，请先确认")
    raw_ids = args.get("order_ids") or []
    if not isinstance(raw_ids, list):
        raise ValidationError("请选择要预申报的订单")
    selected_ids = []
    for value in raw_ids:
        try:
            order_id = int(value)
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in selected_ids:
            selected_ids.append(order_id)
    if not selected_ids:
        raise ValidationError("请选择要预申报的订单")
    if len(selected_ids) > 2000:
        raise ValidationError("一次最多 2000 单，请分批处理")
    created_by = None
    try:
        created_by = (g.current_user or {}).get("user_id")
    except Exception:  # noqa: BLE001
        created_by = None
    job = FourPxDeclarationJob(id=str(uuid.uuid4()), shop_id=shop_id, status="queued",
                               order_ids=selected_ids, requested=len(selected_ids),
                               results=[], created_by=created_by)
    db.session.add(job)
    db.session.commit()
    try:
        from app.tasks import run_fourpx_declaration_job_task
        run_fourpx_declaration_job_task.apply_async(args=[job.id])
    except Exception as exc:  # noqa: BLE001 - 入队失败要让页面知道
        job.status = "failed"
        job.error = "预申报任务入队失败，请稍后重试"
        db.session.commit()
        raise ValidationError(str(exc)[:200]) from exc
    return job.to_dict()


def get_declaration_job(args):
    shop_id = _shop_id(args.get("shop_id"))
    job = db.session.get(FourPxDeclarationJob, str(args.get("job_id")))
    if job is None or job.shop_id != shop_id:
        raise ValidationError("预申报任务不存在")
    return job.to_dict()


def list_declaration_jobs(args):
    shop_id = _shop_id(args.get("shop_id"))
    jobs = (FourPxDeclarationJob.query.filter_by(shop_id=shop_id)
            .order_by(FourPxDeclarationJob.created_at.desc()).limit(5).all())
    return {"jobs": [job.to_dict() for job in jobs]}


def process_declaration_job(job_id):
    """Celery 里执行：按 50 一批真实建单，并持续回写进度。"""
    job = db.session.get(FourPxDeclarationJob, job_id)
    if job is None or job.status in ("done", "failed"):
        return
    job.status = "running"
    db.session.commit()
    # Celery worker 里没有登录用户；用任务创建者/管理员身份通过店铺权限校验。
    g.current_user = {"authority": "admin", "user_id": job.created_by or 1}
    ids = [int(value) for value in (job.order_ids or [])]
    processed = int(job.processed or 0)
    created = int(job.created_count or 0)
    failed = int(job.failed_count or 0)
    results = list(job.results or [])
    try:
        for start in range(processed, len(ids), 50):
            chunk = ids[start:start + 50]
            result = create_declarations({"shop_id": job.shop_id, "order_ids": chunk, "confirm": True})
            results.extend(result["results"])
            processed = start + len(chunk)
            created += result["summary"]["created"]
            failed += result["summary"]["failed"]
            job.results = results
            job.processed = processed
            job.created_count = created
            job.failed_count = failed
            db.session.commit()
        job.status = "done"
        job.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 - 记录失败并停止，避免 Celery 自动重试造成重复建单
        db.session.rollback()
        job = db.session.get(FourPxDeclarationJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:400]
            job.results = results
            job.processed = processed
            job.created_count = created
            job.failed_count = failed
            db.session.commit()


def sync_manual_declarations(args, *, client_factory=None):
    """把 4PX 上已存在的委托单同步成本地包裹（用于批次失败后人工去 4PX 申报的订单）。

    只按客户单号（订单号）回查 4PX；不调用面单接口，所以不会让 4PX 显示已打印。
    """
    from app.models import Shop, ShippingParcel
    from app.services.fourpx_client import FourPxClient, FourPxError

    shop_id = _shop_id(args.get("shop_id"))
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise ValidationError("店铺不存在")
    try:
        app_key, app_secret = shop.get_fourpx_credentials()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(str(exc)) from exc
    factory = client_factory or FourPxClient

    order_ids = []
    job_id = str(args.get("job_id") or "").strip()
    if job_id:
        job = db.session.get(FourPxDeclarationJob, job_id)
        if job is None or job.shop_id != shop_id:
            raise ValidationError("预申报任务不存在")
        order_ids = [row.get("order_id") for row in (job.results or [])
                     if row.get("status") in ("failed", "skipped") and row.get("order_id")]
    else:
        raw_ids = args.get("order_ids") or []
        if not isinstance(raw_ids, list):
            raise ValidationError("请选择要同步的订单")
        for value in raw_ids:
            try:
                order_id = int(value)
            except (TypeError, ValueError):
                continue
            if order_id > 0 and order_id not in order_ids:
                order_ids.append(order_id)
    if not order_ids:
        raise ValidationError("没有可同步的订单：请选择订单，或先查看失败任务")
    if len(order_ids) > 200:
        raise ValidationError("一次最多同步 200 单，请分批处理")

    orders = Order.query.filter(Order.id.in_(order_ids), Order.shop_id == shop_id).all()
    by_id = {order.id: order for order in orders}
    client = factory(app_key, app_secret, environment=shop.fourpx_environment)
    writeback_enabled = _auto_writeback_enabled(shop_id)
    results = []
    bound = already = not_found = failed = 0

    for order_id in order_ids:
        order = by_id.get(order_id)
        if order is None:
            results.append({"order_id": order_id, "status": "invalid", "error": "订单不存在或不属于当前店铺"})
            failed += 1
            continue
        existing_parcel = ShippingParcel.query.filter_by(order_id=order.id).first()
        if existing_parcel is not None:
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "already_bound", "tracking_number": existing_parcel.label_number})
            already += 1
            continue
        if order.cancelled_at or _is_fully_refunded(order):
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "skipped", "error": "订单已取消或已全额退款"})
            failed += 1
            continue
        ref_no = str(order.order_number).lstrip("#")
        try:
            found = _existing_tracking(client, ref_no)
        except FourPxError as exc:
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "failed", "error": str(exc)[:200]})
            failed += 1
            continue
        if not found or not found.get("4px_tracking_no"):
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "not_found", "error": "4PX 没有该订单的委托单，请确认是否已用客户单号申报"})
            not_found += 1
            continue
        tracking = found["4px_tracking_no"]
        if ShippingParcel.query.filter_by(shop_id=shop_id, label_number=tracking).first() is not None:
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "conflict", "error": f"4PX 单号 {tracking} 已被其他包裹占用"})
            failed += 1
            continue
        parcel = _save_parcel(shop_id, order, {"4px_tracking_no": tracking, "label_barcode": None})
        item = {"order_id": order.id, "order_number": order.order_number, "status": "bound",
                "parcel_id": parcel.id, "tracking_number": tracking}
        if writeback_enabled:
            try:
                from app.services.shopify_fulfillment_service import writeback_tracking
                writeback = writeback_tracking(order.id)
                db.session.commit()
                item["shopify_status"] = writeback.get("status")
            except Exception as exc:  # noqa: BLE001 - 回填失败不影响绑定
                db.session.rollback()
                item["shopify_status"] = "failed"
                item["shopify_error"] = str(exc)[:200]
        bound += 1
        results.append(item)

    return {
        "summary": {"requested": len(order_ids), "bound": bound, "already_bound": already,
                    "not_found": not_found, "failed": failed},
        "results": results,
    }


def create_declarations(args, *, client_factory=None):
    """向 4PX 批量创建直发委托单（真实建单，需 confirm=true）。

    只处理「可预申报」的订单；已绑定包裹的直接跳过；报错时先按 ref_no 回查，
    能查到就按已建单处理，避免重复创建。
    """
    from app.models import ShippingParcel  # 局部导入，避免循环依赖
    from app.services.fourpx_client import FourPxClient, FourPxError

    shop_id = _shop_id(args.get("shop_id"))
    if str(args.get("confirm")).lower() not in ("1", "true", "yes"):
        raise ValidationError("真实预申报会向 4PX 创建委托单，请先确认")
    raw_ids = args.get("order_ids") or []
    if not isinstance(raw_ids, list):
        raise ValidationError("请选择要预申报的订单")
    selected_ids = []
    for value in raw_ids:
        try:
            order_id = int(value)
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in selected_ids:
            selected_ids.append(order_id)
    low, high = _order_range(args)
    if not selected_ids and (low is not None or high is not None):
        candidates = _filter_range(
            [_build_row(order) for order in _candidate_orders(shop_id)], args)
        selected_ids = [row["order_id"] for row in candidates if row["ready"]][:50]
    if not selected_ids:
        raise ValidationError("请选择要预申报的订单，或填写订单号范围")
    if len(selected_ids) > 50:
        raise ValidationError("一次最多向 4PX 建 50 单，请分批处理")

    from app.models import Shop
    shop = db.session.get(Shop, shop_id)
    if shop is None:
        raise ValidationError("店铺不存在")
    try:
        app_key, app_secret = shop.get_fourpx_credentials()
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(str(exc)) from exc

    factory = client_factory or FourPxClient
    client = factory(app_key, app_secret, environment=shop.fourpx_environment)

    orders = Order.query.options(selectinload(Order.items)).filter(
        Order.id.in_(selected_ids),
        Order.shop_id == shop_id,
    ).all()
    by_id = {order.id: order for order in orders}
    results = []
    created = 0
    for order_id in selected_ids:
        order = by_id.get(order_id)
        if order is None:
            results.append({"order_id": order_id, "status": "invalid", "error": "订单不存在或不属于当前店铺"})
            continue
        if ShippingParcel.query.filter_by(order_id=order.id).first() is not None:
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "skipped", "error": "订单已有包裹记录"})
            continue
        row = _build_row(order)
        if not row["ready"]:
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "skipped", "error": "；".join(row["blockers"]) or "不满足预申报条件"})
            continue
        ref_no = str(order.order_number).lstrip("#")
        existing = _existing_tracking(client, ref_no)
        if existing:
            parcel = _save_parcel(shop_id, order, existing)
            created += 1
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "already_exists", "parcel_id": parcel.id,
                            "tracking_number": existing["4px_tracking_no"]})
            continue
        try:
            payload = client.create_order(_create_payload(order, row))
        except FourPxError as exc:
            existing = _existing_tracking(client, ref_no)
            if existing:
                parcel = _save_parcel(shop_id, order, existing)
                created += 1
                results.append({"order_id": order.id, "order_number": order.order_number,
                                "status": "created_after_retry", "parcel_id": parcel.id,
                                "tracking_number": existing["4px_tracking_no"]})
            else:
                detail = getattr(exc, "details", None) or str(exc)
                # 加拿大智选邮编不可派送：记录该邮编，并自动用轻小件(S5058)重试一次
                if (row.get("country") == "CA" and row.get("product")
                        and row["product"].get("key") == "smart"
                        and "无法提供派送服务" in detail):
                    _record_smart_rejection(shop_id, "CA", order.shipping_address.get("zip") if isinstance(order.shipping_address, dict) else "",
                                            detail)
                    fallback_row = {
                        **row,
                        "product": {"key": "light", **PRODUCTS["light"]},
                        "product_reason": "智选邮编不可达，自动回退轻小件",
                    }
                    try:
                        fallback_data = client.create_order(_create_payload(order, fallback_row))
                        fb = fallback_data if isinstance(fallback_data, dict) else {}
                        if isinstance(fallback_data, str):
                            try:
                                fb = json.loads(fallback_data)
                            except ValueError:
                                fb = {}
                        fallback_tracking = str(fb.get("4px_tracking_no") or "").strip()
                        if fallback_tracking:
                            fb_barcode = fb.get("label_barcode") or None
                            if fb_barcode and fb_barcode == fallback_tracking:
                                fb_barcode = None
                            parcel = _save_parcel(shop_id, order, {
                                "4px_tracking_no": fallback_tracking,
                                "label_barcode": fb_barcode,
                            })
                            created += 1
                            results.append({"order_id": order.id, "order_number": order.order_number,
                                            "status": "created_fallback", "parcel_id": parcel.id,
                                            "tracking_number": fallback_tracking,
                                            "fallback_reason": "智选邮编不可达，已自动回退轻小件(S5058)"})
                        else:
                            results.append({"order_id": order.id, "order_number": order.order_number,
                                            "status": "failed",
                                            "error": "智选邮编不可达，回退轻小件后 4PX 未返回单号，请核对平台记录"})
                    except FourPxError as fallback_exc:
                        fallback_detail = getattr(fallback_exc, "details", None) or str(fallback_exc)
                        results.append({"order_id": order.id, "order_number": order.order_number,
                                        "status": "failed",
                                        "error": f"智选失败：{detail[:120]}；回退轻小件也失败：{fallback_detail[:120]}"})
                else:
                    results.append({"order_id": order.id, "order_number": order.order_number,
                                    "status": "failed", "error": detail[:300]})
            continue
        data = payload if isinstance(payload, dict) else {}
        if isinstance(payload, str):
            try:
                data = json.loads(payload)
            except ValueError:
                data = {}
        tracking = str(data.get("4px_tracking_no") or "").strip()
        if not tracking:
            results.append({"order_id": order.id, "order_number": order.order_number,
                            "status": "failed", "error": "4PX 未返回跟踪号，请核对平台记录"})
            continue
        label_barcode = data.get("label_barcode") or None
        if label_barcode and label_barcode == tracking:
            # 建单返回的条码等于运单号时只是占位，实体面单条码在打印时才取，
            # 避免申报即调用面单接口、4PX 后台显示已打印。
            label_barcode = None
        parcel = _save_parcel(shop_id, order, {"4px_tracking_no": tracking,
                                              "label_barcode": label_barcode})
        created += 1
        results.append({"order_id": order.id, "order_number": order.order_number,
                        "status": "created", "parcel_id": parcel.id, "tracking_number": tracking})
    # 不再在申报时调用面单接口（避免 4PX 后台提前显示「已打印」）。
    # 实体面单条码在员工实际打印时获取：发货中心批量 PDF / 订单详情「获取面单」会回填。
    auto_writeback = _run_auto_writeback(shop_id, results)
    return {
        "summary": {"requested": len(selected_ids), "created": created, "failed": len(results) - created},
        "barcode_sync": {"updated": 0, "failed": 0},
        "auto_writeback": auto_writeback,
        "results": results,
    }


def _save_parcel(shop_id, order, info):
    from app.models import ShippingParcel
    barcode = info.get("label_barcode")
    if barcode and barcode == info["4px_tracking_no"]:
        # 等于运单号只是占位，不是实体面单条码；打印时再补真实条码。
        barcode = None
    parcel = ShippingParcel(
        shop_id=shop_id, order_id=order.id,
        label_number=info["4px_tracking_no"],
        label_barcode=barcode,
        source="4px",
    )
    db.session.add(parcel)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise ValidationError("4PX 已建单，但本地包裹单号保存失败，请人工核对")
    return parcel
