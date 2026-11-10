"""订单级发货状态校准。

背景：订单增量同步按 **创建时间** 抓最近几天的订单，订单一发货就可能滚出窗口，
本地 ``fulfillment_status`` 会永远停在 ``unfulfilled``，「已发货」快捷筛选因此漏单。
校准按 ``updated_at`` 反向捞状态变化的订单，这里用桩数据覆盖它的行为（不碰网络）。
"""

from app import db
from app.models import Order, Shop
from app.services import shopify_graphql, sync_service


def _patch_statuses(monkeypatch, mapping):
    """替换「批量拉状态」这一步，返回一个记录调用参数的容器。"""
    captured = {}

    def fake(shop, since_iso=None, max_pages=None, timeout=None):
        captured['shop'] = shop.name
        captured['since'] = since_iso
        return dict(mapping)

    monkeypatch.setattr(shopify_graphql, 'fetch_fulfillment_statuses', fake)
    return captured


def _set_local_status(app, order_numbers, status):
    with app.app_context():
        Order.query.filter(Order.shopify_order_id.in_(order_numbers)).update(
            {'fulfillment_status': status}, synchronize_session=False
        )
        db.session.commit()


def test_reconcile_fixes_stale_unfulfilled(app, monkeypatch):
    """本地误存为未发货、Shopify 实为已发货 -> 应该被修正。"""
    _set_local_status(app, ['A-0', 'A-1'], 'unfulfilled')
    _patch_statuses(
        monkeypatch,
        {'A-0': 'fulfilled', 'A-1': 'fulfilled', 'A-2': 'fulfilled'},
    )

    with app.app_context():
        shop = Shop.query.order_by(Shop.id).first()
        result = sync_service.reconcile_fulfillment_status({'shop_id': shop.id})

        assert result['totals']['checked'] == 3
        assert result['totals']['updated'] == 2

        statuses = {
            row.shopify_order_id: row.fulfillment_status
            for row in Order.query.filter(Order.shop_id == shop.id).all()
        }
        assert statuses == {'A-0': 'fulfilled', 'A-1': 'fulfilled', 'A-2': 'fulfilled'}


def test_reconcile_leaves_matching_rows_alone(app, monkeypatch):
    """状态本来就一致时不应该产生「修正」计数。"""
    _patch_statuses(
        monkeypatch,
        {'A-0': 'fulfilled', 'A-1': 'fulfilled', 'A-2': 'fulfilled'},
    )

    with app.app_context():
        shop = Shop.query.order_by(Shop.id).first()
        result = sync_service.reconcile_fulfillment_status({'shop_id': shop.id})

        assert result['totals']['checked'] == 3
        assert result['totals']['updated'] == 0


def test_reconcile_does_not_touch_other_shops(app, monkeypatch):
    """只校准指定店铺，别的店铺不能被顺带改掉。"""
    _set_local_status(app, ['B-0'], 'unfulfilled')
    _patch_statuses(
        monkeypatch,
        {'A-0': 'fulfilled', 'A-1': 'fulfilled', 'A-2': 'fulfilled'},
    )

    with app.app_context():
        shop_a = Shop.query.order_by(Shop.id).first()
        sync_service.reconcile_fulfillment_status({'shop_id': shop_a.id})

        other = Order.query.filter_by(shopify_order_id='B-0').one()
        assert other.fulfillment_status == 'unfulfilled'


def test_reconcile_runs_without_request_context(app, monkeypatch):
    """后台任务/脚本没有请求上下文，也必须覆盖全部启用店铺（不能抛权限错误）。"""
    captured = _patch_statuses(monkeypatch, {'A-0': 'fulfilled'})

    with app.app_context():
        result = sync_service.reconcile_fulfillment_status({'days_back': 7})

    assert len(result['shops']) == 2
    assert captured['since'] is not None
