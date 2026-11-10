"""Incremental scheduling must not regress to full order scans or duplicate carrier calls."""
from datetime import datetime, timedelta
from unittest.mock import Mock

from sqlalchemy import event

from app import db
from app.models import (Order, ShippingParcel, Shop, TrackingBinding, TrackingChange,
                        TrackingDiscoveryState, TrackingRecord, TrackingRefreshJob)
from app.services import logistics_service as service
from tests.test_logistics import facts, tracking_case as _shared_tracking_case

tracking_case = _shared_tracking_case


def record_for(case, key):
    return db.session.get(TrackingBinding, case[key]['binding']).record


def test_unchanged_and_rolled_back_sources_do_not_enqueue(app, tracking_case):
    with app.app_context():
        TrackingChange.query.delete()
        db.session.commit()
        order = db.session.get(Order, tracking_case['B']['order'])
        order.note = 'Only a note; no tracking work'
        db.session.commit()
        assert TrackingChange.query.count() == 0
        order.shipping_label_number = '4PXROLLBACK'
        db.session.flush()
        assert TrackingChange.query.count() == 1
        db.session.rollback()
        assert TrackingChange.query.count() == 0


def test_tracking_change_is_coalesced_and_does_not_scan_other_orders(app, tracking_case):
    case = tracking_case
    with app.app_context():
        TrackingChange.query.delete()
        db.session.commit()
        order = db.session.get(Order, case['B']['order'])
        parcel = order.parcels[0]
        order.shipping_label_number = parcel.label_number = '4PXNEWLABEL'
        db.session.commit()
        assert TrackingChange.query.count() == 1
        before = TrackingRecord.query.count()
        service.drain_changes(case['shop'])
        db.session.commit()
        assert TrackingRecord.query.count() == before + 1
        assert TrackingChange.query.count() == 0
        assert not db.session.get(TrackingBinding, case['B']['binding']).active
        fresh = TrackingRecord.query.filter_by(number='4PXNEWLABEL').one()
        assert fresh.requested_at and fresh.next_check_at <= datetime.utcnow()
        assert record_for(case, 'A').next_check_at > datetime.utcnow()


def test_due_ticks_never_call_full_discovery_or_bypass_cache(app, tracking_case, monkeypatch):
    with app.app_context():
        service.drain_changes(tracking_case['shop'])
        db.session.commit()
        monkeypatch.setattr(service, 'discover', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('no full discovery')))
        result = service.create_job(tracking_case['shop'], 'all', force=True)
        assert result['scope'] == 'due' and result['total'] == 0
        assert record_for(tracking_case, 'E').next_check_at is None


def test_timing_for_preinfo_unshipped_movement_and_recent_dispatch(app, tracking_case):
    case = tracking_case
    with app.app_context():
        service.drain_changes(case['shop'])
        now = datetime.utcnow()
        # Unshipped, no transport yet: one hour. Transport observed: six hours.
        service.save_facts(record_for(case, 'D'), facts(case['D']['number'], repeat=True))
        assert timedelta(minutes=59) < record_for(case, 'D').next_check_at-now < timedelta(minutes=61)
        assert timedelta(hours=5, minutes=59) < record_for(case, 'B').next_check_at-now < timedelta(hours=6, minutes=1)
        # A just-scanned parcel waits for its 24-hour observation window.
        order = db.session.get(Order, case['A']['order'])
        order.shipped_at = order.parcels[0].scanned_at = now-timedelta(hours=2)
        db.session.flush()
        service.drain_changes(case['shop'])
        assert record_for(case, 'A').next_check_at == order.shipped_at+timedelta(days=1)
        assert record_for(case, 'E').next_check_at is None


def test_single_force_can_refresh_delivered_but_cannot_refresh_other_numbers(app, client, admin_headers, tracking_case, monkeypatch):
    case = tracking_case
    calls = []
    monkeypatch.setattr(service, 'fetch_facts', lambda _shop, nums: calls.extend(nums) or {
        num: facts(num, delivered=True) for num in nums})
    response = client.post('/api/logistics/refresh', headers=admin_headers,
        json={'shop_id': case['shop'], 'scope': 'single', 'binding_id': case['E']['binding']})
    assert response.status_code == 200
    job_id = response.get_json()['data']['id']
    with app.app_context():
        assert len(db.session.get(TrackingRefreshJob, job_id).record_ids) == 1
        service.process_batch(job_id)
        assert calls == [case['E']['number']]
        assert record_for(case, 'E').next_check_at is None
        assert not record_for(case, 'E').requested_at


