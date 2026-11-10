"""Offline contract tests; no merchant credentials or network calls."""

import json

import pytest
import requests

from app.services.fourpx_client import FourPxClient, FourPxError, compact_json, sign_request


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.payload, Exception):
            raise self.payload
        return FakeResponse(self.payload)


def test_signature_matches_4px_public_example():
    params = {
        "method": "ds.xms.order.create",
        "app_key": "16081f05-e8fc-4250-b9c4-0660d1ecbb28",
        "v": "1.0",
        "timestamp": "1532592413187",
        "format": "json",
    }
    secret = "7eebf328-8e5a-4030-904d-ec6e89174fbc"
    assert sign_request(params, '{"aa":"bb"}', secret) == "ff4af77c062a9b97d98aa29777621c4a"
    assert sign_request({**params, "access_token": "ignored", "language": "en"}, '{"aa":"bb"}', secret) == (
        "ff4af77c062a9b97d98aa29777621c4a"
    )


def test_posts_same_compact_body_that_was_signed():
    session = FakeSession({"result": "1", "data": {"4px_tracking_no": "TEST123"}})
    client = FourPxClient("test-key", "test-secret", session=session)
    result = client.create_order({"ref_no": "CL-1", "recipient_info": {"city": "香港"}})
    assert result == {"4px_tracking_no": "TEST123"}
    url, options = session.calls[0]
    assert url == "https://open-test.4px.com/router/api/service"
    assert options["headers"] == {"Content-Type": "application/json"}
    body = options["data"].decode("utf-8")
    assert body == compact_json({"ref_no": "CL-1", "recipient_info": {"city": "香港"}})
    assert json.loads(body)["recipient_info"]["city"] == "香港"
    params = options["params"]
    assert params["v"] == "1.1.0"
    assert params["sign"] == sign_request(params, body, "test-secret")


def test_read_methods_send_request_number_and_no_mutating_retry():
    session = FakeSession({"result": 1, "data": []})
    client = FourPxClient("test-key", "test-secret", session=session)
    assert client.get_order("CL-1") == []
    assert client.get_label("CL-1") == []
    assert [call[1]["params"]["method"] for call in session.calls] == [
        "ds.xms.order.get", "ds.xms.label.get"
    ]
    assert [call[1]["data"] for call in session.calls] == [
        b'{"request_no":"CL-1"}',
        b'{"request_no":"CL-1","response_label_format":"PDF"}',
    ]


def test_ambiguous_transport_failure_is_not_retried_or_leaked():
    session = FakeSession(requests.Timeout("secret signed URL and customer address"))
    client = FourPxClient("test-key", "test-secret", session=session)
    with pytest.raises(FourPxError, match="核对平台状态") as error:
        client.create_order({"ref_no": "CL-1"})
    assert len(session.calls) == 1
    assert "secret" not in str(error.value)


def test_business_failure_is_safe_and_partial_success_is_not_accepted():
    session = FakeSession({"result": "2", "msg": "customer name and credentials"})
    client = FourPxClient("test-key", "test-secret", session=session)
    with pytest.raises(FourPxError, match="不要直接重复预报") as error:
        client.create_order({"ref_no": "CL-1"})
    assert "customer" not in str(error.value)


def test_method_and_environment_are_whitelisted():
    with pytest.raises(ValueError):
        FourPxClient("key", "secret", environment="https://example.com")
    client = FourPxClient("key", "secret", session=FakeSession({"result": 1, "data": []}))
    with pytest.raises(ValueError):
        client.call("not-a-method", {})
    with pytest.raises(ValueError):
        client.create_order({})


def test_official_tracking_uses_correct_method_version_language_and_timeout():
    session = FakeSession({'result': '1', 'data': {'deliveryOrderNo': '4PXTESTCN'}})
    client = FourPxClient('test-key', 'test-secret', session=session)
    assert client.get_tracking('4PXTESTCN') == {'deliveryOrderNo': '4PXTESTCN'}
    _, options = session.calls[0]
    assert options['params']['method'] == 'tr.order.tracking.get'
    assert options['params']['v'] == '1.0.0'
    assert options['params']['language'] == 'en'
    assert options['data'] == b'{"deliveryOrderNo":"4PXTESTCN"}'
    assert options['timeout'] == 12
    assert options['params']['sign'] == sign_request(options['params'], options['data'].decode(), 'test-secret')
    with pytest.raises(ValueError):
        client.get_tracking('https://untrusted.example/track')
    assert len(session.calls) == 1


def test_business_error_carries_4px_detail_message():
    from app.services.fourpx_client import FourPxError

    session = FakeSession({"result": "0", "msg": "System processing failed",
                           "errors": [{"error_code": "010113005",
                                       "error_msg": "收件人地址:收件人地址必须包含数字和英文字母"}]})
    client = FourPxClient("test-key", "test-secret", session=session)
    with pytest.raises(FourPxError) as raised:
        client.create_order({"ref_no": "CL-ERR"})
    assert "4PX 未确认操作成功" in str(raised.value)
    assert "收件人地址必须包含数字和英文字母" in raised.value.details
