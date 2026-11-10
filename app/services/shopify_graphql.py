"""Shopify Admin GraphQL 客户端（订单详情、商品图片和物流信息）。

为什么单独走 GraphQL：
    REST 的 ``orders.json`` 在受保护客户数据（Protected Customer Data）上
    经常只回「国家 + 省份」，门牌地址、姓名、电话全是空的；而
    ``order.shippingAddress`` 才是权威来源。订单详情页按需回源拉取，
    拉到就写回 orders 表，下次直接读库。

令牌统一由 :mod:`app.services.shopify_auth` 用
「域名 + Client ID + Client Secret」换取。

只负责取数与归一化，不碰数据库；落库放在 order_service 里。
"""

import logging
import time

import requests

from app.services.shopify_auth import (
    API_VERSION,
    ShopifyAuthError,
    api_root,
    get_access_token,
    invalidate_token,
)
from app.utils.shopify_resources import ADDRESS_FIELDS

logger = logging.getLogger(__name__)

TIMEOUT = 30
TRANSIENT_RETRIES = 3

# Validated against Admin GraphQL 2026-07. Keep pagination small because each
# order includes line items, transactions and fulfillments.
ORDER_SYNC_FIELDS = """
      id name createdAt updatedAt cancelledAt currencyCode presentmentCurrencyCode
      displayFinancialStatus displayFulfillmentStatus note email phone
      shippingAddress {
        name phone address1 address2 city province provinceCode country countryCode zip
      }
      customer { firstName lastName email phone }
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      currentSubtotalPriceSet { shopMoney { amount currencyCode } }
      currentTotalTaxSet { shopMoney { amount currencyCode } }
      totalShippingPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id title variantTitle sku vendor quantity currentQuantity
          originalUnitPriceSet { shopMoney { amount currencyCode } }
          discountedTotalSet { shopMoney { amount currencyCode } }
          variant {
            id
            media(first: 1) { nodes { preview { image { url altText } } } }
            product { id featuredMedia { preview { image { url altText } } } }
          }
        }
      }
      transactions {
        id kind status gateway processedAt
        amountSet {
          shopMoney { amount currencyCode }
          presentmentMoney { amount currencyCode }
        }
      }
      fulfillments(first: 50) {
        id status createdAt updatedAt trackingInfo { company number url }
      }
"""
ORDERS_SYNC_QUERY = """
query OrdersSync($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
""" + ORDER_SYNC_FIELDS + """
    }
  }
}
"""
ORDERS_UPDATED_QUERY = ORDERS_SYNC_QUERY.replace("sortKey: CREATED_AT", "sortKey: UPDATED_AT")
ORDERS_BATCH_SYNC_QUERY = """
query PurchaseOrdersBatch($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Order {
""" + ORDER_SYNC_FIELDS.replace("lineItems(first: 100)", "lineItems(first: 10)") + """
    }
  }
}
"""


def fetch_orders_sync_by_ids(shop, shopify_order_ids):
    """Read a small batch by immutable IDs, with complete sync fields.

    First read ten lines per order to reduce requested query cost. The caller
    follows every remaining line-item page before marking an order refreshed.
    """
    result = _execute(shop, ORDERS_BATCH_SYNC_QUERY, {
        "ids": [gid_for_order(order_id) for order_id in shopify_order_ids],
    })
    nodes = result.get("nodes")
    if not isinstance(nodes, list):
        raise ShopifyGraphQLError("Shopify 未返回批量订单详情")
    return nodes


def fetch_orders_page(shop, since_utc, first=25, after=None):
    """Return one newest-first Shopify orders page; no database writes."""
    search = f"created_at:>={since_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}" if since_utc else None
    result = _execute(shop, ORDERS_SYNC_QUERY, {"first": first, "after": after, "query": search})
    connection = result.get("orders") or {}
    return connection.get("nodes") or [], connection.get("pageInfo") or {}


def fetch_updated_orders_page(shop, since_utc, first=25, after=None):
    """Return orders changed since the timestamp, including older edited orders."""
    search = f"updated_at:>={since_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    result = _execute(
        shop, ORDERS_UPDATED_QUERY, {"first": first, "after": after, "query": search}
    )
    connection = result.get("orders") or {}
    return connection.get("nodes") or [], connection.get("pageInfo") or {}


