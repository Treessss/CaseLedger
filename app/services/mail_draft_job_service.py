"""Only explicit user requests enqueue drafts. Never sends mail or creates discounts."""
from datetime import datetime, timedelta
import logging
from uuid import uuid4

from sqlalchemy.orm import aliased

from app import db
from app.models.mail import MailDraft, MailDraftJob, MailMessage
from app.services import mail_client, mail_service, mail_support_service
from app.services.mail_scope import mailbox_scope, shop_id
from app.utils.errors import ApiError, ConflictError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)
ACTIVE = ('queued', 'running')


def candidates():
    newer = aliased(MailMessage)
    return mail_service._messages().filter(
        MailMessage.direction == 'in', MailMessage.is_latest.is_(True),
        MailMessage.ignored_at.is_(None), MailMessage.is_system.is_(False),
        MailMessage.status.in_(('pending', 'followup', 'verify', 'uncertain')),
        ~db.session.query(newer.id).filter(newer.shop_id == MailMessage.shop_id,
            newer.thread_id == MailMessage.thread_id, newer.direction == 'in',
            newer.sent_at > MailMessage.sent_at).exists(),
        ~db.session.query(MailDraft.message_id).filter(MailDraft.message_id == MailMessage.id,
            MailDraft.reply.isnot(None), MailDraft.reply != '').exists())


def serialize(job):
    if not job:
        return None
    results = job.results or {}
    return {'id': job.id, 'status': job.status, 'total': len(job.message_ids or []),
        'done': len(results),
        **{state: sum(r['state'] == state for r in results.values())
           for state in ('succeeded', 'failed', 'skipped')},
        'failures': [{'message_id': key, 'error': value['error']} for key, value in results.items()
                     if value['state'] == 'failed'], 'error': job.error,
        'created_at': job.created_at.isoformat(),
        'finished_at': job.finished_at.isoformat() if job.finished_at else None}


def latest():
    return serialize(MailDraftJob.query.filter_by(shop_id=shop_id()).order_by(
        MailDraftJob.created_at.desc()).first())


def require_job(job_id):
    job = MailDraftJob.query.filter_by(id=job_id, shop_id=shop_id()).first()
    if not job:
        raise NotFoundError('后台草稿任务不存在')
    return job


def enqueue(payload):
    if mail_client.get_setting('consent') != 'true':
        raise ValidationError('请先允许把邮件正文交给 DeepSeek 处理')
    mail_service._deepseek_key()
    lock = mail_client.acquire_lock('draft-job-create', ttl_ms=30_000)
    if not lock:
        raise ConflictError('正在提交任务，请稍后查看进度')
    try:
        active = MailDraftJob.query.filter_by(shop_id=shop_id()).filter(
            MailDraftJob.status.in_(ACTIVE)).first()
        if active:
            return serialize(active)
        query = candidates()
        if payload.get('retry_job_id'):
            old = require_job(str(payload['retry_job_id']))
            ids = [key for key, value in (old.results or {}).items() if value['state'] == 'failed']
            if old.status == 'failed':
                ids += [key for key in old.message_ids if key not in (old.results or {})]
            query = query.filter(MailMessage.id.in_(ids))
        elif payload.get('all') is not True:
            ids = payload.get('message_ids')
            if not isinstance(ids, list) or len(ids) > 100 or not all(isinstance(x, str) for x in ids):
                raise ValidationError('请提供本页邮件编号，最多 100 封')
            query = query.filter(MailMessage.id.in_(ids))
        ids = [row.id for row in query.order_by(MailMessage.sent_at.desc(), MailMessage.id).all()]
        if not ids:
            raise ValidationError('没有需要生成的缺失草稿；已有草稿、旧来信及忽略邮件已排除')
        job = MailDraftJob(id=str(uuid4()), shop_id=shop_id(), status='queued', message_ids=ids, results={})
        db.session.add(job)
        db.session.commit()
        try:
            dispatch(job.id)
        except Exception:
            job.status = 'failed'
            job.error = '后台队列连接失败，请重试任务'
            db.session.commit()
        return serialize(job)
    finally:
        mail_client.release_lock('draft-job-create', lock)


def cancel(job_id):
    job = require_job(job_id)
    if job.status in ACTIVE:
        job.status = 'cancelled'
        job.finished_at = datetime.utcnow()
        db.session.commit()
    return serialize(job)


def dispatch(job_id, countdown=0):
    from app.tasks import generate_mail_draft_job_task
    generate_mail_draft_job_task.apply_async(args=[job_id], queue='mail', countdown=countdown)


def process_one(job_id):
    job = db.session.get(MailDraftJob, job_id)
    if not job:
        return
    with mailbox_scope(job.shop_id):
        lock = mail_client.acquire_lock('draft-job-'+job.id, ttl_ms=600_000)
        if not lock:
            return
        try:
            db.session.refresh(job)
            if job.status not in ACTIVE:
                return
            ids = [key for key in job.message_ids if key not in (job.results or {})]
            if not ids:
                job.status = 'completed'
                job.finished_at = datetime.utcnow()
                db.session.commit()
                return
            key = ids[0]
            job.status = 'running'
            job.heartbeat_at = datetime.utcnow()
            job.attempts += 1
            db.session.commit()
            result = {'state': 'skipped', 'error': ''}
            retry = False
            fatal = None
            if candidates().filter(MailMessage.id == key).first():
                try:
                    mail_support_service.generate(key, only_missing=True)
                    result['state'] = 'succeeded'
                except Exception as exc:
                    db.session.rollback()
                    db.session.refresh(job)
                    error = exc.message if isinstance(exc, ApiError) else '后台处理异常，请重试或联系管理员'
                    logger.warning('Mail draft job=%s message=%s attempt=%s error=%s',
                                   job.id, key, job.attempts, error)
                    result = {'state': 'failed', 'error': error[:500]}
                    retry = job.attempts < 3
                    if not candidates().filter(MailMessage.id == key).first():
                        result = {'state': 'skipped', 'error': ''}
                        retry = False
                    if 'Key' in error or '余额' in error or '允许把邮件' in error:
                        fatal = error[:500]
                        retry = False
            db.session.refresh(job)
            if fatal and job.status in ACTIVE:
                job.status = 'failed'
                job.error = fatal
            if not retry:
                job.results = {**(job.results or {}), key: result}
                job.attempts = 0
            job.heartbeat_at = datetime.utcnow()
            if job.status in ACTIVE and len(job.results) == len(job.message_ids):
                job.status = 'completed'
                job.finished_at = datetime.utcnow()
            db.session.commit()
            if job.status in ACTIVE:
                dispatch(job.id, countdown=5 if retry else 1)
        finally:
            mail_client.release_lock('draft-job-'+job.id, lock)


def recover():
    """Resumes already requested jobs only; does NOT discover or generate new mail."""
    stale = datetime.utcnow() - timedelta(minutes=11)
    for job in MailDraftJob.query.filter(MailDraftJob.status.in_(ACTIVE),
                                        MailDraftJob.heartbeat_at < stale).all():
        dispatch(job.id)
