"""Merge today's currently printable 4PX labels into one original-size PDF."""

from datetime import datetime, time, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import re
from time import sleep
from zoneinfo import ZoneInfo

import requests
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from app import db
from app.models import AfterSale, Order, ShippingParcel
from app.models.shop import ShopCredentialError
from app.services.fourpx_client import FourPxClient, FourPxError
from app.services.fourpx_label_service import _trusted_pdf_url, fetch_label_data, get_parcel_label
from app.services.shipping_service import _check_label_unique, _commit_labels
from app.services.shipping_queue_service import _shop_id, ready_rows
from app.utils.errors import ApiError, ValidationError

MAX_LABEL_BYTES = 8 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_BATCH_PAGES = 1000


def _order_number_sort_key(value):
    """Print numeric order numbers ascending, with CF replacements after their original."""
    number = str(value or "").strip().removeprefix("#")
    match = re.fullmatch(r"(CF)?([0-9]+)", number, flags=re.IGNORECASE)
    if match:
        return (0, int(match.group(2)), bool(match.group(1)), number.casefold())
    return (1, number.casefold(), False, number.casefold())


def _download_pdf(url, *, http_get=requests.get):
    """Fetch only the already allowlisted 4PX URL, with no redirect escape."""
    url = _trusted_pdf_url(url)
    for attempt in range(2):
        try:
            response = http_get(url, timeout=25, stream=True, allow_redirects=False)
            with response:
                if response.status_code != 200:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                        sleep(0.5)
                        continue
                    raise ValidationError(f"4PX PDF 下载失败（HTTP {response.status_code}）")
                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > MAX_LABEL_BYTES:
                        raise ValidationError("4PX 单张面单超过大小限制")
                    chunks.append(chunk)
                break
        except requests.RequestException as exc:
            if attempt == 0:
                sleep(0.5)
                continue
            raise ValidationError("4PX PDF 下载失败，请稍后重试") from exc
    content = b"".join(chunks)
    if not content.startswith(b"%PDF-"):
        raise ValidationError("4PX 返回的文件不是 PDF")
    return content


def _single_pdf(job, credentials, environment, download_pdf):
    key, secret = credentials
    label = job.get("prefetched_label") or fetch_label_data(key, secret, environment, job["tracking"])
    placeholder = (job.get("source") == "4px" and job["barcode"] == job["tracking"])
    if job["barcode"] and job["barcode"] != label["label_barcode"] and not placeholder:
        raise ValidationError("4PX 面单条码与此前记录不一致")
    return download_pdf(label["pdf_url"]), label["label_barcode"]


def _product_code(job, credentials, environment):
    key, secret = credentials
    data = FourPxClient(key, secret, environment=environment).get_order(job["tracking"])
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return None
    info = data[0].get("consignment_info") or {}
    if str(info.get("4px_tracking_no") or "").strip() != job["tracking"]:
        return None
    return str(info.get("logistics_product_code") or "").strip() or None


def _product_codes_by_day(jobs, credentials, environment):
    """Resolve product codes by binding day; query the prior day only for misses."""
    dates = set()
    for job in jobs:
        created = job.get("created_at")
        if created:
            utc_created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
            day = utc_created.astimezone(ZoneInfo("Asia/Shanghai")).date()
            dates.add(day)
    if not dates or len(dates) > 7:
        return {}
    wanted = {job["tracking"] for job in jobs}
    key, secret = credentials
    client = FourPxClient(key, secret, environment=environment)
    result = {}
    for candidate_days in (dates, {day - timedelta(days=1) for day in dates}):
        for day in sorted(candidate_days):
            if len(result) == len(wanted):
                return result
            start = datetime.combine(day, time.min, ZoneInfo("Asia/Shanghai"))
            end = start + timedelta(days=1)
            try:
                rows = client.get_predeclared_created_between(int(start.timestamp() * 1000),
                                                              int(end.timestamp() * 1000))
            except FourPxError:
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                info = row.get("consignment_info") if isinstance(row, dict) else None
                if isinstance(info, dict):
                    tracking = str(info.get("4px_tracking_no") or "").strip()
                    code = str(info.get("logistics_product_code") or "").strip()
                    if tracking in wanted and code:
                        result[tracking] = code
    return result