def fetch_order_sync_by_id(shop, shopify_order_id, order_number):
    """Read the complete sync fields for one order, then verify its immutable ID."""
    number = str(order_number or "").strip().lstrip("#")
    result = _execute(
        shop,
        ORDERS_SYNC_QUERY,
        {"first": 25, "after": None, "query": f"name:#{number}"},
    )
    for node in (result.get("orders") or {}).get("nodes") or []:
        if node.get("id") == gid_for_order(shopify_order_id):
            return node
    raise ShopifyGraphQLError("Shopify 未返回这笔订单，无法更新详情")


ORDER_LINE_ITEMS_QUERY = """
query OrderLineItems($id: ID!, $after: String) {
  order(id: $id) {
    lineItems(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id title variantTitle sku vendor quantity currentQuantity
        originalUnitPriceSet { shopMoney { amount currencyCode } }
        discountedTotalSet { shopMoney { amount currencyCode } }
        variant {
          id
          media(first: 1) { nodes { preview { image { url altText } } } }
          product { id featuredMedia { preview { image { url altText } } } }
        }
      }
    }
  }
}
"""


def fetch_remaining_order_line_items(shop, order_id, after):
    rows = []
    cursor = after
    visited = set()
    while cursor:
        if cursor in visited:
            raise ShopifyGraphQLError("Shopify 商品分页游标重复，无法读取完整订单")
        visited.add(cursor)
        result = _execute(shop, ORDER_LINE_ITEMS_QUERY, {"id": order_id, "after": cursor})
        line_items = (result.get("order") or {}).get("lineItems") or {}
        page_info = line_items.get("pageInfo") or {}
        if (not isinstance(line_items.get("nodes"), list)
                or not isinstance(page_info.get("hasNextPage"), bool)
                or (page_info.get("hasNextPage") and not page_info.get("endCursor"))):
            raise ShopifyGraphQLError("Shopify 商品分页不完整，无法读取完整订单")
        rows.extend(line_items["nodes"])
        cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
    return rows


PRODUCTS_SYNC_QUERY = """
query ProductsSync($first: Int!, $after: String) {
  products(first: $first, after: $after, sortKey: UPDATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title productType vendor status
      featuredMedia { preview { image { url } } }
      variants(first: 100) {
        pageInfo { hasNextPage endCursor }
        nodes { id title sku price inventoryQuantity image { url } media(first: 1) { nodes { preview { image { url } } } } }
      }
    }
  }
}
"""


def fetch_products_page(shop, first=25, after=None):
    result = _execute(shop, PRODUCTS_SYNC_QUERY, {"first": first, "after": after})
    connection = result.get("products") or {}
    return connection.get("nodes") or [], connection.get("pageInfo") or {}


PRODUCT_VARIANTS_QUERY = """
query ProductVariants($id: ID!, $after: String) {
  product(id: $id) {
    variants(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id title sku price inventoryQuantity image { url } media(first: 1) { nodes { preview { image { url } } } } }
    }
  }
}
"""


def fetch_remaining_variants(shop, product_id, after):
    """Complete a product's variant list when the first 100 did not fit."""
    rows = []
    cursor = after
    while cursor:
        result = _execute(shop, PRODUCT_VARIANTS_QUERY, {"id": product_id, "after": cursor})
        variants = (result.get("product") or {}).get("variants") or {}
        rows.extend(variants.get("nodes") or [])
        page_info = variants.get("pageInfo") or {}
        cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
    return rows


PRODUCT_IMAGES_QUERY = """
query ProductImages($id: ID!, $after: String) {
  product(id: $id) {
    id
    featuredMedia { preview { image { url altText } } }
    variants(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        media(first: 1) { nodes { preview { image { url altText } } } }
      }
    }
  }
}
"""


