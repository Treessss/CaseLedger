from datetime import datetime, timedelta

import pytest

from app import db
from app.models import MailDraft, MailDraftJob, MailMessage, Order
from app.services import mail_client, mail_support_service
from app.services import mail_draft_job_service as service
from app.services.mail_scope import mailbox_scope
from app.utils.errors import NotFoundError, ValidationError


@pytest.fixture()
def jobs(app, monkeypatch):
    with app.app_context(), mailbox_scope(1):
        mail_client.put_setting('consent', 'true')
        monkeypatch.setattr(service.mail_service, '_deepseek_key', lambda: 'test')
        monkeypatch.setattr(service, 'dispatch', lambda *_args, **_kwargs: None)
        for key, status in [('todo', 'pending'), ('ignored', 'ignored'), ('done', 'pending')]:
            db.session.add(MailMessage(id=key, provider_message_id=key, shop_id=1,
                direction='in', status=status, is_latest=True, thread_id=key, sent_at=10,
                body='test', hydrated=True, header_checked=True))
        db.session.commit()
        db.session.add(MailDraft(message_id='done', reply='Human draft'))
        db.session.commit()
        yield


def test_enqueue_is_scoped_missing_only_and_idempotent(jobs):
    job = service.enqueue({'all': True})
    assert job['total'] == 1
    assert service.enqueue({'all': True})['id'] == job['id']
    with mailbox_scope(2), pytest.raises(NotFoundError):
        service.cancel(job['id'])


def test_process_cancel_no_external_send_or_coupon(jobs, monkeypatch):
    monkeypatch.setattr(service.mail_support_service, 'generate', lambda *_a, **_k: {})
    job = service.enqueue({'all': True})
    service.process_one(job['id'])
    assert service.latest()['succeeded'] == 1
    assert service.latest()['status'] == 'completed'


def test_cancel_skips_processing(jobs, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('Cancelled job must not generate')
    monkeypatch.setattr(service.mail_support_service, 'generate', forbidden)
    job = service.enqueue({'all': True})
    service.cancel(job['id'])
    service.process_one(job['id'])
    assert service.latest()['done'] == 0


def test_retries_then_records_failure(jobs, monkeypatch):
    def failing(*_args, **_kwargs):
        raise ValidationError('DeepSeek 暂时繁忙（HTTP 429），稍后重试')
    monkeypatch.setattr(service.mail_support_service, 'generate', failing)
    job = service.enqueue({'all': True})
    service.process_one(job['id'])
    assert service.latest()['done'] == 0
    service.process_one(job['id'])
    service.process_one(job['id'])
    result = service.latest()
    assert result['failed'] == 1
    assert result['failures'][0]['message_id'] == 'todo'
    assert service.enqueue({'retry_job_id': job['id']})['total'] == 1


def test_existing_manual_draft_never_overwritten(jobs, monkeypatch):
    job = service.enqueue({'all': True})
    db.session.add(MailDraft(message_id='todo', reply='Operator draft'))
    db.session.commit()
    monkeypatch.setattr(service.mail_support_service, 'generate', lambda *_a, **_k: pytest.fail('overwrite'))
    service.process_one(job['id'])
    assert service.latest()['skipped'] == 1


def test_configuration_failure_stops_batch(jobs, monkeypatch):
    def failing(*_args, **_kwargs):
        raise ValidationError('DeepSeek 调用失败（HTTP 401），请检查 Key 与余额')
    monkeypatch.setattr(service.mail_support_service, 'generate', failing)
    job = service.enqueue({'all': True})
    service.process_one(job['id'])
    assert service.latest()['status'] == 'failed'
    assert service.latest()['failed'] == 1


def test_recovery_only_resumes_requested_jobs(jobs, monkeypatch):
    calls = []
    monkeypatch.setattr(service, 'dispatch', lambda key, **_kwargs: calls.append(key))
    job = service.enqueue({'all': True})
    calls.clear()
    row = db.session.get(MailDraftJob, job['id'])
    row.heartbeat_at = datetime.utcnow() - timedelta(minutes=12)
    db.session.commit()
    service.recover()
    assert calls == [job['id']]


def test_sync_timestamp_does_not_invalidate_but_business_change_does(jobs):
    row = db.session.get(MailMessage, 'todo')
    order = Order(shop_id=1, shopify_order_id='test', order_number='100', total_price=1,
        subtotal_price=1, order_date=datetime.utcnow(), warehouse_status='pending')
    db.session.add(order)
    db.session.commit()
    before = mail_support_service.fingerprint(row, order)
    order.updated_at = datetime.utcnow() + timedelta(minutes=1)
    db.session.commit()
    assert mail_support_service.fingerprint(row, order) == before
    order.warehouse_status = 'shipped'
    db.session.commit()
    assert mail_support_service.fingerprint(row, order) != before


def test_job_endpoints_require_mail_permission(client, limited_headers):
    assert client.get('/api/mail/draft-jobs?shop_id=1', headers=limited_headers).status_code == 403
    assert client.post('/api/mail/draft-jobs?shop_id=1', headers=limited_headers,
                       json={'all': True}).status_code == 403
    assert client.post('/api/mail/draft-jobs/unknown/cancel?shop_id=1',
                       headers=limited_headers).status_code == 403


def test_truncated_ai_json_is_retried_with_larger_budget(jobs, monkeypatch):
    from types import SimpleNamespace
    calls = []
    def post(_url, **kwargs):
        calls.append(kwargs['json']['max_tokens'])
        reason = 'length' if len(calls) == 1 else 'stop'
        return SimpleNamespace(status_code=200, json=lambda: {'choices': [{
            'finish_reason': reason, 'message': {'content': '{"reply":"draft"}'}}]})
    monkeypatch.setattr(service.mail_service.requests, 'post', post)
    assert service.mail_service._deepseek([], max_tokens=2200)['reply'] == 'draft'
    assert calls == [2200, 4400]


def test_rate_limit_is_not_misreported_as_invalid_key(jobs, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(service.mail_service.requests, 'post',
                        lambda *_a, **_k: SimpleNamespace(status_code=429))
    with pytest.raises(ValidationError, match='暂时繁忙'):
        service.mail_service._deepseek([])
