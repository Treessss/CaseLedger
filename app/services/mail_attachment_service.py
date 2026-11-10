"""Shop-scoped Zoho attachment metadata and bounded binary downloads."""
from collections import OrderedDict
from io import BytesIO
import re
from threading import RLock
from time import monotonic

import requests

from app.services import mail_client, mail_service
from app.services.mail_scope import shop_id
from app.utils.errors import NotFoundError, ValidationError

MAX_BYTES = 25 * 1024 * 1024
_cache = OrderedDict()
_lock = RLock()


def _segment(value):
    value = str(value or '')
    if not re.fullmatch(r'[0-9]{1,80}', value):
        raise ValidationError('邮箱附件标识不完整，请重新同步邮件')
    return value


def _context(message_id):
    row = mail_service.require_message(message_id)
    account = mail_client.get_setting('account')
    if not account:
        raise ValidationError('请先连接当前店铺邮箱')
    path = f'accounts/{_segment(account)}/folders/{_segment(row.folder_id)}/messages/{_segment(row.provider_message_id)}'
    return path


def _filename(value):
    name = str(value or '附件').replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', '', name).strip()
    return name[:200] or '附件'


def list_attachments(message_id):
    path = _context(message_id)  # Always authorize before touching the cache.
    key = (shop_id(), mail_client.domain(), path)
    with _lock:
        cached = _cache.get(key)
        if cached and monotonic() - cached[0] < 120:
            return [dict(item) for item in cached[1]]
    data = mail_client.api(path+'/attachmentinfo', params={'includeInline':'true'}, timeout=10)
    if not isinstance(data, dict):
        raise ValidationError('邮箱返回的附件信息格式异常，请重试')
    result = []
    seen = set()
    for inline, field in ((False,'attachments'),(True,'inline')):
        entries = data.get(field) or []
        if not isinstance(entries, list):
            raise ValidationError('邮箱返回的附件清单格式异常')
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identifier = _segment(entry.get('attachmentId'))
            if identifier in seen:
                continue
            seen.add(identifier)
            name = _filename(entry.get('attachmentName'))
            try:
                size = max(int(entry.get('attachmentSize') or 0), 0)
            except (ValueError, TypeError):
                size = 0
            result.append({'id':identifier,'name':name,'size':size,'inline':inline,
                           'image':bool(re.search(r'\.(png|jpe?g|gif|webp)$',name,re.I))})
    with _lock:
        _cache[key] = (monotonic(), result)
        _cache.move_to_end(key)
        while len(_cache) > 256:
            _cache.popitem(last=False)
    return [dict(item) for item in result]


def _image_type(content):
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if content.startswith((b'GIF87a',b'GIF89a')):
        return 'image/gif'
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        return 'image/webp'
    return 'application/octet-stream'  # Never render HTML/SVG as same-origin content.


def download_attachment(message_id, attachment_id):
    path = _context(message_id)
    identifier = _segment(attachment_id)
    item = next((item for item in list_attachments(message_id) if item['id']==identifier), None)
    if item is None:
        raise NotFoundError('该附件不属于当前邮件')
    if item['size'] > MAX_BYTES:
        raise ValidationError('附件超过 25 MB，请到邮箱后台下载')
    try:
        with requests.get(f'https://mail.zoho.{mail_client.domain()}/api/{path}/attachments/{identifier}',
            headers={'Authorization':f'Zoho-oauthtoken {mail_client.access_token()}',
                     'Accept':'application/octet-stream'},timeout=25,stream=True,allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValidationError(f'附件读取失败（HTTP {response.status_code}），请检查邮箱权限或重试')
            content = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValidationError('附件超过 25 MB，请到邮箱后台下载')
    except requests.RequestException as exc:
        raise ValidationError('附件下载连接失败，请稍后重试') from exc
    if not content:
        raise ValidationError('邮箱返回了空附件，请重试')
    content = bytes(content)
    return BytesIO(content), item['name'], _image_type(content)