def fetch_product_images(shop, product_id):
    """Read every variant image of one Shopify product for a historical backfill."""
    product_gid = f"gid://shopify/Product/{str(product_id).rsplit('/', 1)[-1]}"
    after = None
    result = {}
    while True:
        data = _execute(shop, PRODUCT_IMAGES_QUERY, {"id": product_gid, "after": after})
        product = data.get("product")
        if not isinstance(product, dict):
            return result
        product_image = _featured_image(product)
        fallback = product_image.get("url") if product_image else None
        connection = product.get("variants") or {}
        for variant in connection.get("nodes") or []:
            variant_id = str(variant.get("id") or "").rsplit("/", 1)[-1]
            image = _media_image(variant.get("media"))
            url = (image or {}).get("url") or fallback
            if variant_id and url:
                result[variant_id] = url
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            return result
        after = page_info["endCursor"]


VARIANT_IMAGES_QUERY = """
query VariantImages($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant {
      id
      media(first: 1) { nodes { preview { image { url altText } } } }
      product { featuredMedia { preview { image { url altText } } } }
    }
  }
}
"""


def fetch_variant_images(shop, variant_ids):
    """Fetch exact variant images in one request, with each variant's product fallback."""
    gids = [f"gid://shopify/ProductVariant/{str(value).rsplit('/', 1)[-1]}" for value in variant_ids]
    data = _execute(shop, VARIANT_IMAGES_QUERY, {"ids": gids})
    return {
        str(node["id"]).rsplit("/", 1)[-1]: url
        for node in data.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
        if (url := line_item_image(node))
    }


SHOP_INFO_QUERY = """
query ShopInfo {
  shop {
    name email primaryDomain { host } currencyCode ianaTimezone
    plan { publicDisplayName }
  }
}
"""


def fetch_shop_info(shop):
    return (_execute(shop, SHOP_INFO_QUERY, {}) or {}).get("shop") or {}


ACCESS_SCOPES_QUERY = """
query AccessScopes {
  currentAppInstallation { accessScopes { handle } }
}
"""


def require_all_orders_access(shop):
    """Do not claim a full-history sync when Shopify only exposes 60 days."""
    data = _execute(shop, ACCESS_SCOPES_QUERY, {})
    installation = data.get("currentAppInstallation") or {}
    scopes = {item.get("handle") for item in installation.get("accessScopes") or []}
    if "read_all_orders" not in scopes:
        raise ShopifyGraphQLError(
            "当前 Shopify 应用未获 read_all_orders 权限，最多只能读取最近 60 天订单；"
            "请先在 Shopify 为应用开通全部历史订单读取权限"
        )

# 一次把订单级、客户、商品图和物流拿全，避免多次往返。
# 该查询已通过 Shopify Admin GraphQL 2026-07 schema 校验。
ORDER_DETAILS_QUERY = """
query OrderDetails($id: ID!) {
  order(id: $id) {
    id
    name
    displayFulfillmentStatus
    note
    email
    phone
    shippingAddress {
      name
      company
      phone
      address1
      address2
      city
      province
      provinceCode
      country
      countryCode
      zip
    }
    customer {
      displayName
      firstName
      lastName
      email
      phone
    }
    lineItems(first: 250) {
      nodes {
        id
        title
        variantTitle
        sku
        quantity
        originalUnitPriceSet {
          shopMoney { amount currencyCode }
        }
        variant {
          id
          media(first: 1) {
            nodes { preview { image { url altText } } }
          }
          product {
            id
            featuredMedia { preview { image { url altText } } }
          }
        }
      }
    }
    fulfillments(first: 50) {
      id
      status
      createdAt
      updatedAt
      trackingInfo { company number url }
    }
  }
}
"""

# GraphQL 的驼峰字段名 → 库里存的蛇形字段名
_ADDRESS_KEY_MAP = {
    "provinceCode": "province_code",
    "countryCode": "country_code",
}


class ShopifyGraphQLError(RuntimeError):
    """GraphQL 返回了 errors，或 HTTP 层面失败。"""


def endpoint_for(shop):
    return f"{api_root(shop, API_VERSION)}/graphql.json"


def gid_for_order(shopify_order_id):
    """把本地存的数字/字符串订单 id 拼成 GraphQL 需要的全局 id。"""
    value = str(shopify_order_id or "").strip()
    if not value:
        raise ShopifyGraphQLError("订单缺少 Shopify 订单号，无法拉取客户信息")
    if value.startswith("gid://"):
        return value
    return f"gid://shopify/Order/{value}"


