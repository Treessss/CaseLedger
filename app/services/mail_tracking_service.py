"""Carrier-backed mail facts; fixed API hosts, scoped credentials, no mail writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit

from app import db
from app.models.tracking import TrackingRecord
from app.models.shop import ShopCredentialError
from app.services.fourpx_client import FourPxClient, FourPxError


def is_delivered_event(code, message):
    """Customer delivery only; returned-to-sender signatures are not delivery.

    OKCC is used by 4PX for collection/authorised delivery as well as OK.
    Unknown last-mile codes may carry an explicit completed-delivery message;
    do not infer success from 'delivery', 'collection' or a code prefix alone.
    """
    code = str(code or '').strip().upper()
    message = ' '.join(str(message or '').split()).casefold()
    if code.startswith('FPX_R_') or code == 'FPX_M_PRTS' or re.search(
            r'\b(?:not|never|unsuccessful|failed|failure|unable|undelivered|return(?:ed|ing)?|sender|shipper)\b', message):
        return False
    if code in {'FPX_S_OK', 'FPX_S_OKCC'}:
        return True
    return bool(re.match(
        r'^(?:(?:parcel|package|shipment|item) (?:was |has been )?delivered\b'
        r'|(?:successfully )?delivered\b|delivery (?:completed|successful)\b'
        r'|signature obtained[.!]?$|signed for by (?:the )?(?:recipient|customer)\b)', message))


def _event_time(event):
    try:
        stamp = datetime.fromisoformat(str(event.get('occurDatetime') or '').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            zone = str(event.get('timeZone') or '').strip().upper()
            if zone in ('UTC', 'GMT', 'Z'):
                offset = timedelta()
            else:
                match = re.fullmatch(r'(?:UTC|GMT)([+-])(\d{1,2})(?::(\d{2}))?', zone)
                if not match:
                    raise ValueError('missing carrier timezone')
                hours, minutes = int(match[2]), int(match[3] or 0)
                if hours > 14 or minutes > 59 or (hours == 14 and minutes):
                    raise ValueError('invalid carrier timezone')
                offset = timedelta(hours=hours, minutes=minutes) * (1 if match[1] == '+' else -1)
            stamp = stamp.replace(tzinfo=timezone(offset))
        stamp = stamp.astimezone(timezone.utc)
        if stamp > datetime.utcnow().replace(tzinfo=timezone.utc) + timedelta(minutes=5):
            raise ValueError('future carrier event')
        return stamp
    except (ValueError, TypeError, OverflowError):
        raise FourPxError('4PX 轨迹时间或时区无法核验，请人工查询') from None


def normalize_tracking(data, number, *, include_events=False):
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise FourPxError('4PX 轨迹响应格式异常') from None
    if (not isinstance(data, dict)
            or str(data.get('deliveryOrderNo') or '').upper() != number.upper()):
        raise FourPxError('4PX 返回的物流号与所查运单不一致')
    events = data.get('trackingList')
    if not isinstance(events, list) or not events or len(events) > 2000:
        raise FourPxError('4PX 暂无可核验的轨迹记录')
    normalized = []
    for event in events:
        if not isinstance(event, dict) or not str(event.get('trackingContent') or '').strip():
            raise FourPxError('4PX 轨迹内容不完整')
        stamp = _event_time(event)
        code = str(event.get('businessLinkCode') or '').strip().upper()
        normalized.append({'happenedAt': stamp.isoformat(),
            'message': str(event['trackingContent']).strip(),
            'status': 'DELIVERED' if is_delivered_event(code, event['trackingContent']) else code or 'TRACKING_EVENT',
            'carrier_code': code})
    # The carrier list is not assumed sorted; compare actual UTC instants.
    latest = max(normalized, key=lambda event: event['happenedAt'])
    delivered = [event['happenedAt'] for event in normalized if event['status'] == 'DELIVERED']
    preinfo = [event for event in normalized if event['carrier_code'] == 'FPX_L_RPIF'
               or ' '.join(event['message'].split()).casefold().rstrip('.') == 'parcel information received']
    return {'latest': latest, 'delivered_at': max(delivered) if delivered else None,
            'event_count': len(normalized), 'source': '4PX official tracking API',
            'only_preinfo': len(normalized) == 1 and len(preinfo) == 1,
            'has_movement': len(preinfo) < len(normalized),
            'first_preinfo_at': min((event['happenedAt'] for event in preinfo), default=None),
            **({'events': sorted(normalized, key=lambda event: event['happenedAt'], reverse=True)[:100]}
               if include_events else {}),
            'tracking_checked_at': datetime.utcnow().isoformat() + 'Z'}


def _is_fourpx(item):
    number = str(item.get('number') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9-]{1,120}', number):
        return False
    try:
        host = (urlsplit(str(item.get('url') or '')).hostname or '').lower()
    except ValueError:
        host = ''
    return (number.upper().startswith('4PX') or host == 'track.4px.com'
            or str(item.get('company') or '').strip().casefold() in ('4px', '4px express'))


def cached_tracking(shop_id, tracking):
    """Fast, read-only, shop-scoped overlay; never rewrite a saved reply or observation."""
    result = [dict(item) for item in tracking]
    for item in result:
        item.pop('tracking_cached', None)  # Freshness must come from this shop's current database, not an old draft flag.
        item.pop('tracking_carrier', None)
    if not isinstance(shop_id, int) or shop_id < 1:
        return result
    numbers = list(dict.fromkeys(str(item.get('number') or '').strip() for item in result
        if re.fullmatch(r'[A-Za-z0-9-]{1,120}', str(item.get('number') or '').strip())))[:100]
    if not numbers:
        return result
    records = {record.number: record for record in db.session.query(
        TrackingRecord.number, TrackingRecord.latest, TrackingRecord.delivered_at,
        TrackingRecord.checked_at, TrackingRecord.error).filter(
            TrackingRecord.shop_id == shop_id, TrackingRecord.number.in_(numbers)).all()}
    for item in result:
        number = str(item.get('number') or '').strip()
        record = records.get(number)
        if record is None:
            continue
        # Known 4PX aliases (GV/OLS) remain queryable even before their first successful cache fill.
        item['tracking_carrier'] = '4px'
        item['url'] = 'https://track.4px.com/#/result/0/' + number
        if record.checked_at is None or not isinstance(record.latest, dict):
            if record.error and not item.get('latest'):
                item['tracking_error'] = '4PX 上次查询未成功，尚无已核验轨迹；可重新生成草稿重试'
            continue
        try:
            cached_at = _event_time({'occurDatetime': record.latest.get('happenedAt')})
        except FourPxError:
            continue
        try:
            old_at = _event_time({'occurDatetime': (item.get('latest') or {}).get('happenedAt')})
        except FourPxError:
            old_at = None
        if old_at is not None and old_at > cached_at:
            continue
        item.update(latest=dict(record.latest), source='4PX official tracking API', tracking_cached=True,
                    tracking_checked_at=record.checked_at.isoformat()+'Z',
                    delivered_at=record.delivered_at.isoformat()+'Z' if record.delivered_at else item.get('delivered_at'))
        item.pop('tracking_error', None)
        item['url'] = 'https://track.4px.com/#/result/0/' + number
        if record.error:
            item['tracking_warning'] = '最近一次查询失败；当前显示上次成功查询的可靠轨迹'
        else:
            item.pop('tracking_warning', None)
    return result


def _fresh_cache(item):
    if not item.get('tracking_cached'):
        return False
    try:
        checked_at = _event_time({'occurDatetime': item.get('tracking_checked_at')})
    except FourPxError:
        return False
    return bool(item.get('delivered_at') or datetime.utcnow().replace(tzinfo=timezone.utc)-checked_at < timedelta(hours=6))


def enrich_tracking(shop, tracking, *, client_factory=None):
    """Fetch each known carrier number once. Never request a URL from an email."""
    result = cached_tracking(getattr(shop, 'id', None), tracking)
    numbers = list(dict.fromkeys(str(item['number']).strip() for item in result
        if (_is_fourpx(item) or item.get('tracking_carrier') == '4px') and not _fresh_cache(item)))
    if not numbers:
        return result
    try:
        key, secret = shop.get_fourpx_credentials()
    except ShopCredentialError:
        return [{**item, 'tracking_error': '此店铺未配置可用的4PX轨迹查询凭据'}
                if str(item.get('number') or '').strip() in numbers else item for item in result]
    factory = client_factory or FourPxClient
    environment = shop.fourpx_environment

    def fetch(number):
        try:
            value = factory(key, secret, environment=environment).get_tracking(number)
            return normalize_tracking(value, number)
        except (FourPxError, ValueError, TypeError):
            # Never return/log signed URLs, credentials, or raw provider errors.
            return {'tracking_error': '4PX轨迹暂时获取失败或数据无法核验，请稍后重新生成草稿'}

    # Bound latency and concurrency even for unusual many-parcel orders.
    selected = numbers[:12]
    with ThreadPoolExecutor(max_workers=min(3, len(selected))) as executor:
        fetched = dict(zip(selected, executor.map(fetch, selected)))
    for item in result:
        number = str(item.get('number') or '').strip()
        if number in fetched:
            facts = fetched[number]
            if facts.get('tracking_error') and item.get('latest'):
                item['tracking_warning'] = '实时查询未完成；保留上次可靠轨迹，请留意下方查询时间'
                continue
            if facts.get('latest'):
                old = item.get('latest') or {}
                # Do not replace a later, timezone-qualified Shopify event with older carrier data.
                try:
                    stamp = datetime.fromisoformat(str(old.get('happenedAt') or '').replace('Z', '+00:00'))
                    newer = stamp.tzinfo is not None and stamp > datetime.fromisoformat(facts['latest']['happenedAt'])
                except (ValueError, TypeError):
                    newer = False
                if newer:
                    facts = {**facts, 'latest': old, 'source': item.get('source') or 'Shopify fulfillment event',
                             'delivered_at': item.get('delivered_at') or facts['delivered_at']}
            if facts.get('latest'):
                item.pop('tracking_error', None)
                item.pop('tracking_warning', None)
                item['tracking_cached'] = False
            item.update(facts)
        elif number in numbers:
            item['tracking_error'] = '包裹数量较多，请人工核对剩余包裹轨迹'
    return result