def test_history_cannot_block_incremental_and_never_rechecks_attempted_records(app, tracking_case):
    case = tracking_case
    with app.app_context():
        service.drain_changes(case['shop'])
        unknown = record_for(case, 'A')
        unknown.checked_at = unknown.attempted_at = None
        unknown.next_check_at = datetime.utcnow()-timedelta(seconds=1)
        due = record_for(case, 'B')
        due.checked_at = datetime.utcnow()-timedelta(hours=8)
        due.next_check_at = datetime.utcnow()-timedelta(seconds=1)
        db.session.commit()
        old = service.create_job(case['shop'], 'history')
        new = service.create_job(case['shop'], 'due')
        assert old['total'] == new['total'] == 1 and old['id'] != new['id']
        assert db.session.get(TrackingRefreshJob, old['id']).record_ids == [unknown.id]
        assert db.session.get(TrackingRefreshJob, new['id']).record_ids == [due.id]


def test_record_lease_and_last_minute_recheck_prevent_duplicate_fetches(app, tracking_case, monkeypatch):
    case = tracking_case
    with app.app_context():
        service.drain_changes(case['shop'])
        record = record_for(case, 'B')
        record.next_check_at = datetime.utcnow()-timedelta(seconds=1)
        db.session.add_all([TrackingRefreshJob(id=value, shop_id=case['shop'], scope='due', record_ids=[record.id])
                            for value in ('first', 'duplicate')])
        db.session.commit()
        fetch = Mock(return_value={record.number: facts(record.number, moved=True)})
        monkeypatch.setattr(service, 'fetch_facts', fetch)
        service.process_batch('first')
        service.process_batch('duplicate')
        assert sum(len(call.args[1]) for call in fetch.call_args_list) == 1
        assert db.session.get(TrackingRefreshJob, 'duplicate').skipped == 1


def test_source_change_during_http_is_not_overwritten(app, tracking_case, monkeypatch):
    case = tracking_case
    with app.app_context():
        service.drain_changes(case['shop'])
        record = record_for(case, 'B')
        record.next_check_at = datetime.utcnow()-timedelta(seconds=1)
        db.session.add(TrackingRefreshJob(id='changing', shop_id=case['shop'], scope='due', record_ids=[record.id]))
        db.session.commit()
        def changed(_shop, nums):
            record.requested_at = datetime.utcnow()
            record.next_check_at = record.requested_at
            db.session.commit()
            return {num: facts(num) for num in nums}
        monkeypatch.setattr(service, 'fetch_facts', changed)
        service.process_batch('changing')
        assert record_for(case, 'B').has_movement
        assert record_for(case, 'B').requested_at
        assert db.session.get(TrackingRefreshJob, 'changing').skipped == 1


def test_bootstrap_finishes_once_and_never_rescans_completed_history(app, tracking_case, monkeypatch):
    with app.app_context():
        calls = []
        original = service.discover
        def discover(shop_id, order_ids=None, **kwargs):
            assert order_ids is not None
            calls.extend(order_ids)
            return original(shop_id, order_ids, **kwargs)
        monkeypatch.setattr(service, 'discover', discover)
        for _ in range(10):
            service.bootstrap(tracking_case['shop'], limit=2)
            db.session.commit()
        state = db.session.get(TrackingDiscoveryState, tracking_case['shop'])
        assert state.completed and len(calls) == len(set(calls))
        before = list(calls)
        service.bootstrap(tracking_case['shop'], limit=2)
        assert before == calls


def test_future_work_uses_indexed_record_clock_not_order_payloads(app, tracking_case):
    with app.app_context():
        service.drain_changes(tracking_case['shop'])
        db.session.commit()
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement.lower())
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            service.create_job(tracking_case['shop'])
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        assert not any('orders.fulfillments' in statement for statement in statements)
        assert any('tracking_records.next_check_at <=' in statement for statement in statements)


def test_repeated_failures_back_off_and_retain_last_known_facts(app, tracking_case):
    with app.app_context():
        record = record_for(tracking_case, 'B')
        latest = record.latest
        intervals = []
        for _ in range(6):
            service.save_facts(record, {'error': 'test provider failure'})
            intervals.append(round((record.next_check_at-record.attempted_at).total_seconds()/60))
        assert intervals == [30, 60, 120, 240, 360, 360]
        assert record.latest == latest