def _execute(shop, query, variables, timeout=TIMEOUT):
    """发一次 GraphQL 请求，返回 ``data`` 部分。"""
    response = _post_graphql(shop, _token_for(shop, timeout), query, variables, timeout)

    # 换来的临时令牌可能被提前吊销，清掉缓存再试一次
    if response.status_code == 401:
        invalidate_token(shop)
        response = _post_graphql(shop, _token_for(shop, timeout), query, variables, timeout)

    # GraphQL throttling is HTTP 200, not the HTTP 429 handled below. Respect
    # query-cost metadata instead of increasing concurrency and retrying blindly.
    for attempt in range(TRANSIENT_RETRIES):
        try:
            payload = response.json()
        except ValueError:
            break
        errors = payload.get("errors") or []
        throttled = any(isinstance(error, dict)
                        and (error.get("extensions") or {}).get("code") == "THROTTLED" for error in errors)
        if not throttled or attempt == TRANSIENT_RETRIES - 1:
            break
        cost = (payload.get("extensions") or {}).get("cost") or {}
        throttle = cost.get("throttleStatus") or {}
        restore = max(float(throttle.get("restoreRate") or 1), 1)
        deficit = max(float(cost.get("requestedQueryCost") or 0)
                      - float(throttle.get("currentlyAvailable") or 0), 0)
        time.sleep(min(max(deficit / restore, 1), 15))
        response = _post_graphql(shop, _token_for(shop, timeout), query, variables, timeout)
    return _parse_response(shop, response)


def _token_for(shop, timeout=TIMEOUT):
    """取令牌，并把授权错误统一成 GraphQL 侧异常。"""
    try:
        return get_access_token(shop, timeout=timeout)
    except ShopifyAuthError as exc:
        raise ShopifyGraphQLError(str(exc)) from exc


