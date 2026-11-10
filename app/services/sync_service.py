"""Shopify synchronisation service.

Resolves which shops the caller may sync (admins: any; sub-accounts: assigned
shops only), then runs the per-shop sync through ShopifyService and aggregates
the statistics.
"""

from datetime import datetime, timedelta

from flask import current_app, has_request_context

from app import db
from app.models import Order, Product, Shop
from app.services.shopify_service import ShopifyService
from app.services import gateway_fee_service
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import allowed_shop_ids, apply_shop_scope, can_access_shop


def _parse_shop_id(data, default=None):
    raw = (data or {}).get("shop_id") if isinstance(data, dict) else None
    if raw in (None, "", "all"):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def resolve_target_shops(explicit_shop_id=None):
    """Shops this request is allowed to sync."""
    if explicit_shop_id is not None:
        shop = db.session.get(Shop, explicit_shop_id)
        if shop is None:
            raise NotFoundError("店铺不存在")
        if not can_access_shop(shop.id):
            raise ForbiddenError("无权同步该店铺")
        if not shop.is_active:
            raise ValidationError("店铺已停用")
        return [shop]

    allowed = allowed_shop_ids()
    query = Shop.query.filter(Shop.is_active.is_(True))
    if allowed is not None:
        if not allowed:
            raise ForbiddenError("当前账号未分配任何店铺")
        query = query.filter(Shop.id.in_(allowed))

    shops = query.order_by(Shop.name).all()
    if not shops:
        raise ValidationError("没有可同步的店铺，请先配置店铺")
    return shops


def _service_for(shop):
    service = ShopifyService()
    service.init_shop(shop, current_app)
    return service


def _run_for_shops(shops, runner, touch_last_sync=False):
    """Run ``runner(service)`` for each shop, collecting stats and errors."""
    results = []
    for shop in shops:
        try:
            stats = runner(_service_for(shop))
            if touch_last_sync:
                error_count = int(stats.get("errors", 0) or 0) if isinstance(stats, dict) else 0
                record_order_sync_result(
                    shop, f"{error_count} 笔订单处理失败" if error_count else None
                )
            results.append({"shop_id": shop.id, "shop_name": shop.name, "stats": stats})
        except Exception as exc:  # noqa: BLE001 - per-shop isolation is intentional
            db.session.rollback()
            current_app.logger.error("同步店铺 %s 失败: %s", shop.name, exc)
            if touch_last_sync:
                record_order_sync_result(shop, exc)
            results.append({"shop_id": shop.id, "shop_name": shop.name, "error": str(exc)})
    return results


def record_order_sync_result(shop, error):
    """Persist the latest order-sync outcome for manual and scheduled runs."""
    shop.last_sync_attempt_at = datetime.utcnow()
    if error is None:
        shop.last_sync = shop.last_sync_attempt_at
        shop.last_sync_error = None
    else:
        shop.last_sync_error = str(error)[:2000]
    db.session.commit()


def test_connections(payload=None):
    explicit = _parse_shop_id(payload or {})
    shops = resolve_target_shops(explicit)

    results = []
    for shop in shops:
        try:
            service = _service_for(shop)
            connected = bool(service.test_connection())
            results.append(
                {
                    "shop_id": shop.id,
                    "shop_name": shop.name,
                    "connected": connected,
                    "shop_info": service.get_shop_info() if connected else None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "shop_id": shop.id,
                    "shop_name": shop.name,
                    "connected": False,
                    "error": str(exc),
                }
            )

    return {
        "all_connected": all(item["connected"] for item in results),
        "shops": results,
    }


def sync_orders(payload=None):
    payload = payload or {}
    days_back = payload.get("days_back", 30)
    limit = payload.get("limit", 250)
    # 订单同步完是否顺手把真实手续费补上（默认开；拉取订单时就该拿到真实数据）
    with_fees = payload.get("with_fees", True)

    if not isinstance(days_back, int) or not 1 <= days_back <= 365:
        raise ValidationError("同步天数必须在1-365之间")
    if not isinstance(limit, int) or not 1 <= limit <= 250:
        raise ValidationError("每次同步数量必须在1-250之间")

    shops = resolve_target_shops(_parse_shop_id(payload))
    results = _run_for_shops(
        shops,
        lambda service: service.sync_orders(days_back=days_back, limit=limit),
        touch_last_sync=True,
    )

    fees = refresh_fees_for_shops(shops) if with_fees else []

    totals = {"total_fetched": 0, "new_orders": 0, "updated_orders": 0, "errors": 0}
    for item in results:
        if "stats" in item:
            for key in totals:
                totals[key] += int(item["stats"].get(key, 0) or 0)
        else:
            totals["errors"] += 1

    return {"shop_count": len(shops), "stats": totals, "shops": results, "fees": fees}


