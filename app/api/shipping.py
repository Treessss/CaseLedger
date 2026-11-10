"""Shipping work queue; domain decisions stay in the service layer."""

from io import BytesIO

from flask import request, send_file

from app.api import bp
from app.services import packing_service, shipping_batch_pdf_service, shipping_queue_service
from app.services import fourpx_declaration_service
from app.services import fourpx_preview_service
from app.services import packing_session_service, outbound_scan_failure_service
from app.utils.responses import ok


@bp.route("/shipping/packing/start", methods=["POST"])
def begin_packing():
    return ok(packing_session_service.begin_packing(request.get_json(silent=True) or {}))


@bp.route("/shipping/packing/cancel", methods=["POST"])
def cancel_packing():
    return ok(packing_session_service.cancel_packing(request.get_json(silent=True) or {}))


@bp.route("/shipping/packing/priority", methods=["POST"])
def packing_priority():
    return ok(packing_session_service.set_priority(request.get_json(silent=True) or {}))


@bp.route("/shipping/ready", methods=["GET"])
def list_ready_orders():
    return ok(shipping_queue_service.list_ready_orders(request.args))


@bp.route("/shipping/packing-lookup", methods=["GET"])
def lookup_packing_list():
    return ok(packing_service.lookup_packing_list(request.args))


@bp.route("/shipping/ready-labels/pdf", methods=["GET"])
def download_ready_labels_pdf():
    content, filename = shipping_batch_pdf_service.build_ready_labels_pdf(request.args.get("shop_id"))
    return send_file(BytesIO(content), mimetype="application/pdf", as_attachment=True, download_name=filename)


@bp.route("/shipping/scan", methods=["POST"])
def scan_shipping_label():
    return ok(shipping_queue_service.scan_by_label(request.get_json(silent=True) or {}),
              message="包裹扫码成功")


@bp.route("/shipping/outbound/recent", methods=["GET"])
def list_recent_outbound():
    return ok(shipping_queue_service.list_recent_outbound(request.args))


@bp.route("/shipping/outbound/failures", methods=["GET"])
def list_outbound_failures():
    return ok(outbound_scan_failure_service.list_failures(request.args))


@bp.route("/shipping/outbound/undo", methods=["POST"])
def undo_outbound():
    return ok(shipping_queue_service.undo_outbound(request.get_json(silent=True) or {}),
              message="已撤回出库")


@bp.route("/shipping/4px/preview", methods=["POST"])
def preview_fourpx_predeclared():
    return ok(fourpx_preview_service.preview_predeclared(request.get_json(silent=True) or {}))


@bp.route("/shipping/4px/bind", methods=["POST"])
def bind_fourpx_predeclared():
    return ok(fourpx_preview_service.bind_predeclared(request.get_json(silent=True) or {}),
              message="4PX 单号已绑定订单，尚未回填 Shopify 或出库")


@bp.route("/shipping/4px/bind-and-writeback", methods=["POST"])
def bind_and_writeback_fourpx_predeclared():
    return ok(fourpx_preview_service.bind_and_writeback(request.get_json(silent=True) or {}),
              message="4PX 对单已保存；请核对 Shopify 回填状态，仓库仍需扫码出库")


@bp.route("/shipping/4px/batch-bind-and-writeback", methods=["POST"])
def batch_bind_and_writeback_fourpx_predeclared():
    return ok(fourpx_preview_service.batch_bind_and_writeback(request.get_json(silent=True) or {}),
              message="批量对单完成；请逐项核对回填与异常结果，仓库仍需扫码出库")


@bp.route("/shipping/4px/batch-bind", methods=["POST"])
def batch_bind_fourpx_predeclared():
    return ok(fourpx_preview_service.batch_bind_predeclared(request.get_json(silent=True) or {}),
              message="4PX 对单已绑定；面单齐全的订单可逐单回填 Shopify，仓库仍需扫码出库")


@bp.route("/shipping/4px/declaration/check", methods=["GET"])
def check_fourpx_declaration():
    """只读检查：按业务规则给出待出库订单的预申报产品/重量/申报价值，不调用 4PX。"""
    return ok(fourpx_declaration_service.check(request.args))


@bp.route("/shipping/4px/declaration/check.csv", methods=["GET"])
def export_fourpx_declaration():
    """导出预申报检查结果 CSV；只读，不调用 4PX。"""
    content, filename = fourpx_declaration_service.export_csv(request.args)
    return send_file(BytesIO(content), mimetype="text/csv", as_attachment=True, download_name=filename)


@bp.route("/shipping/4px/declaration/plan", methods=["POST"])
def plan_fourpx_declaration():
    """批量预申报计划：按产品汇总待下单清单，不创建 4PX 委托单。"""
    return ok(fourpx_declaration_service.plan_batch(request.get_json(silent=True) or {}))


@bp.route("/shipping/4px/declaration/settings", methods=["GET"])
def get_fourpx_declaration_settings():
    """预申报店铺开关（建单后是否自动回填 Shopify）。"""
    return ok(fourpx_declaration_service.get_settings(request.args))


@bp.route("/shipping/4px/declaration/settings", methods=["POST"])
def save_fourpx_declaration_settings():
    """保存预申报店铺开关；默认关闭。"""
    return ok(fourpx_declaration_service.save_settings(request.get_json(silent=True) or {}))


@bp.route("/shipping/4px/freight/sync", methods=["POST"])
def sync_fourpx_freight():
    """同步 4PX 实际出账费用（计费重 + 明细 + 总费用）。"""
    return ok(fourpx_freight_service.sync_order_freights(request.get_json(silent=True) or {}),
              message="4PX 费用同步完成")


@bp.route("/shipping/4px/declaration/sync", methods=["POST"])
def sync_fourpx_manual_declarations():
    """把 4PX 上已人工建好的委托单同步成本地包裹（不取面单、不标记已打印）。"""
    return ok(fourpx_declaration_service.sync_manual_declarations(request.get_json(silent=True) or {}),
              message="已同步 4PX 已有委托单")


@bp.route("/shipping/4px/declaration/jobs", methods=["POST"])
def start_fourpx_declaration_job():
    """创建异步预申报任务（后台按 50 一批建单，可轮询进度）。"""
    return ok(fourpx_declaration_service.start_declaration_job(request.get_json(silent=True) or {}),
              message="预申报任务已开始")


@bp.route("/shipping/4px/declaration/jobs", methods=["GET"])
def list_fourpx_declaration_jobs():
    """当前店铺最近 5 个预申报任务，用于刷新后恢复进度。"""
    return ok(fourpx_declaration_service.list_declaration_jobs(request.args))


@bp.route("/shipping/4px/declaration/jobs/<job_id>", methods=["GET"])
def get_fourpx_declaration_job(job_id):
    """查询预申报任务进度/结果。"""
    args = dict(request.args)
    args["job_id"] = job_id
    return ok(fourpx_declaration_service.get_declaration_job(args))


@bp.route("/shipping/4px/declaration/create", methods=["POST"])
def create_fourpx_declarations():
    """真实向 4PX 批量建单（必须 confirm=true，页面会二次确认）。"""
    return ok(fourpx_declaration_service.create_declarations(request.get_json(silent=True) or {}),
              message="已完成 4PX 预申报建单")