def _post_graphql(shop, token, query, variables, timeout):
    for attempt in range(TRANSIENT_RETRIES):
        try:
            response = requests.post(
                endpoint_for(shop),
                headers={
                    "X-Shopify-Access-Token": token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={"query": query, "variables": variables},
                timeout=timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == TRANSIENT_RETRIES - 1:
                raise ShopifyGraphQLError(f"连接 Shopify 失败：{exc}") from exc
            logger.warning("Shopify 连接暂时中断，重试第 %s 次：%s", attempt + 1, exc)
        else:
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == TRANSIENT_RETRIES - 1:
                return response
            logger.warning("Shopify 暂时返回 HTTP %s，重试第 %s 次", response.status_code, attempt + 1)
        time.sleep(min(2**attempt, 4))


def _parse_response(shop, response):
    if response.status_code == 401:
        raise ShopifyGraphQLError("访问令牌无效或已过期，请在店铺配置里更新凭据")
    if response.status_code == 402:
        raise ShopifyGraphQLError("店铺已冻结或欠费，Shopify 拒绝访问")
    if response.status_code == 403:
        raise ShopifyGraphQLError("应用缺少读取订单的权限（需要 read_orders）")
    if response.status_code == 404:
        raise ShopifyGraphQLError("接口地址不存在，请确认店铺域名与 API 版本")
    if response.status_code == 429:
        raise ShopifyGraphQLError("请求过于频繁，Shopify 已限流，请稍后重试")
    if response.status_code != 200:
        raise ShopifyGraphQLError(f"Shopify 返回 HTTP {response.status_code}：{response.text[:200]}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ShopifyGraphQLError("Shopify 返回了非 JSON 内容") from exc

    if payload.get("errors"):
        raise ShopifyGraphQLError(_format_errors(payload["errors"]))

    return payload.get("data") or {}


def _format_errors(errors):
    """GraphQL 会对每个被拒字段各报一次同样的错，去重后再展示。"""
    if not isinstance(errors, list):
        return f"Shopify 拒绝了这次查询：{errors}"

    seen = []
    for item in errors:
        message = item.get("message") if isinstance(item, dict) else str(item)
        message = str(message).strip()
        message = _humanize(message)
        if message and message not in seen:
            seen.append(message)
    text = "；".join(seen) or "未知错误"
    return f"Shopify 拒绝了这次查询：{text}"


def _humanize(message):
    """把 Shopify 的英文政策报错翻译成能直接执行的说明。"""
    lowered = message.lower()
    if "not approved to access the customer object" in lowered or "personally identifiable" in lowered:
        return (
            "当前应用没有客户隐私数据（PII）权限，拿不到姓名、收货地址、电话。"
            "两种解决方式二选一：①在 Shopify 后台给这个应用开启 "
            "「Protected customer data access」；②改用已获批 PII 权限的 Partner 应用，"
            "填好 SHOPIFY_OAUTH_CLIENT_ID / SHOPIFY_OAUTH_CLIENT_SECRET 后重试。"
        )
    if "access denied" in lowered or "not approved" in lowered:
        return "应用未获得该数据权限，请在 Shopify 应用配置里补充授权"
    return message


def _clean_address(raw):
    """只保留订单里真正有值的地址字段，并统一成蛇形命名。"""
    if not isinstance(raw, dict):
        return None

    filled = {}
    for key, value in raw.items():
        target = _ADDRESS_KEY_MAP.get(key, key)
        if target not in ADDRESS_FIELDS:
            continue
        if value in (None, ""):
            continue
        filled[target] = value
    return filled or None


def _display_name(customer, address):
    if isinstance(customer, dict):
        if customer.get("displayName"):
            return customer["displayName"]
        parts = [customer.get("firstName"), customer.get("lastName")]
        joined = " ".join(str(part).strip() for part in parts if part)
        if joined:
            return joined
    if isinstance(address, dict) and address.get("name"):
        return address["name"]
    return None


def fetch_order_contact(shop, shopify_order_id, timeout=TIMEOUT):
    """拉取订单的客户信息与收货地址。

    返回结构与 ``app.utils.shopify_resources.extract_customer_info`` 一致：
    ``{"email", "name", "phone", "shipping_address"}``，取不到就是 None，
    调用方可以直接写回 Order 字段。
    """
    data = _execute(
        shop,
        ORDER_DETAILS_QUERY,
        {"id": gid_for_order(shopify_order_id)},
        timeout=timeout,
    )
    order = data.get("order")
    if not isinstance(order, dict):
        raise ShopifyGraphQLError("Shopify 查不到这个订单（可能已被删除）")

    shipping = _clean_address(order.get("shippingAddress"))
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}

    email = order.get("email") or customer.get("email")
    phone = order.get("phone") or (shipping or {}).get("phone") or customer.get("phone")
    name = _display_name(customer, shipping)

    return {
        "email": email or None,
        "name": name or None,
        "phone": phone or None,
        "shipping_address": shipping,
    }


def _media_image(media):
    nodes = (media or {}).get("nodes") if isinstance(media, dict) else None
    first = nodes[0] if nodes else None
    preview = first.get("preview") if isinstance(first, dict) else None
    image = preview.get("image") if isinstance(preview, dict) else None
    return image if isinstance(image, dict) else None


def _featured_image(product):
    media = product.get("featuredMedia") if isinstance(product, dict) else None
    preview = media.get("preview") if isinstance(media, dict) else None
    image = preview.get("image") if isinstance(preview, dict) else None
    return image if isinstance(image, dict) else None


def line_item_image(variant):
    """Prefer the exact variant image; use its own product image as fallback."""
    if not isinstance(variant, dict):
        return None
    direct = variant.get("image")
    if isinstance(direct, dict) and direct.get("url"):
        return direct["url"]
    image = _media_image(variant.get("media"))
    if image and image.get("url"):
        return image["url"]
    product_image = _featured_image(variant.get("product"))
    return product_image.get("url") if product_image else None


def normalize_fulfillment_status(value):
    """把 GraphQL 的展示状态收敛为本系统使用的三态值。"""
    status = str(value or "").strip().upper()
    if status == "FULFILLED":
        return "fulfilled"
    if status == "PARTIALLY_FULFILLED":
        return "partial"
    if status:
        return "unfulfilled"
    return None


# 批量核对「订单级发货状态」。
#
# 为什么需要它：订单增量同步是按 **创建时间** 抓最近几天的，订单一发货就滚出窗口，
# 本地 fulfillment_status 会永远停在 unfulfilled，导致「已发货」筛选漏单。
# 按 **updated_at** 过滤才能捞到「状态刚变化」的订单，一次 250 条，成本很低。
ORDERS_FULFILLMENT_QUERY = """
query OrdersFulfillment($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: UPDATED_AT, reverse: true) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      name
      displayFulfillmentStatus
    }
  }
}
"""


def order_id_from_gid(gid):
    """``gid://shopify/Order/123`` -> ``123``。"""
    value = str(gid or "").strip()
    if not value:
        return None
    return value.rsplit("/", 1)[-1] or None


def fetch_fulfillment_statuses(shop, since_iso=None, max_pages=None, timeout=TIMEOUT):
    """按 ``updated_at`` 批量拉取订单级发货状态。

    返回 ``{shopify_order_id: 'fulfilled' | 'partial' | 'unfulfilled'}``；
    只包含取到状态的订单。
    """
    statuses = {}
    after = None
    search = f"updated_at:>'{since_iso}'" if since_iso else None
    pages = 0

    while True:
        data = _execute(
            shop,
            ORDERS_FULFILLMENT_QUERY,
            {"first": 250, "after": after, "query": search},
            timeout=timeout,
        )
        page = data.get("orders") or {}

        for node in page.get("nodes") or []:
            order_id = order_id_from_gid(node.get("id"))
            status = normalize_fulfillment_status(node.get("displayFulfillmentStatus"))
            if order_id and status:
                statuses[order_id] = status

        pages += 1
        page_info = page.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or (max_pages and pages >= max_pages):
            break
        after = page_info.get("endCursor")

    return statuses


def fetch_order_details(shop, shopify_order_id, timeout=TIMEOUT):
    """拉取并归一化订单备注、商品图片、客户地址和物流信息。"""
    data = _execute(
        shop,
        ORDER_DETAILS_QUERY,
        {"id": gid_for_order(shopify_order_id)},
        timeout=timeout,
    )
    order = data.get("order")
    if not isinstance(order, dict):
        raise ShopifyGraphQLError("Shopify 查不到这个订单（可能已被删除）")

    shipping = _clean_address(order.get("shippingAddress"))
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    contact = {
        "email": order.get("email") or customer.get("email") or None,
        "name": _display_name(customer, shipping),
        "phone": order.get("phone") or (shipping or {}).get("phone") or customer.get("phone") or None,
        "shipping_address": shipping,
    }

    items = []
    for node in (order.get("lineItems") or {}).get("nodes") or []:
        if not isinstance(node, dict):
            continue
        variant = node.get("variant") if isinstance(node.get("variant"), dict) else {}
        image = _media_image(variant.get("media")) or _featured_image(variant.get("product"))
        items.append(
            {
                "shopify_line_item_id": str(node.get("id", "")).rsplit("/", 1)[-1],
                "image_url": image.get("url") if image else None,
                "image_alt": image.get("altText") if image else None,
            }
        )

    fulfillments = []
    for row in order.get("fulfillments") or []:
        if not isinstance(row, dict):
            continue
        tracking = row.get("trackingInfo") or []
        fulfillments.append(
            {
                "id": str(row.get("id", "")).rsplit("/", 1)[-1] or None,
                "status": row.get("status"),
                "tracking_company": next(
                    (item.get("company") for item in tracking if item.get("company")), None
                ),
                "tracking_numbers": [item.get("number") for item in tracking if item.get("number")],
                "tracking_urls": [item.get("url") for item in tracking if item.get("url")],
                "created_at": row.get("createdAt"),
                "updated_at": row.get("updatedAt"),
            }
        )

    return {
        **contact,
        "note": order.get("note") or None,
        "fulfillment_status": normalize_fulfillment_status(
            order.get("displayFulfillmentStatus")
        ),
        "line_items": items,
        "fulfillments": fulfillments,
    }
