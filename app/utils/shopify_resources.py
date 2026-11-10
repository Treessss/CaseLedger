"""从 Shopify 订单对象里取「客户信息 / 收货地址 / 商品明细」。

兼容两种输入：
    - shopify-python 的 Resource 对象（嵌套层同样是 Resource）
    - REST 接口返回的原始 dict（回填脚本用这个，订单列表接口一次就能拿全）
"""

from decimal import Decimal, InvalidOperation


class WebhookResource:
    """Expose REST-shaped JSON like a Shopify Resource without remote lazy methods."""

    def __init__(self, attributes):
        self.attributes = attributes

    def __getattr__(self, name):
        value = self.attributes.get(name)
        if isinstance(value, dict):
            return WebhookResource(value)
        if isinstance(value, list):
            return [WebhookResource(item) if isinstance(item, dict) else item for item in value]
        return value

# 收货地址里需要留存的字段（其余字段体积大且用不上）
ADDRESS_FIELDS = (
    "name",
    "company",
    "phone",
    "address1",
    "address2",
    "city",
    "province",
    "province_code",
    "country",
    "country_code",
    "zip",
)


def to_plain(value):
    """把 shopify-python 的 Resource 对象递归还原成 dict / list / 基本类型。"""
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    attributes = getattr(value, "attributes", None)
    if isinstance(attributes, dict):
        return {key: to_plain(item) for key, item in attributes.items()}
    return value


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _full_name(customer):
    if not isinstance(customer, dict):
        return None
    parts = [customer.get("first_name"), customer.get("last_name")]
    name = " ".join(part.strip() for part in parts if part and part.strip())
    return name or None


def extract_customer_info(order):
    """返回 email / name / phone / shipping_address（取不到就是 None）。"""
    empty = {"email": None, "name": None, "phone": None, "shipping_address": None}
    plain = to_plain(order)
    if not isinstance(plain, dict):
        return empty

    customer = plain.get("customer") or {}
    shipping = plain.get("shipping_address") or plain.get("billing_address") or {}

    email = plain.get("email") or (customer.get("email") if isinstance(customer, dict) else None)
    name = _full_name(customer) or (shipping.get("name") if isinstance(shipping, dict) else None)
    phone = plain.get("phone")
    if not phone and isinstance(shipping, dict):
        phone = shipping.get("phone")
    if not phone and isinstance(customer, dict):
        phone = customer.get("phone")

    address = None
    if isinstance(shipping, dict):
        filled = {field: shipping.get(field) for field in ADDRESS_FIELDS if shipping.get(field)}
        address = filled or None

    return {"email": email, "name": name, "phone": phone, "shipping_address": address}


def extract_line_items(order):
    """订单商品明细（每行的商品、规格、数量、单价、折扣）。"""
    plain = to_plain(order)
    if not isinstance(plain, dict):
        return []

    rows = []
    for line_item in plain.get("line_items") or []:
        if not isinstance(line_item, dict) or line_item.get("id") is None:
            continue
        discount = line_item.get("total_discount")
        if discount in (None, ""):
            discount_set = line_item.get("total_discount_set") or {}
            discount = ((discount_set.get("shop_money") or {}) if isinstance(discount_set, dict) else {}).get(
                "amount"
            )
        rows.append(
            {
                "shopify_line_item_id": str(line_item["id"]),
                "shopify_product_id": str(line_item["product_id"]) if line_item.get("product_id") else None,
                "shopify_variant_id": str(line_item["variant_id"]) if line_item.get("variant_id") else None,
                "title": line_item.get("title") or line_item.get("name"),
                "variant_title": line_item.get("variant_title"),
                "sku": line_item.get("sku"),
                "vendor": line_item.get("vendor"),
                "image_url": line_item.get("image_url"),
                # REST webhooks expose current_quantity after order edits/refunds.
                # quantity includes removed units and must not drive procurement.
                "quantity": int(line_item.get("current_quantity", line_item.get("quantity")) or 0),
                "price": _decimal(line_item.get("price")) or Decimal("0"),
                "total_discount": _decimal(discount) or Decimal("0"),
            }
        )
    return rows


def extract_fulfillments(order):
    """提取订单履约及物流跟踪信息，兼容 REST Resource 和原始 dict。"""
    plain = to_plain(order)
    if not isinstance(plain, dict):
        return []

    rows = []
    for fulfillment in plain.get("fulfillments") or []:
        if not isinstance(fulfillment, dict):
            continue
        numbers = fulfillment.get("tracking_numbers") or []
        urls = fulfillment.get("tracking_urls") or []
        if fulfillment.get("tracking_number") and not numbers:
            numbers = [fulfillment["tracking_number"]]
        if fulfillment.get("tracking_url") and not urls:
            urls = [fulfillment["tracking_url"]]
        rows.append(
            {
                "id": str(fulfillment.get("id")) if fulfillment.get("id") is not None else None,
                "status": fulfillment.get("status"),
                "shipment_status": fulfillment.get("shipment_status"),
                "tracking_company": fulfillment.get("tracking_company"),
                "tracking_numbers": [str(value) for value in numbers if value],
                "tracking_urls": [str(value) for value in urls if value],
                "created_at": fulfillment.get("created_at"),
                "updated_at": fulfillment.get("updated_at"),
            }
        )
    return rows
