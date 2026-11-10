"""Capture relevant source changes in the *same* DB transaction, without HTTP/broker work."""
from datetime import datetime

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models import AfterSale, Order, ShippingParcel, TrackingChange

FIELDS = {
    Order: ('fulfillments', 'shipping_label_number', 'shipping_label_source', 'warehouse_status', 'shipped_at'),
    AfterSale: ('tracking_number', 'order_id', 'deleted_at', 'status', 'shipped_at'),
    ShippingParcel: ('label_number', 'order_id', 'source', 'scanned_at'),
}


def _before_flush(session, *_args):
    pending = session.info.setdefault('tracking_source_changes', [])
    for obj in session.new.union(session.dirty).union(session.deleted):
        fields = FIELDS.get(type(obj))
        if not fields:
            continue
        if isinstance(obj, Order) and obj in session.deleted:
            continue  # Queue FK is deleted by cascade.
        if obj in session.new or obj in session.deleted or any(inspect(obj).attrs[key].history.has_changes() for key in fields):
            pending.append(obj)


def _after_flush(session, *_args):
    rows = {}
    for obj in session.info.pop('tracking_source_changes', []):
        order_id = obj.id if isinstance(obj, Order) else obj.order_id
        if obj.shop_id and order_id:
            rows[order_id] = {'order_id': order_id, 'shop_id': obj.shop_id, 'updated_at': datetime.utcnow()}
    if not rows:
        return
    connection = session.connection()
    if connection.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(TrackingChange.__table__).values([rows[key] for key in sorted(rows)])
    connection.execute(statement.on_conflict_do_update(index_elements=['order_id'], set_={
        'shop_id': statement.excluded.shop_id, 'updated_at': statement.excluded.updated_at}))


def _rollback(session, *_args):
    session.info.pop('tracking_source_changes', None)


def register():
    if not event.contains(Session, 'before_flush', _before_flush):
        event.listen(Session, 'before_flush', _before_flush)
        event.listen(Session, 'after_flush_postexec', _after_flush)
        event.listen(Session, 'after_rollback', _rollback)