def refresh_fees_for_shops(shops, paypal_limit=10, days_back=7):
    """订单一同步完就把真实手续费补上。

    - Shopify Payments：读结算流水，按 processed_at 倒序提前停止，通常 1~2 页就够
    - PayPal：回执只能逐单取，只补最近还没拿到真实费用的少量订单

    单店失败只记录不抛出，不能因为手续费把订单同步整体判为失败。
    """
    since = (datetime.utcnow() - timedelta(days=days_back)).date() if days_back is not None else None
    summary = []

    for shop in shops:
        entry = {"shop_id": shop.id, "shop_name": shop.name}

        try:
            settlement = gateway_fee_service.sync_shopify_payments_charges(
                shop, since_date=since
            )
            entry["settlement_updated"] = settlement.get("updated", 0)
        except Exception as exc:  # noqa: BLE001 - 手续费失败不影响订单同步结果
            db.session.rollback()
            current_app.logger.warning("店铺 %s 结算流水补取失败: %s", shop.name, exc)
            entry["settlement_error"] = str(exc)
        finally:
            db.session.commit()

        if paypal_limit:
            try:
                paypal = gateway_fee_service.refresh_paypal_payments(
                    shop, limit=paypal_limit, only_missing=True
                )
                entry["paypal_updated"] = paypal.get("updated", 0)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                current_app.logger.warning("店铺 %s PayPal 回执补取失败: %s", shop.name, exc)
                entry["paypal_error"] = str(exc)
            finally:
                db.session.commit()

        summary.append(entry)

    return summary


def sync_products(payload=None):
    payload = payload or {}
    limit = payload.get("limit", 250)
    if not isinstance(limit, int) or not 1 <= limit <= 250:
        raise ValidationError("每次同步数量必须在1-250之间")

    shops = resolve_target_shops(_parse_shop_id(payload))
    results = _run_for_shops(shops, lambda service: service.sync_products(limit=limit))

    totals = {"total_fetched": 0, "new_products": 0, "updated_products": 0, "errors": 0}
    for item in results:
        if "stats" in item:
            for key in totals:
                totals[key] += int(item["stats"].get(key, 0) or 0)
        else:
            totals["errors"] += 1

    return {"shop_count": len(shops), "stats": totals, "shops": results}


def sync_recent_orders(payload=None):
    payload = payload or {}
    hours = payload.get("hours", 24)
    if not isinstance(hours, int) or not 1 <= hours <= 168:
        raise ValidationError("同步小时数必须在1-168之间")

    shops = resolve_target_shops(_parse_shop_id(payload))
    results = _run_for_shops(
        shops,
        lambda service: service.sync_recent_orders(hours=hours),
        touch_last_sync=True,
    )
    return {"shop_count": len(shops), "shops": results}


def sync_gateway_fees(payload=None):
    """把网关真实手续费/净额写回支付记录。

    - Shopify Payments：读结算流水，一次翻页即可覆盖全量
    - PayPal：回执要逐单取，慢，需显式传 ``paypal=True``
    """
    payload = payload or {}
    shops = resolve_target_shops(_parse_shop_id(payload))
    since = payload.get("since")
    include_paypal = bool(payload.get("paypal"))
    paypal_limit = payload.get("paypal_limit")

    totals = {
        "settlement_fetched": 0,
        "settlement_updated": 0,
        "paypal_orders": 0,
        "paypal_updated": 0,
        "errors": 0,
    }
    results = []

    for shop in shops:
        entry = {"shop_id": shop.id, "shop_name": shop.name}

        try:
            settlement = gateway_fee_service.sync_shopify_payments_charges(shop, since_date=since)
            entry["settlement"] = settlement
            totals["settlement_fetched"] += settlement["fetched"]
            totals["settlement_updated"] += settlement["updated"]
        except Exception as exc:  # noqa: BLE001 - per-shop isolation is intentional
            db.session.rollback()
            current_app.logger.error("店铺 %s 结算流水同步失败: %s", shop.name, exc)
            entry["error"] = str(exc)
            totals["errors"] += 1

        if include_paypal:
            try:
                paypal = gateway_fee_service.refresh_paypal_payments(shop, limit=paypal_limit)
                entry["paypal"] = paypal
                totals["paypal_orders"] += paypal["orders"]
                totals["paypal_updated"] += paypal["updated"]
            except Exception as exc:  # noqa: BLE001 - per-shop isolation is intentional
                db.session.rollback()
                current_app.logger.error("店铺 %s PayPal 回执同步失败: %s", shop.name, exc)
                entry["paypal_error"] = str(exc)
                totals["errors"] += 1

        results.append(entry)

    return {"shop_count": len(shops), "stats": totals, "shops": results}


