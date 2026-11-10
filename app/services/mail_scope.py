"""Explicit mailbox ownership shared by HTTP entry points and internal callers."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from app import db
from app.models.shop import Shop
from app.utils.errors import ForbiddenError, NotFoundError, ValidationError
from app.utils.security import can_access_shop, requested_shop_id

_shop = ContextVar("mailbox_shop", default=None)


def shop_id():
    value = _shop.get()
    if value is None:
        raise ValidationError("请先选择邮件所属店铺")
    return value


@contextmanager
def mailbox_scope(value):
    token = _shop.set(int(value))
    try:
        yield
    finally:
        _shop.reset(token)


def mailbox_request(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        value = requested_shop_id()
        if value is None or value <= 0:
            raise ValidationError("请先选择邮件所属店铺")
        if not can_access_shop(value):
            raise ForbiddenError("无权访问该店铺邮箱")
        if db.session.get(Shop, value) is None:
            raise NotFoundError("店铺不存在")
        with mailbox_scope(value):
            return function(*args, **kwargs)
    return wrapped
