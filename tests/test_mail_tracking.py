"""Carrier tracking facts: identity, real timezones, safe failure and read-only use."""
import json
from types import SimpleNamespace

import pytest

from app.models.shop import ShopCredentialError
from app.services import mail_tracking_service as service
from app.services.fourpx_client import FourPxError

NUMBER = '4PX3003188042135CN'


def event(time='2026-10-05 08:46:45', zone='UTC+09:30', code='FPX_S_OK', message='Delivered-Left at safe place'):
    return {'occurDatetime': time, 'timeZone': zone, 'businessLinkCode': code, 'trackingContent': message}


def payload(*events):
    return {'deliveryOrderNo': NUMBER, 'trackingList': list(events)}


def test_actual_delivery_timezone_and_json_string_response():
    value = service.normalize_tracking(json.dumps(payload(event())), NUMBER)
    assert value['latest']['happenedAt'] == '2026-10-04T23:16:45+00:00'
    assert value['delivered_at'] == value['latest']['happenedAt']
    assert value['latest']['status'] == 'DELIVERED'
    assert value['latest']['message'] == 'Delivered-Left at safe place'
    assert value['source'] == '4PX official tracking API'


def test_latest_is_sorted_by_utc_not_return_order_or_local_wall_clock():
    value = service.normalize_tracking(payload(
        event('2026-10-05 09:00:00', 'UTC+10:00', 'FPX_D_SD', 'Out for delivery'),
        event('2026-10-05 08:46:45', 'UTC+09:30')), NUMBER)
    assert value['latest']['status'] == 'DELIVERED'
    assert value['event_count'] == 2


@pytest.mark.parametrize('data', [
    payload(), payload(event(zone='')), payload(event(zone='UTC+14:30')),
    payload(event(time='2999-01-01 00:00:00')), payload(event(message='')),
    {'deliveryOrderNo': 'OTHER', 'trackingList': [event()]}, '{invalid', [],
])
def test_incomplete_mismatched_or_unreliable_events_are_not_invented(data):
    with pytest.raises(FourPxError):
        service.normalize_tracking(data, NUMBER)


def test_explicit_iso_offset_without_named_zone_is_valid():
    value = service.normalize_tracking(payload(event('2026-10-05T08:46:45+09:30', '')), NUMBER)
    assert value['latest']['happenedAt'] == '2026-10-04T23:16:45+00:00'


@pytest.mark.parametrize('zone,expected', [
    ('UTC+8', '2026-09-15T01:33:36+00:00'),
    ('UTC+08', '2026-09-15T01:33:36+00:00'),
    ('GMT-5', '2026-09-15T14:33:36+00:00'),
    ('UTC+05:30', '2026-09-15T04:03:36+00:00'),
])
def test_official_hour_only_timezone_is_explicit_not_an_assumed_local_time(zone, expected):
    value = service.normalize_tracking(payload(event('2026-09-15 09:33:36', zone)), NUMBER)
    assert value['latest']['happenedAt'] == expected


@pytest.mark.parametrize('zone', ['UTC+15', 'GMT-99', 'UTC+14:01', 'UTC+8:3', 'local', ''])
def test_invalid_or_missing_timezone_still_fails_closed(zone):
    with pytest.raises(FourPxError):
        service.normalize_tracking(payload(event(zone=zone)), NUMBER)


def test_in_transit_is_not_treated_as_delivered():
    value = service.normalize_tracking(payload(event(code='FPX_D_SD', message='Out for delivery')), NUMBER)
    assert value['latest']['status'] == 'FPX_D_SD'
    assert value['delivered_at'] is None


@pytest.mark.parametrize(('code', 'message'), [
    ('FPX_S_OKCC', 'Parcel delivered - collected from pick up location'),
    ('FPX_S_OKCC', 'Delivered - left as authorised'),
    ('FPX_UNDIFINED', 'Signature Obtained'),
    ('', 'Delivered, In/At Mailbox'),
    ('FPX_UNDIFINED', 'Parcel has been delivered to recipient'),
    ('fpx_s_okcc ', 'Completed'),
])
def test_collection_authorised_delivery_and_explicit_last_mile_signature(code, message):
    value = service.normalize_tracking(payload(event(code=code, message=message)), NUMBER)
    assert value['latest']['status'] == 'DELIVERED'
    assert value['delivered_at'] == value['latest']['happenedAt']