def sync_status():
    """Order/product counters within the caller's shop scope."""
    since = datetime.now() - timedelta(days=7)

    total_orders = apply_shop_scope(Order.query, Order).count()
    recent_orders = apply_shop_scope(Order.query.filter(Order.created_at >= since), Order).count()
    total_products = apply_shop_scope(Product.query, Product).count()
    active_products = apply_shop_scope(Product.query.filter_by(status="active"), Product).count()

    last_order = apply_shop_scope(Order.query, Order).order_by(Order.updated_at.desc()).first()

    return {
        "orders": {"total": total_orders, "recent_7_days": recent_orders},
        "products": {"total": total_products, "active": active_products},
        "last_sync_time": last_order.updated_at.isoformat() if last_order and last_order.updated_at else None,
    }


def shop_info(payload=None):
    shops = resolve_target_shops(_parse_shop_id(payload or {}))
    results = []
    for shop in shops:
        try:
            results.append(
                {
                    "shop_id": shop.id,
                    "shop_name": shop.name,
                    "shop_info": _service_for(shop).get_shop_info(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"shop_id": shop.id, "shop_name": shop.name, "error": str(exc)})
    return results


def _chunks(items, size=500):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _shops_for_sync(payload=None):
    """同步范围：请求内按权限过滤，后台任务 / 脚本则覆盖全部启用店铺。

    后台任务（Celery）和命令行脚本没有请求上下文，``allowed_shop_ids()``
    会返回空列表，必须走这条分支。
    """
    explicit_shop_id = _parse_shop_id(payload or {})
    if has_request_context():
        return resolve_target_shops(explicit_shop_id)

    if explicit_shop_id is not None:
        shop = db.session.get(Shop, explicit_shop_id)
        if shop is None:
            raise NotFoundError("店铺不存在")
        return [shop]

    return Shop.query.filter(Shop.is_active.is_(True)).order_by(Shop.id).all()


def reconcile_fulfillment_status(payload=None):
    """校准「订单级发货状态」，修掉已发货却显示未发货的订单。

    订单增量同步按 **创建时间** 抓最近几天，订单一发货就可能滚出窗口，
    本地 fulfillment_status 会永远停在 unfulfilled，「已发货」筛选因此漏单。
    这里按 **updated_at** 反向捞取状态变化的订单，一次 250 条，成本很低。
    """
    from app.services.shopify_graphql import fetch_fulfillment_statuses

    payload = payload or {}
    shops = _shops_for_sync(payload)
    days_back = max(1, min(int(payload.get("days_back") or 30), 3650))
    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")

    totals = {"fetched": 0, "checked": 0, "updated": 0}
    results = []

    for shop in shops:
        entry = {"shop_id": shop.id, "shop_name": shop.name}
        try:
            statuses = fetch_fulfillment_statuses(shop, since_iso=since)
            entry["fetched"] = len(statuses)
            totals["fetched"] += len(statuses)

            checked = 0
            updated = 0
            for chunk in _chunks(list(statuses.keys())):
                orders = Order.query.filter(
                    Order.shop_id == shop.id, Order.shopify_order_id.in_(chunk)
                ).all()
                for order in orders:
                    checked += 1
                    remote = statuses.get(order.shopify_order_id)
                    if remote and order.fulfillment_status != remote:
                        order.fulfillment_status = remote
                        updated += 1

            db.session.commit()
            entry.update({"checked": checked, "updated": updated})
            totals["checked"] += checked
            totals["updated"] += updated
        except Exception as exc:  # noqa: BLE001 - 逐店铺隔离，单店失败不影响其它店
            db.session.rollback()
            current_app.logger.error("店铺 %s 发货状态校准失败: %s", shop.name, exc)
            entry["error"] = str(exc)

        results.append(entry)

    return {"since": since, "totals": totals, "shops": results}
