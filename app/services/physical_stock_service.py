"""Live balances for scanning, without rebuilding the shop's presentation rows.

No balance cache: the caller holds the shop lock and reads committed stock in the
same transaction as its append-only deduction. Historical ambiguous partial
orders keep the reference accounting path until they are reconciled.
"""
from sqlalchemy import text

from app import db
from app.services.inventory_service import _stock_rows


def physical_stock_rows(shop_id, keys, seed_items=()):
    keys = set(keys)
    if not keys:
        return {}
    if db.session.get_bind().dialect.name != 'postgresql':
        return _stock_rows(shop_id, group_keys=keys, seed_items=seed_items)
    ambiguous = db.session.execute(text("""
        SELECT 1 FROM orders o WHERE o.shop_id = :shop_id
        AND (o.warehouse_status = 'legacy_partial_unverified' OR
             (o.warehouse_status = 'pending' AND EXISTS (
                SELECT 1 FROM shipping_parcels p WHERE p.order_id = o.id
                AND p.shop_id = :shop_id AND p.scanned_at IS NOT NULL)))
        AND NOT EXISTS (SELECT 1 FROM inventory_outbound_lines l
                        WHERE l.order_id = o.id AND l.shop_id = :shop_id)
        LIMIT 1
    """), {'shop_id': shop_id}).first()
    if ambiguous:
        return _stock_rows(shop_id, group_keys=keys, seed_items=seed_items)
    result = db.session.execute(text("""
        WITH amounts AS (
            SELECT r->>'group_key' AS group_key,
                   COALESCE(NULLIF(r->>'received_quantity', ''), '0')::bigint AS quantity
            FROM purchase_lists p
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(p.receipt::jsonb) = 'array'
                     THEN p.receipt::jsonb ELSE '[]'::jsonb END) r
            WHERE p.shop_id = :shop_id AND p.status IN ('partially_received', 'received')
              AND r->>'group_key' = ANY(:keys)
            UNION ALL
            SELECT a.group_key, a.quantity_delta::bigint FROM inventory_adjustments a
            WHERE a.shop_id = :shop_id AND a.group_key = ANY(:keys)
            UNION ALL
            SELECT l.group_key, l.quantity_delta::bigint FROM inventory_outbound_lines l
            WHERE l.shop_id = :shop_id AND l.group_key = ANY(:keys)
            UNION ALL
            SELECT a->>'group_key',
                   -COALESCE(NULLIF(a->>'received_quantity', ''), '0')::bigint
            FROM purchase_lists p
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(p.allocations::jsonb) = 'array'
                     THEN p.allocations::jsonb ELSE '[]'::jsonb END) a
            JOIN orders o ON o.id = (a->>'order_id')::integer AND o.shop_id = :shop_id
            WHERE p.shop_id = :shop_id AND p.status IN ('partially_received', 'received')
              AND o.warehouse_status IN ('shipped', 'legacy_unverified')
              AND a->>'group_key' = ANY(:keys)
              AND NOT EXISTS (SELECT 1 FROM inventory_outbound_lines l
                              WHERE l.shop_id = :shop_id AND l.order_id = o.id)
            UNION ALL
            SELECT r.group_key, -r.quantity::bigint FROM inventory_reservations r
            JOIN orders o ON o.id = r.order_id AND o.shop_id = :shop_id
            JOIN order_items i ON i.id = r.order_item_id
            WHERE r.shop_id = :shop_id AND r.group_key = ANY(:keys)
              AND o.warehouse_status IN ('shipped', 'legacy_unverified')
              AND NOT EXISTS (SELECT 1 FROM inventory_outbound_lines l
                              WHERE l.shop_id = :shop_id AND l.order_id = o.id)
        ) SELECT group_key, SUM(quantity) AS quantity FROM amounts GROUP BY group_key
    """), {'shop_id': shop_id, 'keys': sorted(keys)}).all()
    balances = {row.group_key: int(row.quantity) for row in result}
    return {key: {'on_hand_quantity': balances.get(key, 0)} for key in keys}
