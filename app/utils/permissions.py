"""功能权限注册表 —— 菜单/模块级权限的唯一来源。

后端用它做接口鉴权，前端通过 /api/currentUser 拿到同一份定义来显示菜单：
两边共用一套 key，避免「菜单藏了但接口还能调」这种假权限。

规则：
    - 管理员（role=admin）默认拥有全部权限；
    - 子账户只拥有被显式授予的权限；
    - admin_only 的权限（凭据、店铺、账号、系统设置）不可授予子账户。
"""

import re

# 权限清单（顺序即前端展示顺序）
PERMISSIONS = (
    {"key": "dashboard", "label": "仪表板", "group": "经营概览", "admin_only": False,
     "description": "经营数据总览", "requires": ["orders", "reports"],
     "note": "仪表板同时依赖订单与财务报表权限，只勾仪表板会没有数据"},
    {"key": "orders", "label": "订单管理", "group": "经营概览", "admin_only": False,
     "description": "订单列表/详情、成本同步、订单备注", "requires": []},
    {"key": "fulfillment", "label": "仓储履约", "group": "仓储作业", "admin_only": False,
     "description": "采购管理、发货中心、4PX 预申报、物流轨迹、装箱核对、扫码出库", "requires": []},
    {"key": "tracking_force_outbound", "label": "物流轨迹强制补记出库", "group": "仓储作业",
     "admin_only": False,
     "description": "物流轨迹页勾选已有运输轨迹的订单批量补记出库（高危，影响库存）", "requires": ["fulfillment"]},
    {"key": "inventory", "label": "库存管理", "group": "商品与库存", "admin_only": False,
     "description": "公共库存、入库与调整、库存流水", "requires": []},
    {"key": "products", "label": "商品管理", "group": "商品与库存", "admin_only": False,
     "description": "商品/颜色的中文名映射", "requires": []},
    {"key": "after_sales", "label": "售后管理", "group": "售后与客服", "admin_only": False,
     "description": "售后补发、售后采购、售后成本", "requires": []},
    {"key": "mail", "label": "客服邮件", "group": "售后与客服", "admin_only": False,
     "description": "邮件同步、AI 回复草稿、售后审阅", "requires": []},
    {"key": "accounts", "label": "资金账户", "group": "财务", "admin_only": False,
     "description": "账户余额、充值、消费记录", "requires": []},
    {"key": "expenses", "label": "费用管理", "group": "财务", "admin_only": False,
     "description": "费用录入、分摊、审批", "requires": []},
    {"key": "expenses_delete", "label": "删除费用", "group": "财务", "admin_only": False,
     "description": "单条与批量删除费用（高危）", "requires": ["expenses"]},
    {"key": "reports", "label": "财务报表", "group": "财务", "admin_only": False,
     "description": "利润、订单成本与报表导出", "requires": []},
    {"key": "platform_accounts", "label": "平台账号（凭据）", "group": "系统管理", "admin_only": True,
     "description": "平台账号与密钥维护", "requires": []},
    {"key": "shops", "label": "店铺管理", "group": "系统管理", "admin_only": True,
     "description": "店铺与 Shopify/4PX 凭据", "requires": []},
    {"key": "sub_accounts", "label": "子账户管理", "group": "系统管理", "admin_only": True,
     "description": "子账户、店铺与权限分配", "requires": []},
    {"key": "settings", "label": "系统设置", "group": "系统管理", "admin_only": True,
     "description": "全局费用类型与费率", "requires": []},
)

ALL_KEYS = tuple(item["key"] for item in PERMISSIONS)
GRANTABLE_KEYS = tuple(item["key"] for item in PERMISSIONS if not item["admin_only"])
ADMIN_ONLY_KEYS = tuple(item["key"] for item in PERMISSIONS if item["admin_only"])

