"""Reclassify saved 4PX signatures for one shop; dry-run unless --apply.

No carrier requests, emails, Shopify updates, inventory or warehouse writes.
Back up the database before applying. Existing observations keep their timestamps.
"""
import argparse
import json
from datetime import datetime

from sqlalchemy import event

from app import create_app, db
from app.models import TrackingRecord
from app.services import logistics_service as service


def repair(shop_id, *, apply=False):
    def guard(session, *_args):
        changed = set(session.new) | set(session.dirty) | set(session.deleted)
        if any(not isinstance(row, TrackingRecord) for row in changed):
            raise RuntimeError('Delivery repair attempted a non-tracking write')

    event.listen(db.session.session_factory, 'before_flush', guard)
    try:
        cutoff, cursor = datetime.utcnow(), 0
        before = service._query(shop_id).filter(service._tab_filter('overdue', cutoff)).count()
        changed = []
        while True:
            rows = TrackingRecord.query.filter(TrackingRecord.shop_id == shop_id,
                TrackingRecord.id > cursor, TrackingRecord.delivered_at.is_(None),
                TrackingRecord.checked_at.isnot(None)).order_by(TrackingRecord.id).limit(200).with_for_update().all()
            if not rows:
                break
            for row in rows:
                if service.repair_saved_delivery(row):
                    changed.append({'id': row.id, 'number': row.number,
                                    'delivered_at': row.delivered_at.isoformat()})
            cursor = rows[-1].id
            if apply:
                db.session.commit()
            else:
                db.session.flush()
        after = service._query(shop_id).filter(service._tab_filter('overdue', cutoff)).count()
        if apply:
            db.session.commit()
        else:
            db.session.rollback()
        return {'shop_id': shop_id, 'applied': apply, 'overdue_before': before,
                'overdue_after': after, 'corrected': len(changed), 'records': changed}
    finally:
        event.remove(db.session.session_factory, 'before_flush', guard)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shop-id', type=int, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.shop_id < 1:
        parser.error('--shop-id must be positive')
    with create_app().app_context():
        print(json.dumps(repair(args.shop_id, apply=args.apply), ensure_ascii=False))


if __name__ == '__main__':
    main()