@pytest.mark.parametrize(('code', 'message'), [
    ('FPX_R_RDOW', 'Delivered, To Original Sender'),
    ('FPX_R_RDOW', 'Signature available'),
    ('FPX_R_RPD', 'Parcel delivered'),
    ('FPX_M_PRTS', 'THE ITEM WAS SUCCESSFULLY RETURNED TO THE SENDER.'),
    ('FPX_S_OKCC', 'Delivered to original sender'),
    ('', 'Delivered, returned to sender'),
    ('', 'Not delivered'),
    ('', 'Delivery failed'),
    ('FPX_D_SD', 'Out for Delivery'),
    ('FPX_D_SASPS', 'Parcel available for collection'),
    ('FPX_D_SASPS', 'AWAITING COLLECTION AT AUSTRALIA POST'),
    ('FPX_D_UNDG', 'Parcel Disposal'),
    ('FPX_S_UNKNOWN', 'Awaiting delivery'),
])
def test_return_disposal_attempt_and_awaiting_collection_are_not_customer_delivery(code, message):
    value = service.normalize_tracking(payload(event(code=code, message=message)), NUMBER)
    assert value['latest']['status'] != 'DELIVERED'
    assert value['delivered_at'] is None


def shop():
    return SimpleNamespace(get_fourpx_credentials=lambda: ('own-shop-key', 'own-shop-secret'),
                           fourpx_environment='production')


def test_enriches_missing_shopify_events_once_and_keeps_other_carriers_unchanged():
    calls = []
    class Client:
        def __init__(self, key, secret, *, environment):
            assert (key, secret, environment) == ('own-shop-key', 'own-shop-secret', 'production')

        def get_tracking(self, number):
            calls.append(number)
            return payload(event())
    source = [{'number': NUMBER, 'url': 'https://track.4px.com/#/result/0/' + NUMBER, 'latest': None},
              {'number': NUMBER, 'company': '4PX', 'latest': None},
              {'number': 'YT123', 'company': 'YunExpress', 'latest': None},
              {'number': 'https://untrusted.example', 'company': '4PX', 'latest': None}]
    result = service.enrich_tracking(shop(), source, client_factory=Client)
    assert calls == [NUMBER]
    assert result[0]['latest']['status'] == result[1]['latest']['status'] == 'DELIVERED'
    assert source[0]['latest'] is None  # No hidden writes to the saved snapshot.
    assert result[2:] == source[2:]


def test_carrier_failure_preserves_reliable_shopify_facts_and_hides_secrets():
    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_tracking(self, _number):
            raise FourPxError('signed URL and secret must never escape')
    latest = {'happenedAt': '2026-10-06T00:00:00Z', 'status': 'DELIVERED'}
    result = service.enrich_tracking(shop(), [{'number': NUMBER, 'latest': latest}], client_factory=Client)
    assert result[0]['latest'] == latest
    assert 'secret' not in result[0]['tracking_warning']
    assert 'tracking_error' not in result[0]


def test_missing_shop_credentials_does_not_use_another_shops_account():
    def missing():
        raise ShopCredentialError('not configured')
    scope = SimpleNamespace(get_fourpx_credentials=missing)
    result = service.enrich_tracking(scope, [{'number': NUMBER, 'latest': None}])
    assert result[0]['latest'] is None
    assert '凭据' in result[0]['tracking_error']


def test_newer_reliable_shopify_event_is_not_overwritten():
    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_tracking(self, _number):
            return payload(event())
    latest = {'happenedAt': '2026-10-06T00:00:00Z', 'status': 'DELIVERED', 'message': 'Delivered'}
    source = {'number': NUMBER, 'latest': latest, 'source': 'Shopify fulfillment event'}
    result = service.enrich_tracking(shop(), [source], client_factory=Client)
    assert result[0]['latest'] == latest
    assert result[0]['source'] == source['source']