# 路径 → 需要的权限。
#
# 注意：这里只登记「功能模块级」的权限。未列出的 /api 路径只要求登录，
# 由业务层自己再收一道口子，例如：
#   - /api/shops 的读接口有意放行给全体登录用户：子账户要能读到
#     自己被分配的店铺列表来渲染店铺选择器，真正的数据裁剪靠
#     shop_scope（allowed_shop_ids）在每个查询里做；
#   - /api/currentUser、/api/users/me/password、/api/exchange/* 在
#     ALWAYS_ALLOWED 里显式放行。
#
# 关于 dashboard：仪表板没有独立接口，它是前端把 /api/orders/* 与
# /api/reports/* 拼起来的。所以「仪表板」这个 key 只用于前端菜单可见性，
# 接口鉴权由下面的 orders / reports 两条规则兜住；只给子账户 dashboard
# 而不给 orders，界面会因为没有数据而报错，分配时要注意。
ROUTE_PERMISSIONS = (
    (re.compile(r"^/api/orders"), "orders"),
    # 履约作业单独一条权限：仓库/打包人员只需要「采购 + 发货 + 装箱」，
    # 不应该连带拿到订单查询和售后；客服则相反。
    # Includes packing/start, packing/cancel and packing/priority mutations.
    (re.compile(r"^/api/shipping/outbound/failures$"), "fulfillment"),
    (re.compile(r"^/api/shipping"), "fulfillment"),
    (re.compile(r"^/api/logistics"), "fulfillment"),
    (re.compile(r"^/api/purchase-lists"), "fulfillment"),
    (re.compile(r"^/api/purchase-candidates"), "fulfillment"),
    (re.compile(r"^/api/after-sales/\d+/tracking$"), "after_sales"),  # Manual reshipment labels, never original fulfillment.
    (re.compile(r"^/api/after-sales"), "after_sales"),
    (re.compile(r"^/api/mail"), "mail"),  # Scoped attachments, AI drafts and human-confirmed exchange discounts.
    (re.compile(r"^/api/inventory"), "inventory"),
    (re.compile(r"^/api/products"), "products"),
    (re.compile(r"^/api/order-costs/fourpx"), "expenses"),  # 物流费对账/回填归费用管理
    (re.compile(r"^/api/order-costs"), "orders"),
    (re.compile(r"^/api/order-cost-batches"), "orders"),
    (re.compile(r"^/api/expenses"), "expenses"),
    (re.compile(r"^/api/accounts"), "accounts"),
    (re.compile(r"^/api/recharges"), "accounts"),
    (re.compile(r"^/api/consumptions"), "accounts"),
    (re.compile(r"^/api/reports"), "reports"),
    (re.compile(r"^/api/platform-accounts"), "platform_accounts"),
    (re.compile(r"^/api/settings"), "settings"),
    (re.compile(r"^/api/users"), "sub_accounts"),
    # 同步相关：除 /api/sync/status（在 ALWAYS_ALLOWED）外都归订单模块，
    # 只有能看到订单的人才有必要触发同步。
    (re.compile(r"^/api/sync/"), "orders"),
)

# 始终放行（登录即可）：当前用户信息、本人改密、汇率换算、同步状态
ALWAYS_ALLOWED = (
    re.compile(r"^/api/currentUser$"),
    re.compile(r"^/api/users/me/password$"),
    re.compile(r"^/api/exchange/"),
    re.compile(r"^/api/sync/status$"),
)


ACTION_PERMISSIONS = (
    ("PATCH", re.compile(r"^/api/purchase-lists/\d+/items$"), "fulfillment"),
    ("PATCH", re.compile(r"^/api/logistics/\d+/handling$"), "fulfillment"),
    ("POST", re.compile(r"^/api/logistics/batch-outbound$"), "tracking_force_outbound"),
    ("DELETE", re.compile(r"^/api/expenses/\d+$"), "expenses_delete"),
    ("POST", re.compile(r"^/api/expenses/batch-delete$"), "expenses_delete"),
    ("DELETE", re.compile(r"^/api/orders/\d+/parcels/\d+$"), "fulfillment"),
)


def required_action_permission(method, path):
    """Action permission supplements module permission; never replaces it."""
    for action_method, pattern, permission in ACTION_PERMISSIONS:
        if method == action_method and pattern.match(path):
            return permission
    return None


def required_permission(path):
    """该路径要求的权限 key；无需额外权限时返回 None。"""
    for pattern in ALWAYS_ALLOWED:
        if pattern.match(path):
            return None
    for pattern, permission in ROUTE_PERMISSIONS:
        if pattern.match(path):
            return permission
    return None


def is_grantable(key):
    return key in GRANTABLE_KEYS


def catalog(include_admin_only=True):
    """供前端渲染权限勾选列表。"""
    return [
        {
            "key": item["key"],
            "label": item["label"],
            "group": item["group"],
            "description": item["description"],
            "requires": list(item["requires"]),
            "note": item.get("note", ""),
            "admin_only": item["admin_only"],
        }
        for item in PERMISSIONS
        if include_admin_only or not item["admin_only"]
    ]


def label_of(key):
    for item in PERMISSIONS:
        if item["key"] == key:
            return item["label"]
    return key
