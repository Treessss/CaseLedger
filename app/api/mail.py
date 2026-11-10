"""客服邮件接口。

只做参数解析与响应组装，业务判断全在 app/services/mail_service.py。
"""

from flask import request, send_file

from app.api import bp
from app.services import mail_client, mail_service
from app.services import mail_attachment_service, mail_support_service
from app.services import mail_draft_job_service
from app.services.mail_scope import mailbox_request
from app.utils.responses import ok
from app.utils.security import require_admin


@bp.route('/mail/draft-jobs', methods=['GET', 'POST'])
@mailbox_request
def mail_draft_jobs():
    if request.method == 'POST':
        return ok(mail_draft_job_service.enqueue(request.get_json(silent=True) or {}))
    return ok(mail_draft_job_service.latest())


@bp.route('/mail/draft-jobs/<job_id>/cancel', methods=['POST'])
@mailbox_request
def mail_cancel_draft_job(job_id):
    return ok(mail_draft_job_service.cancel(job_id))


@bp.route("/mail/status", methods=["GET"])
@mailbox_request
def mail_status():
    return ok(mail_client.status())


@bp.route("/mail/settings", methods=["POST"])
@require_admin
@mailbox_request
def mail_settings():
    return ok(
        mail_service.save_settings(request.get_json(silent=True) or {}),
        message="设置已保存",
    )


@bp.route("/mail/connect", methods=["POST"])
@require_admin
@mailbox_request
def mail_connect():
    return ok(
        mail_client.connect_account(request.get_json(silent=True) or {}),
        message="邮箱已连接",
    )


@bp.route("/mail/sync", methods=["POST"])
@mailbox_request
def mail_sync():
    payload = request.get_json(silent=True) or {}
    return ok(mail_service.sync(history=bool(payload.get("history"))), message="同步完成")


@bp.route("/mail/verify", methods=["POST"])
@mailbox_request
def mail_verify():
    return ok(mail_service.verify_batch())


@bp.route("/mail/messages", methods=["GET"])
@mailbox_request
def mail_messages():
    return ok(mail_service.list_messages(request.args))


@bp.route("/mail/messages/<message_id>", methods=["GET"])
@mailbox_request
def mail_message_detail(message_id):
    return ok(mail_service.get_message(message_id))


@bp.route("/mail/messages/<message_id>/attachments", methods=["GET"])
@mailbox_request
def mail_message_attachments(message_id):
    return ok(mail_attachment_service.list_attachments(message_id))


@bp.route("/mail/messages/<message_id>/attachments/<attachment_id>/download", methods=["GET"])
@mailbox_request
def mail_attachment_download(message_id, attachment_id):
    content, name, mimetype = mail_attachment_service.download_attachment(message_id, attachment_id)
    response = send_file(content, download_name=name, mimetype=mimetype, as_attachment=True, max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


@bp.route("/mail/messages/<message_id>/guidance", methods=["POST"])
@mailbox_request
def mail_message_guidance(message_id):
    payload = request.get_json(silent=True) or {}
    return ok(
        mail_service.save_guidance(message_id, payload.get("guidance")),
        message="回复关键点已保存",
    )


@bp.route("/mail/messages/<message_id>/hydrate-thread", methods=["POST"])
@mailbox_request
def mail_message_hydrate_thread(message_id):
    payload = request.get_json(silent=True) or {}
    return ok(
        mail_service.hydrate_thread(message_id, payload.get("limit") or 6),
        message="已加载完整往来",
    )


@bp.route("/mail/messages/<message_id>/draft", methods=["POST"])
@mailbox_request
def mail_message_draft(message_id):
    payload = request.get_json(silent=True) or {}
    return ok(
        mail_service.generate_draft(message_id, payload.get("guidance"), payload.get('order_id'), payload.get('facts')),
        message="智能分析与回复草稿已生成，未发送",
    )


@bp.route('/mail/messages/<message_id>/exchange-discount', methods=['POST'])
@mailbox_request
def mail_exchange_discount(message_id):
    return ok(mail_support_service.confirm_discount(message_id, request.get_json(silent=True) or {}),
              message='优惠已确认并更新草稿，未发送邮件')


@bp.route("/mail/messages/<message_id>/reply", methods=["POST"])
@mailbox_request
def mail_message_reply(message_id):
    payload = request.get_json(silent=True) or {}
    return ok(
        mail_service.save_reply(message_id, payload.get("reply")),
        message="回复已保存",
    )


@bp.route("/mail/messages/<message_id>/send", methods=["POST"])
@mailbox_request
def mail_message_send(message_id):
    return ok(mail_service.send_reply(message_id), message="回复已发送")


@bp.route("/mail/messages/<message_id>/send-with-attachments", methods=["POST"])
@mailbox_request
def mail_message_send_with_attachments(message_id):
    reply = request.form.get("reply", "")
    files = [
        (upload.filename or "", upload.read(), upload.mimetype or "")
        for upload in request.files.getlist("files")
    ]
    return ok(
        mail_service.send_reply_with_attachments(message_id, reply, files),
        message="回复已发送",
    )


@bp.route("/mail/messages/<message_id>/ignore", methods=["POST"])
@mailbox_request
def mail_message_ignore(message_id):
    payload = request.get_json(silent=True) or {}
    return ok(mail_service.ignore_message(message_id, payload.get("ignored", True)))


@bp.route("/mail/translate", methods=["POST"])
@mailbox_request
def mail_translate():
    payload = request.get_json(silent=True) or {}
    return ok(
        mail_service.translate(
            str(payload.get("id") or ""),
            str(payload.get("kind") or "mail"),
            payload.get("reply"),
        )
    )