def test_cached_tracking_is_scoped_read_only_and_supports_non_4px_prefix(app):
    from app import db
    from app.models import TrackingRecord
    from datetime import datetime
    with app.app_context():
        latest = {'happenedAt': '2026-09-02T07:00:00+00:00', 'message': 'Parcel received and sorted', 'status': 'FPX_O_RR'}
        db.session.add(TrackingRecord(shop_id=1, number='GV26TEST', latest=latest, checked_at=datetime.utcnow()))
        db.session.commit()
        source = [{'number': 'GV26TEST', 'latest': None, 'tracking_error': 'old error'}]
        result = service.cached_tracking(1, source)
        assert result[0]['latest'] == latest and result[0]['tracking_cached']
        assert 'tracking_error' not in result[0]
        assert result[0]['url'] == 'https://track.4px.com/#/result/0/GV26TEST'
        assert service.cached_tracking(2, source) == source
        assert source[0]['latest'] is None
        assert not db.session.dirty


@pytest.mark.parametrize('delivered', [False, True])
def test_fresh_or_delivered_cache_does_not_call_carrier_or_require_credentials(app, delivered):
    from app import db
    from app.models import TrackingRecord
    from datetime import datetime, timedelta
    with app.app_context():
        db.session.add(TrackingRecord(shop_id=1, number=NUMBER, checked_at=datetime.utcnow()-timedelta(
            days=20 if delivered else 0), delivered_at=datetime.utcnow()-timedelta(days=20) if delivered else None,
            latest={'happenedAt': '2026-09-02T07:00:00+00:00', 'status': 'DELIVERED' if delivered else 'FPX_O_RR'}))
        db.session.commit()
        def forbidden(*_a, **_k):
            pytest.fail('Fresh or delivered cache must not make a network call or read credentials')
        owner = SimpleNamespace(id=1, get_fourpx_credentials=forbidden)
        result = service.enrich_tracking(owner, [{'number': NUMBER, 'latest': None}], client_factory=forbidden)
        assert result[0]['latest'] and result[0]['tracking_checked_at']


def test_failed_live_refresh_preserves_stale_cache_and_real_checked_time(app):
    from app import db
    from app.models import TrackingRecord
    from datetime import datetime, timedelta
    with app.app_context():
        at = datetime.utcnow()-timedelta(days=1)
        latest = {'happenedAt': '2026-09-02T07:00:00+00:00', 'status': 'FPX_O_RR'}
        db.session.add(TrackingRecord(shop_id=1, number=NUMBER, checked_at=at, latest=latest))
        db.session.commit()
        class Client:
            def __init__(self, *_a, **_k):
                pass
            def get_tracking(self, _number):
                raise FourPxError('signed URL with private secret')
        owner = shop()
        owner.id = 1
        result = service.enrich_tracking(owner, [{'number': NUMBER, 'latest': None}], client_factory=Client)
        assert result[0]['latest'] == latest
        assert result[0]['tracking_checked_at'] == at.isoformat()+'Z'
        assert '保留' in result[0]['tracking_warning'] and 'secret' not in str(result)
        assert not db.session.dirty


def test_known_ols_alias_with_failed_cache_is_queried_and_recovers(app):
    from app import db
    from app.models import TrackingRecord
    with app.app_context():
        number = 'OLS3060227832782'
        db.session.add(TrackingRecord(shop_id=1, number=number, error='previous failure'))
        db.session.commit()
        class Client:
            def __init__(self, *_a, **_k):
                pass
            def get_tracking(self, value):
                assert value == number
                return {'deliveryOrderNo': number, 'trackingList': [event()]}
        owner = shop()
        owner.id = 1
        result = service.enrich_tracking(owner, [{'number': number, 'latest': None}], client_factory=Client)
        assert result[0]['latest']['status'] == 'DELIVERED'
        assert 'tracking_error' not in result[0]
