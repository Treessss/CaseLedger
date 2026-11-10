"""Display procurement provenance without restoring removed demand."""
from sqlalchemy.orm import load_only, noload

from app import db
from app.models import AfterSale, Order, PurchaseListEdit


def after_sale_links(purchase):
    def sale_ids(allocations):
        return {int(row['after_sale_id']) for row in allocations or [] if row.get('after_sale_id')}

    current = sale_ids(purchase.allocations)
    removed = set()
    # Only detail responses read edit history. Lists keep their existing fast path.
    for before, after in db.session.query(
            PurchaseListEdit.before['allocations'], PurchaseListEdit.after['allocations']).filter_by(
            purchase_id=purchase.id).all():
        removed.update(sale_ids(before) - sale_ids(after))
    ids = current | removed
    if not ids:
        return []
    rows = db.session.query(AfterSale, Order.order_number).options(
        noload('*'), load_only(AfterSale.id, AfterSale.customer_reference, AfterSale.order_id, AfterSale.deleted_at),
    ).join(Order, Order.id == AfterSale.order_id).filter(
        AfterSale.id.in_(ids), AfterSale.shop_id == purchase.shop_id, Order.shop_id == purchase.shop_id).all()
    return sorted([{'id': sale.id, 'customer_reference': sale.customer_reference,
                    'order_id': sale.order_id, 'order_number': number,
                    'removed': sale.id not in current, 'deleted': sale.deleted_at is not None}
                   for sale, number in rows], key=lambda row: (row['removed'], row['customer_reference'], row['id']))
