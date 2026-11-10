"""Attachments remain authenticated, shop/message scoped, bounded and inert."""
import pytest
from datetime import datetime

from app import db
from app.models import MailMessage, MailSetting
from app.services import mail_attachment_service as service


@pytest.fixture()
def attachment_mail(app, shop_ids, monkeypatch):
    service._cache.clear()
    with app.app_context():
        db.session.add(MailSetting(key=f"s{shop_ids['a']}:account",value='123'))
        db.session.add(MailMessage(id='attachment-mail',shop_id=shop_ids['a'],provider_message_id='456',
            folder_id='789',direction='in',sent_at=1700000000000,has_attachment=True,
            status='ignored',ignored_at=datetime(2026,10,14)))
        db.session.commit()
    api = monkeypatch.setattr
    metadata = {'attachments':[{'attachmentId':'100','attachmentName':'../photo.png','attachmentSize':20},
                              {'attachmentId':'101','attachmentName':'report.pdf','attachmentSize':20}],
                'inline':[{'attachmentId':'102','attachmentName':'image.jpg','attachmentSize':20,'cid':'cid-1'}]}
    api(service.mail_client,'api',lambda *args,**kwargs:metadata)
    api(service.mail_client,'access_token',lambda:'test-secret')
    yield metadata
    service._cache.clear()


class BinaryResponse:
    status_code = 200
    def __init__(self, content):
        self.content = content
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return None
    def iter_content(self,chunk_size):
        yield self.content


def test_metadata_includes_inline_without_changing_ignored_status(app,client,admin_headers,attachment_mail):
    response = client.get('/api/mail/messages/attachment-mail/attachments?shop_id=1',headers=admin_headers)
    assert response.status_code == 200
    items = response.get_json()['data']
    assert len(items) == 3
    assert items[0]['name'] == 'photo.png' and items[0]['image']
    assert items[2]['inline']
    with app.app_context():
        assert db.session.get(MailMessage,'attachment-mail').status == 'ignored'


def test_attachment_auth_scope_and_message_ownership(client,admin_headers,limited_headers,attachment_mail,monkeypatch):
    path='/api/mail/messages/attachment-mail/attachments'
    assert client.get(path+'?shop_id=1').status_code == 401
    assert client.get(path+'?shop_id=1',headers=limited_headers).status_code == 403
    assert client.get(path+'?shop_id=2',headers=admin_headers).status_code == 404
    calls=[]
    monkeypatch.setattr(service.requests,'get',lambda *args,**kwargs:calls.append(args))
    assert client.get(path+'/999/download?shop_id=1',headers=admin_headers).status_code == 404
    assert client.get(path+'/invalid/download?shop_id=1',headers=admin_headers).status_code == 400
    assert calls == []


@pytest.mark.parametrize('content,mimetype',[(b'\x89PNG\r\n\x1a\nimage','image/png'),
    (b'<html><script>alert(1)</script></html>','application/octet-stream')])
def test_download_is_bounded_and_cannot_execute_html(client,admin_headers,attachment_mail,monkeypatch,content,mimetype):
    def download(url,**kwargs):
        assert url.endswith('/messages/456/attachments/100')
        assert kwargs['allow_redirects'] is False
        assert kwargs['headers']['Authorization'] == 'Zoho-oauthtoken test-secret'
        return BinaryResponse(content)
    monkeypatch.setattr(service.requests,'get',download)
    response=client.get('/api/mail/messages/attachment-mail/attachments/100/download?shop_id=1',headers=admin_headers)
    assert response.status_code == 200
    assert response.data == content and response.mimetype == mimetype
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Content-Disposition'].startswith('attachment;')
    monkeypatch.setattr(service,'MAX_BYTES',4)
    attachment_mail['attachments'][0]['attachmentSize']=0
    service._cache.clear()
    assert client.get('/api/mail/messages/attachment-mail/attachments/100/download?shop_id=1',headers=admin_headers).status_code == 400