def _batch_pdf(jobs, product_code, credentials, environment, download_pdf):
    key, secret = credentials
    url = FourPxClient(key, secret, environment=environment).get_labels_pdf(
        [job["tracking"] for job in jobs], product_code
    )
    if isinstance(url, dict):
        url = url.get("logistics_label")
    content = download_pdf(_trusted_pdf_url(url))
    reader = PdfReader(BytesIO(content), strict=False)
    if reader.is_encrypted or len(reader.pages) != len(jobs):
        raise ValidationError("4PX 批量面单页数与包裹数不符")
    for job, page in zip(jobs, reader.pages):
        printed = page.extract_text() or ""
        # Some 4PX products print only the physical barcode, not the tracking number.
        # The barcode is unique per shop and is the value warehouse staff scan.
        if job["barcode"] not in printed:
            raise ValidationError("4PX 批量面单顺序或条码不匹配")
    return content


def _fetch_optimized(jobs, credentials, environment, download_pdf):
    """Fetch missing barcodes concurrently, then use 4PX's 30-label PDF API."""
    completed = []
    fourpx_jobs = [job for job in jobs if job.get("source") == "4px"]
    bulk_codes = _product_codes_by_day(fourpx_jobs, credentials, environment) if fourpx_jobs else {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        key, secret = credentials
        label_futures = {job["index"]: pool.submit(fetch_label_data, key, secret, environment,
                                                     job["tracking"])
                         for job in fourpx_jobs if not job["barcode"] and job["tracking"] in bulk_codes}
        for job in fourpx_jobs:
            future = label_futures.get(job["index"])
            if future:
                try:
                    job["prefetched_label"] = future.result()
                    job["barcode"] = job["prefetched_label"]["label_barcode"]
                except (ApiError, FourPxError, ValueError, TypeError, KeyError):
                    # A failed optimization still gets the original single-label path.
                    pass
        saved_jobs = [job for job in fourpx_jobs if job["barcode"]]
        code_futures = {job["index"]: pool.submit(_product_code, job, credentials, environment)
                        for job in saved_jobs if job["tracking"] not in bulk_codes}
        by_code = {}
        single_jobs = [job for job in jobs if not job["barcode"] or job.get("source") != "4px"]
        for job in jobs:
            if not job["barcode"] or job.get("source") != "4px":
                continue
            code = bulk_codes.get(job["tracking"])
            if not code:
                try:
                    code = code_futures[job["index"]].result()
                except (FourPxError, ValueError, TypeError):
                    code = None
            if code:
                by_code.setdefault(code, []).append(job)
            else:
                single_jobs.append(job)
        chunks = []
        for code, group in by_code.items():
            for start in range(0, len(group), 30):
                chunk = group[start:start + 30]
                if len(chunk) > 1:
                    chunks.append((code, chunk))
                else:
                    single_jobs.extend(chunk)
        single_futures = {job["index"]: pool.submit(_single_pdf, job, credentials, environment, download_pdf)
                          for job in single_jobs}
        batch_futures = [(code, chunk, pool.submit(_batch_pdf, chunk, code, credentials, environment, download_pdf))
                         for code, chunk in chunks]
        for job in single_jobs:
            try:
                content, barcode = single_futures[job["index"]].result()
            except (ApiError, FourPxError, PdfReadError, ValueError, TypeError) as exc:
                raise ValidationError(f"订单 {job['order_number']} 的面单处理失败：{exc}") from exc
            completed.append((job["index"], [job], content, [barcode]))
        for code, chunk, future in batch_futures:
            try:
                content = future.result()
            except (ApiError, FourPxError, PdfReadError, ValueError, TypeError):
                # Some 4PX products do not support the bulk endpoint; retain complete output.
                fallback_futures = {job['index']: pool.submit(
                    _single_pdf, job, credentials, environment, download_pdf,
                ) for job in chunk}
                for job in chunk:
                    try:
                        single, barcode = fallback_futures[job['index']].result()
                    except (ApiError, FourPxError, PdfReadError, ValueError, TypeError) as exc:
                        raise ValidationError(f"订单 {job['order_number']} 的面单处理失败：{exc}") from exc
                    completed.append((job["index"], [job], single, [barcode]))
            else:
                completed.append((chunk[0]["index"], chunk, content, [job["barcode"] for job in chunk]))
    return sorted(completed, key=lambda item: item[0])


def build_ready_labels_pdf(raw_shop_id, *, download_pdf=_download_pdf, label_fetch=None):
    """Only fully stocked, fully labelled, completely unscanned orders are included."""
    shop_id = _shop_id(raw_shop_id)
    from app.services.inventory_pool_service import build_pool_plan
    pool = build_pool_plan(shop_id, respect_procurement_exclusion=False)
    eligible = [row for row in ready_rows(shop_id, pool=pool) if row['label_state'] == 'ready_to_scan']
    printable = [row for row in eligible if row['id'] in pool['suggested_ready_order_ids']]
    ready_sale_ids = pool['suggested_cf_ready_ids']
    after_sales = AfterSale.query.filter(
        AfterSale.shop_id == shop_id, AfterSale.id.in_(ready_sale_ids), AfterSale.deleted_at.is_(None),
        AfterSale.tracking_number.isnot(None),
    ).order_by(AfterSale.created_at, AfterSale.id).all()
    if not printable and not after_sales:
        raise ValidationError("当前店铺没有面单齐全且待扫码的可发货订单")
    order_ids = [row["id"] for row in printable]
    orders = Order.query.filter(Order.id.in_(order_ids)).all()
    by_id = {order.id: order for order in orders}
    parcels = ShippingParcel.query.filter(ShippingParcel.order_id.in_(order_ids)).order_by(
        ShippingParcel.order_id, ShippingParcel.id
    ).all()
    by_order = {}
    for parcel in parcels:
        by_order.setdefault(parcel.order_id, []).append(parcel)
    count = sum(len(by_order.get(order_id, [])) for order_id in order_ids) + len(after_sales)
    if count > MAX_BATCH_PAGES:
        raise ValidationError("待打印面单超过 1000 张，请联系管理员分批处理")
    for row in printable:
        order_parcels = by_order.get(row["id"], [])
        if len(order_parcels) != row["expected_parcel_count"] or any(
            parcel.scanned_at for parcel in order_parcels
        ):
            raise ValidationError(f"订单 {row['order_number']} 面单不齐或已扫码，批量下载已中止")

    writer = PdfWriter()
    bytes_total = 0
    page_total = 0
    jobs = [{"order_id": row["id"], "order_number": row["order_number"],
             "parcel": parcel, "tracking": parcel.label_number, "barcode": parcel.label_barcode,
             "source": parcel.source, "created_at": parcel.created_at}
            for row in printable for parcel in by_order[row["id"]]]
    for record in after_sales:
        jobs.append({"order_id": record.order_id,
                     "order_number": record.customer_reference, "parcel": None,
                     "after_sale_id": record.id, "tracking": record.tracking_number,
                     "barcode": record.label_barcode, "source": "4px", "created_at": record.created_at})
    jobs.sort(key=lambda job: _order_number_sort_key(job["order_number"]))
    for index, job in enumerate(jobs):
        job["index"] = index
    if label_fetch is None:
        try:
            shop = by_id[printable[0]["id"]].shop if printable else after_sales[0].shop
            credentials = shop.get_fourpx_credentials()
        except ShopCredentialError as exc:
            raise ValidationError(str(exc)) from exc
        environment = shop.fourpx_environment
        groups = _fetch_optimized(jobs, credentials, environment, download_pdf)
    else:
        groups = []
        for job in jobs:
            label = label_fetch(job["order_id"], job["parcel"].id if job["parcel"] else None)
            groups.append((job["index"], [job], download_pdf(label["pdf_url"]),
                           [label.get("label_barcode")]))
    pages_by_job = {}
    for _, group_jobs, content, barcodes in groups:
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted or not reader.pages:
                raise ValidationError("4PX 面单为空或已加密")
            if len(reader.pages) > 10 * len(group_jobs):
                raise ValidationError("单个包裹包含过多 PDF 页面，请人工检查")
            if len(group_jobs) > 1 and len(reader.pages) != len(group_jobs):
                raise ValidationError("4PX 批量面单页数与包裹数不符")
            bytes_total += len(content)
            page_total += len(reader.pages)
            if bytes_total > MAX_BATCH_BYTES or page_total > MAX_BATCH_PAGES:
                raise ValidationError("合并后的面单超过大小或页数限制，请联系管理员分批处理")
            if len(group_jobs) == 1:
                pages_by_job[group_jobs[0]["index"]] = list(reader.pages)
            else:
                for job, page in zip(group_jobs, reader.pages):
                    pages_by_job[job["index"]] = [page]
            for job, barcode in zip(group_jobs, barcodes):
                parcel = job["parcel"]
                if job.get("after_sale_id"):
                    record = db.session.get(AfterSale, job["after_sale_id"])
                    if barcode and record and not record.label_barcode:
                        if ShippingParcel.query.filter_by(label_barcode=barcode).first() or AfterSale.query.filter(
                            AfterSale.label_barcode == barcode, AfterSale.id != record.id,
                        ).first():
                            raise ValidationError("售后面单条码已被其他包裹占用")
                        record.label_barcode = barcode
                        _commit_labels()
                elif barcode and (not parcel.label_barcode
                                  or (parcel.source == "4px" and parcel.label_barcode == parcel.label_number)):
                    _check_label_unique(by_id[job["order_id"]], barcode, exclude_parcel_id=parcel.id)
                    parcel.label_barcode = barcode
                    _commit_labels()
        except ApiError as exc:
            raise ValidationError(f"订单 {group_jobs[0]['order_number']} 的面单处理失败：{exc.message}") from exc
        except (PdfReadError, ValueError, TypeError, KeyError) as exc:
            raise ValidationError(f"订单 {group_jobs[0]['order_number']} 的 4PX PDF 无法合并，请单独检查") from exc

    if len(pages_by_job) != len(jobs):
        raise ValidationError("4PX 面单未完整覆盖待打印订单，批量下载已中止")
    for index in range(len(jobs)):
        for page in pages_by_job[index]:
            writer.add_page(page)

    # A scan can occur while remote PDFs download. Never hand out a batch whose
    # orders have ceased to be fully ready before the download finishes.
    final_pool = build_pool_plan(shop_id, respect_procurement_exclusion=False)
    still_ready = {row["id"] for row in ready_rows(shop_id, pool=final_pool) if row["label_state"] == "ready_to_scan"}
    if not set(order_ids).issubset(still_ready):
        raise ValidationError("下载期间发货队列发生变化，请刷新后重新生成面单")
    if not set(order_ids).issubset(final_pool['suggested_ready_order_ids']):
        raise ValidationError('下载期间库存数量发生变化，本批次货量不足，请刷新后重新下载')
    still_ready_after_sales = final_pool['suggested_cf_ready_ids']
    if not {record.id for record in after_sales}.issubset(still_ready_after_sales):
        raise ValidationError("下载期间售后发货队列发生变化，请刷新后重新生成面单")
    output = BytesIO()
    writer.write(output)
    day = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    return output.getvalue(), f"4px-ready-labels-{day}-shop-{shop_id}.pdf"
