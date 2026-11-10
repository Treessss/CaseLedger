"""Workflow tabs are independent of shared-stock availability."""
from sqlalchemy import or_

from app import db
from app.models import AfterSale, PurchaseList
from app.utils.errors import ValidationError


def apply_view(query, view):
    view = view or 'all'
    if view not in ('all', 'unprocessed', 'unprocured', 'shipped'):
        raise ValidationError('售后分类无效')
    # Fetch only identity/workflow columns, never full historical case bodies.
    scope = query.with_entities(AfterSale.id, AfterSale.shop_id).all()
    shops = {row.shop_id for row in scope}
    confirmed = set()
    for single, allocations in db.session.query(PurchaseList.after_sale_id, PurchaseList.allocations).filter(
            PurchaseList.shop_id.in_(shops), PurchaseList.status.in_(('ordered', 'partially_received', 'received'))).all():
        if single:
            confirmed.add(single)
        confirmed.update(int(row['after_sale_id']) for row in allocations or []
                         if row.get('after_sale_id') and int(row.get('quantity') or 0) > 0)
    shipped = or_(AfterSale.status == 'shipped', AfterSale.shipped_at.isnot(None))
    unfinished = query.filter(~shipped)
    # A draft, cancelled purchase or available shared stock is not a confirmed purchase.
    unprocured = unfinished.filter(AfterSale.id.notin_(confirmed), AfterSale.purchased_at.is_(None))
    counts = {'all': len(scope), 'unprocessed': unfinished.count(),
              'unprocured': unprocured.count(), 'shipped': query.filter(shipped).count()}
    return {'all': query, 'unprocessed': unfinished, 'unprocured': unprocured,
            'shipped': query.filter(shipped)}[view], counts
