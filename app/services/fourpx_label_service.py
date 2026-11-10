"""Read a bound 4PX PDF label and persist only its physical scan barcode."""

import json
from urllib.parse import urlparse

from app.models import ShippingParcel
from app.models.shop import ShopCredentialError
from app.services.fourpx_client import FourPxClient, FourPxError
from app.services.shipping_service import _check_label_unique, _commit_labels, _order_for_update
from app.utils.errors import NotFoundError, ValidationError


def _trusted_pdf_url(raw):
    if not isinstance(raw, str):
        raise ValidationError("4PX 尚未生成可打印的 PDF 面单")
    url = urlparse(raw.strip())
    host = (url.hostname or "").lower()
    if (url.scheme not in ("http", "https") or url.username or url.password or
            not (host.endswith(".4px.com") or host.endswith(".aliyuncs.com") or host == "bss-fss.i4px.com")):
        raise ValidationError("4PX 返回的面单地址不是可信下载域名，请人工核对")
    return raw.strip()


def fetch_label_data(app_key, app_secret, environment, tracking_number, *, client_factory=FourPxClient):
    """Network-only label fetch, safe to run off the Flask/SQLAlchemy thread."""
    try:
        data = client_factory(app_key, app_secret, environment=environment).get_label(tracking_number)
    except FourPxError as exc:
        raise ValidationError("4PX 未返回面单；请确认该店铺和物流产品已开通预生成标签能力") from exc
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise ValidationError("4PX 面单响应格式异常") from None
    if not isinstance(data, dict):
        raise ValidationError("4PX 面单响应格式异常")
    children = data.get("child_label_barcode") or []
    if children:
        raise ValidationError("4PX 返回子面单条码，请先人工核对多包裹映射，暂不自动打印")
    barcode = str(data.get("label_barcode") or "").strip()
    if not barcode or len(barcode) > 120 or any(char.isspace() for char in barcode):
        raise ValidationError("4PX 面单缺少有效的实体条码")
    url = _trusted_pdf_url((data.get("label_url_info") or {}).get("logistics_label"))
    return {"label_barcode": barcode, "pdf_url": url}


def get_parcel_label(order_id, parcel_id, *, client_factory=FourPxClient):
    order = _order_for_update(order_id)
    parcel = ShippingParcel.query.filter_by(id=parcel_id, order_id=order.id).one_or_none()
    if parcel is None:
        raise NotFoundError("包裹不存在")
    if parcel.source != "4px":
        raise ValidationError("仅支持已从 4PX 对单绑定的包裹获取面单")
    try:
        app_key, app_secret = order.shop.get_fourpx_credentials()
    except ShopCredentialError as exc:
        raise ValidationError(str(exc)) from exc
    label = fetch_label_data(app_key, app_secret, order.shop.fourpx_environment,
                             parcel.label_number, client_factory=client_factory)
    barcode = label["label_barcode"]
    # 建单时只存了运单号占位（或空），打印时用 4PX 返回的实体条码覆盖；
    # 只有已存过不同的真实条码才视为冲突。
    if (parcel.label_barcode and parcel.label_barcode != barcode
            and parcel.label_barcode != parcel.label_number):
        raise ValidationError("4PX 面单条码与此前获取的条码不一致，请人工核对后再打印")
    _check_label_unique(order, barcode, exclude_parcel_id=parcel.id)
    if not parcel.label_barcode or parcel.label_barcode == parcel.label_number:
        parcel.label_barcode = barcode
        _commit_labels()
    return {"order_id": order.id, "parcel_id": parcel.id, "tracking_number": parcel.label_number, **label}
