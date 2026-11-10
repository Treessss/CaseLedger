import { BarcodeOutlined, FilePdfOutlined, ReloadOutlined } from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Alert, Button, Card, DatePicker, Grid, Input, message, Modal, Popconfirm, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import type { InputRef } from 'antd';
import React, { useEffect, useRef, useState } from 'react';
import { bindAfterSaleFourPx, fetchAfterSaleLabel, previewAfterSaleFourPx, previewCfShopify, writebackCfShopify, type CfFourPxRow, type CfShopifyPreview } from '@/services/afterSales';
import type { ReadyAfterSale } from '@/services/shipping';
import { batchBindFourPxPredeclared, bindFourPxPredeclared, downloadReadyLabelsPdf, listReadyOrders, previewFourPxPredeclared, scanShippingLabel, type FourPxBatchOrderResult, type FourPxPreview, type FourPxPreviewRow, type ReadyOrder, type ReadySummary } from '@/services/shipping';
import { writebackShopifyTracking } from '@/services/orders';
import { runConcurrentBatches } from '@/utils/concurrentBatches';
import { getErrorMessage } from '@/utils/error';
import { persistedPagination } from '@/utils/pagination';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';
import { formatBeijingTime, getCurrentBeijingTime } from '@/utils/timezone';
import { ShopRequiredNotice } from '@/components';
import { canSelectCf, cfPairKey, executeCfBatch, prepareCfBatch, type CfBatchEntry, type CfBatchMode } from '@/services/afterSales/batch';

const fourPxStatusText: Record<string, string> = {
  ready_to_bind: '可绑定', already_bound: '已绑定', duplicate_4px: '4PX 重复', ambiguous_order: '本地订单重复',
  order_not_found: '本地无订单', missing_number: '缺少单号', ineligible_order: '订单不可发货',
  label_conflict: '单号已绑定别单', not_pending: '仓库已出库', not_predeclared: '非预申报状态',
  parcel_count_mismatch: '包裹数不符', invalid_response: '返回格式异常', pair_not_unique: '对单结果不存在或重复',
  shopify_already_fulfilled: 'Shopify 已回填', shopify_tracking_conflict: 'Shopify 单号冲突',
};
type SelectableFourPxRow = FourPxPreviewRow & {
  order_id: number;
  customer_reference: string;
  tracking_number: string;
};

const Shipping: React.FC = () => {
  const screens = Grid.useBreakpoint();
  const selectedShop = getSelectedShopId();
  const shopId = selectedShop === ALL_SHOPS ? null : Number(selectedShop);
  const actionRef = useRef<ActionType>(null);
  const scanRef = useRef<InputRef>(null);
  const [scanNumber, setScanNumber] = useState('');
  const [scanning, setScanning] = useState(false);
  const [downloadingLabels, setDownloadingLabels] = useState(false);
  const [previewDate, setPreviewDate] = useState<string>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [bindingTracking, setBindingTracking] = useState<string>();
  const [preview, setPreview] = useState<FourPxPreview>();
  const [afterSalePreview, setAfterSalePreview] = useState<CfFourPxRow[]>([]);
  const [cfPreview, setCfPreview] = useState<CfShopifyPreview | null>(null);
  const [cfFulfillmentId, setCfFulfillmentId] = useState<string>();
  const [cfConfirmedDate, setCfConfirmedDate] = useState<string>();
  const [afterSaleDate, setAfterSaleDate] = useState<string>();
  const [afterSaleBusy, setAfterSaleBusy] = useState(false);
  const [selectedCfKeys, setSelectedCfKeys] = useState<string[]>([]);
  const [cfBatch, setCfBatch] = useState<{ mode: CfBatchMode; date: string; entries: CfBatchEntry[]; finished: boolean } | null>(null);
  const cfBatchLock = useRef(false);
  const cfMounted = useRef(true);
  useEffect(() => { cfMounted.current = true; return () => { cfMounted.current = false; }; }, []);
  const cfContextCurrent = () => cfMounted.current && Number(getSelectedShopId()) === shopId;
  useEffect(() => {
    setAfterSalePreview([]); setSelectedCfKeys([]); setCfBatch(null); setCfPreview(null);
  }, [shopId]);
  const [readyAfterSales, setReadyAfterSales] = useState<ReadyAfterSale[]>([]);
  const [selectedPairKeys, setSelectedPairKeys] = useState<string[]>([]);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchProgress, setBatchProgress] = useState('');
  const [batchResults, setBatchResults] = useState<FourPxBatchOrderResult[]>([]);
  const [batchSkipped, setBatchSkipped] = useState<string[]>([]);
  const [batchError, setBatchError] = useState('');
  const [batchResultOpen, setBatchResultOpen] = useState(false);
  const [summary, setSummary] = useState<ReadySummary>({
    ready_orders: 0, awaiting_labels: 0, ready_to_scan: 0, partially_scanned: 0, after_sale_ready: 0,
  });

  const scan = async () => {
    if (!shopId || !scanNumber.trim() || scanning) return;
    setScanning(true);
    try {
      const response = await scanShippingLabel(shopId, scanNumber.trim());
      const data = response.data;
      message.success(data.warehouse_status === 'shipped'
        ? `${data.order_number} 全部包裹已扫码，订单完成出库`
        : `${data.order_number} 已扫描 ${data.scanned_parcel_count}/${data.expected_parcel_count} 个包裹`);
      setScanNumber('');
      actionRef.current?.reload();
    } catch (error) {
      message.error(getErrorMessage(error, '扫码出库失败；请核对面单与当前店铺'));
    } finally {
      setScanning(false);
      scanRef.current?.focus();
    }
  };

  const downloadLabels = async () => {
    if (!shopId || !(summary.ready_to_scan || readyAfterSales.some((row) => row.tracking_number)) || downloadingLabels) return;
    setDownloadingLabels(true);
    try {
      const content = await downloadReadyLabelsPdf(shopId);
      const url = URL.createObjectURL(content);
      const link = document.createElement('a');
      link.href = url;
      link.download = `4px-ready-labels-${getCurrentBeijingTime('YYYY-MM-DD')}-shop-${shopId}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      message.success('可发货订单的 4PX 面单已合并成一个 PDF，下载后可直接批量打印');
      actionRef.current?.reload();
    } catch (error) {
      let detail = getErrorMessage(error, '批量下载失败，请核对订单面单后重试');
      const body = (error as { response?: { data?: unknown } })?.response?.data;
      if (body instanceof Blob) {
        try { detail = (JSON.parse(await body.text()) as { message?: string }).message || detail; } catch { /* use fallback */ }
      }
      message.error(detail, 8);
    } finally { setDownloadingLabels(false); }
  };

  const loadPreview = async () => {
    if (!shopId || !previewDate) return;
    setPreviewLoading(true);
    try {
      setPreview((await previewFourPxPredeclared(shopId, previewDate)).data);
      setSelectedPairKeys([]);
    } catch (error) {
      setPreview(undefined);
      message.error(getErrorMessage(error, '查询 4PX 预申报失败'));
    } finally {
      setPreviewLoading(false);
    }
  };

  const loadAfterSalePreview = async () => {
    if (!shopId || !afterSaleDate) return;
    setAfterSaleBusy(true);
    try {
      const rows = (await previewAfterSaleFourPx(shopId, afterSaleDate)).data.rows;
      if (cfContextCurrent()) { setAfterSalePreview(rows); setSelectedCfKeys([]); }
    }
    catch (error) { setAfterSalePreview([]); setSelectedCfKeys([]); message.error(getErrorMessage(error, '查询售后 4PX 预申报失败')); }
    finally { setAfterSaleBusy(false); }
  };

  const prepareCfBatchAction = async (mode: CfBatchMode) => {
    if (!shopId || !afterSaleDate || cfBatchLock.current || afterSaleBusy) return;
    const rows = afterSalePreview.filter((row) => selectedCfKeys.includes(cfPairKey(row)));
    if (!rows.length) return;
    cfBatchLock.current = true;
    setAfterSaleBusy(true);
    try {
      const entries = await prepareCfBatch(rows, mode, shopId, afterSaleDate, cfContextCurrent);
      if (cfContextCurrent()) setCfBatch({ mode, date: afterSaleDate, entries, finished: false });
    } finally { cfBatchLock.current = false; setAfterSaleBusy(false); }
  };

  const confirmCfBatch = async () => {
    if (!shopId || !cfBatch || cfBatch.finished || cfBatchLock.current || !cfContextCurrent()) return;
    cfBatchLock.current = true;
    setAfterSaleBusy(true);
    try {
      await executeCfBatch(cfBatch.entries, cfBatch.mode, shopId, cfBatch.date, (result) => {
        if (cfContextCurrent()) setCfBatch((previous) => previous && ({ ...previous,
          entries: previous.entries.map((entry) => cfPairKey(entry) === cfPairKey(result) ? result : entry) }));
      }, cfContextCurrent);
      if (cfContextCurrent()) {
        setCfBatch((previous) => previous && ({ ...previous, finished: true }));
        await loadAfterSalePreview();
        actionRef.current?.reload();
      }
    } finally { cfBatchLock.current = false; setAfterSaleBusy(false); }
  };

  const bindAfterSale = async (id: number, tracking: string) => {
    if (!afterSaleDate) return;
    setAfterSaleBusy(true);
    try {
      await bindAfterSaleFourPx(id, afterSaleDate, tracking);
      message.success('售后单号已绑定；原订单 Shopify 运单不会更改');
      await loadAfterSalePreview();
      actionRef.current?.reload();
    } catch (error) { message.error(getErrorMessage(error, '售后对单失败')); }
    finally { setAfterSaleBusy(false); }
  };

  const prepareCfWriteback = async (row: CfFourPxRow) => {
    if (!shopId || !afterSaleDate) return;
    setAfterSaleBusy(true);
    try {
      const response = await previewCfShopify({ shop_id: shopId, date: afterSaleDate,
        customer_reference: row.customer_reference, tracking_number: row.tracking_number });
      setCfPreview(response.data);
      setCfConfirmedDate(afterSaleDate);
      setCfFulfillmentId(response.data.fulfillments.length === 1 ? response.data.fulfillments[0].id : undefined);
    } catch (error) { message.error(getErrorMessage(error, '获取原订单物流失败')); }
    finally { setAfterSaleBusy(false); }
  };

  const confirmCfWriteback = async () => {
    const fulfillment = cfPreview?.fulfillments.find((item) => item.id === cfFulfillmentId);
    if (!shopId || !cfPreview || !cfConfirmedDate || !fulfillment) return;
    setAfterSaleBusy(true);
    try {
      const response = await writebackCfShopify({ shop_id: shopId, date: cfConfirmedDate,
        customer_reference: cfPreview.customer_reference, tracking_number: cfPreview.tracking_number,
        confirm_overwrite: true, fulfillment_id: fulfillment.id, expected_tracking_numbers: fulfillment.tracking_numbers });
      message.success(response.data.customer_notification_requested ? '原订单物流已更新，已请求 Shopify 通知客户' : 'Shopify 已有相同单号，未重复发送通知');
      setCfPreview(null);
      await loadAfterSalePreview();
    } catch (error) { message.error(getErrorMessage(error, '覆盖回填失败；结果不明时请先核对 Shopify')); }
    finally { setAfterSaleBusy(false); }
  };

  const openAfterSaleLabel = async (id: number) => {
    setAfterSaleBusy(true);
    try {
      const result = await fetchAfterSaleLabel(id);
      window.open(result.data.pdf_url, '_blank', 'noopener,noreferrer');
      actionRef.current?.reload();
    } catch (error) { message.error(getErrorMessage(error, '获取售后面单失败')); }
    finally { setAfterSaleBusy(false); }
  };

  const pairKey = (row: FourPxPreviewRow) => JSON.stringify([row.customer_reference, row.tracking_number]);
  const selectableRows = (preview?.rows || []).filter((row): row is SelectableFourPxRow => Boolean(
    row.order_id && row.customer_reference && row.tracking_number &&
    (row.status === 'ready_to_bind' || row.status === 'already_bound')));

  const batchBind = async () => {
    if (!shopId || !previewDate || !preview || !selectedPairKeys.length || batchBusy) return;
    const selected = selectableRows.filter((row) => selectedPairKeys.includes(pairKey(row)));
    if (!selected.length) return;
    const byOrder = new Map<number, SelectableFourPxRow[]>();
    for (const row of selected) {
      const orderId = row.order_id;
      byOrder.set(orderId, [...(byOrder.get(orderId) || []), row]);
    }
    const chunks: SelectableFourPxRow[][] = [];
    for (const rows of byOrder.values()) {
      if (!chunks.length || chunks[chunks.length - 1].length + rows.length > 20) chunks.push([]);
      chunks[chunks.length - 1].push(...rows);
    }
    const concurrency = Math.min(3, chunks.length);
    setBatchBusy(true);
    setBatchResults([]);
    setBatchSkipped([]);
    setBatchError('');
    try {
      setBatchProgress(`绑定 0/${chunks.length} 批 · ${concurrency} 批并行`);
      const collect = (outcomes: Array<PromiseSettledResult<{
        orders: FourPxBatchOrderResult[]; skipped: string[];
      }> | undefined>) => outcomes.flatMap((outcome) => outcome?.status === 'fulfilled' ? [outcome.value] : []);
      const outcomes = await runConcurrentBatches(chunks, concurrency, async (chunk) => {
        const response = await batchBindFourPxPredeclared(shopId, previewDate, chunk.map((row) => ({
          customer_reference: row.customer_reference, tracking_number: row.tracking_number,
        })));
        return {
          orders: response.data.orders,
          skipped: response.data.skipped.map((row) =>
            `${row.customer_reference} / ${row.tracking_number}：${fourPxStatusText[row.reason] || row.reason}`),
        };
      }, (completed, partial) => {
        const finished = collect(partial);
        setBatchResults(finished.flatMap((batch) => batch.orders));
        setBatchSkipped(finished.flatMap((batch) => batch.skipped));
        setBatchProgress(`绑定 ${completed}/${chunks.length} 批 · ${concurrency} 批并行`);
      });
      const successful = collect(outcomes);
      const results = successful.flatMap((batch) => batch.orders);
      const skipped = successful.flatMap((batch) => batch.skipped);
      const failedBatches = outcomes.flatMap((outcome, index) => outcome.status === 'rejected'
        ? [{ index, detail: getErrorMessage(outcome.reason, '处理失败，结果可能部分成功') }] : []);
      if (failedBatches.length) {
        const failedIndexes = failedBatches.map((row) => row.index + 1).sort((a, b) => a - b).join('、');
        setBatchError(`第 ${failedIndexes} 批绑定结果未确认；其他批次继续处理。完成后刷新预览，核对已绑定和 Shopify 状态再重试。${failedBatches[0].detail}`);
      }
      const ready = results.filter((row) => row.shopify_status === 'ready_to_writeback');
      if (ready.length) {
        setBatchProgress(`Shopify 回填 0/${ready.length} 单 · 最多 3 单并行`);
        await runConcurrentBatches(ready, 3, async (row) => {
          const response = await writebackShopifyTracking(row.order_id);
          return response.data.status;
        }, (completed, writebacks) => {
          writebacks.forEach((outcome, index) => {
            if (!outcome) return;
            ready[index].shopify_status = outcome.status === 'fulfilled' &&
              (outcome.value === 'updated' || outcome.value === 'already_fulfilled')
              ? outcome.value : 'failed';
            if (outcome.status === 'rejected') {
              ready[index].shopify_error = `${getErrorMessage(outcome.reason, '回填结果不确定')}；请先核对 Shopify，勿直接重复提交`;
            } else if (ready[index].shopify_status === 'failed') {
              ready[index].shopify_error = 'Shopify 未确认回填成功，请核对订单';
            }
          });
          setBatchResults([...results]);
          setBatchProgress(`Shopify 回填 ${completed}/${ready.length} 单 · 最多 3 单并行`);
        });
      }
      if (failedBatches.length || results.some((row) => row.shopify_status === 'failed') || skipped.length) {
        message.warning('批量操作完成，但有异常；请查看逐单结果并核对 Shopify 后再重试', 8);
      } else {
        message.success('批量对单完成；已齐全的订单已请求 Shopify 回填，仓库仍需扫码出库');
      }
    } catch (error) {
      const detail = getErrorMessage(error, '批量回填中断，结果可能部分成功，请先核对 Shopify 再重试');
      setBatchError(`${detail}；部分批次可能未执行，请刷新预览核对。`);
      message.error('批量回填中断，请查看结果并核对后再重试', 8);
    } finally {
      setBatchResultOpen(true);
      setBatchBusy(false);
      setBatchProgress('');
      await loadPreview();
      actionRef.current?.reload();
    }
  };

  const bind = async (row: FourPxPreviewRow) => {
    if (!shopId || !previewDate || !row.customer_reference || !row.tracking_number) return;
    setBindingTracking(row.tracking_number);
    try {
      const result = (await bindFourPxPredeclared(shopId, previewDate, row.customer_reference, row.tracking_number)).data;
      if (result.shopify_status === 'failed') {
        message.warning(`4PX 单号已保存，但 Shopify 回填失败：${result.shopify_error || '请在订单详情中核对并重试'}`, 8);
      } else if (result.shopify_status === 'awaiting_parcels') {
        message.info('已绑定当前包裹；待全部包裹绑定齐全后，再回填 Shopify。');
      } else if (result.shopify_status === 'already_fulfilled') {
        message.info('4PX 单号已核对，Shopify 已有相同单号；本次未重复发送客户通知。');
      } else {
        message.success('4PX 单号已回填 Shopify，并已请求向客户发送发货通知；仓库仍需扫码出库。');
      }
      await loadPreview();
      actionRef.current?.reload();
    } catch (error) {
      message.error(getErrorMessage(error, '绑定失败，请刷新核对结果'));
    } finally {
      setBindingTracking(undefined);
    }
  };

  const columns: ProColumns<ReadyOrder>[] = [
    { title: '订单号', dataIndex: 'order_number', hideInSearch: true, width: 140,
      render: (_, row) => <a onClick={() => history.push(`/orders/detail/${row.id}`)}>{row.order_number}</a> },
    { title: '搜索订单号', dataIndex: 'search', hideInTable: true, fieldProps: { placeholder: '输入订单号' } },
    { title: '客户', dataIndex: 'customer_name', hideInSearch: true, width: 170,
      render: (_, row) => row.customer_name || '-' },
    { title: '下单时间', dataIndex: 'order_date', hideInSearch: true, width: 180,
      render: (_, row) => formatBeijingTime(row.order_date) },
    { title: '面单进度', dataIndex: 'label_state', hideInSearch: true, width: 160,
      render: (_, row) => <Tag color={row.label_state === 'awaiting_labels' ? 'orange' : 'blue'}>
        {row.registered_parcel_count}/{row.expected_parcel_count} 张已登记
      </Tag> },
    { title: '扫码进度', dataIndex: 'scanned_parcel_count', hideInSearch: true, width: 150,
      render: (_, row) => <Tag color={row.scanned_parcel_count ? 'cyan' : 'default'}>
        {row.scanned_parcel_count}/{row.expected_parcel_count} 个已扫码
      </Tag> },
    { title: '操作', valueType: 'option', width: 110,
      render: (_, row) => <Button type="link" onClick={() => history.push(`/orders/detail/${row.id}`)}>查看详情</Button> },
  ];

  return <PageContainer title="发货中心" subTitle="Shopify 已发货 ≠ 仓库已出库">
    {!shopId ? <ShopRequiredNotice reason="面单和预申报按店铺隔离，请先选择店铺。" /> : <>
      <Card
        style={{ marginBottom: 16 }}
        // 批量下载是整页的主操作，放卡片右上角；塞在统计数字中间会把那一格撑变形
        extra={
          <Button
            icon={<FilePdfOutlined />}
            loading={downloadingLabels}
            disabled={!(summary.ready_to_scan || readyAfterSales.some((row) => row.tracking_number))}
            onClick={downloadLabels}
          >
            批量下载面单 PDF
          </Button>
        }
      >
        <div className="stat-row">
          <Statistic title="可发货订单" value={summary.ready_orders} />
          <Statistic title="可发货售后单" value={summary.after_sale_ready} />
          <Statistic title="待登记面单" value={summary.awaiting_labels} />
          <Statistic title="面单齐全待扫码" value={summary.ready_to_scan} />
          <Statistic title="部分包裹已扫码" value={summary.partially_scanned} />
        </div>
      </Card>
      <Card title="售后补发 · 4PX 对单" style={{ marginBottom: 16 }}>
        <Space wrap><DatePicker disabled={afterSaleBusy || Boolean(cfBatch) || Boolean(cfPreview)} onChange={(_, dateString) => {
          setAfterSaleDate(typeof dateString === 'string' ? dateString : undefined); setAfterSalePreview([]); setSelectedCfKeys([]);
        }} />
          <Button disabled={!afterSaleDate || Boolean(cfBatch) || Boolean(cfPreview)} loading={afterSaleBusy} onClick={loadAfterSalePreview}>查询 CF 预申报</Button></Space>
        <Space wrap style={{ marginBottom: 12 }}>
          <Typography.Text>已选 {selectedCfKeys.length} 条</Typography.Text>
          <Button disabled={afterSaleBusy || Boolean(cfBatch) || Boolean(cfPreview) || !afterSalePreview.some(canSelectCf)}
            onClick={() => setSelectedCfKeys(afterSalePreview.filter(canSelectCf).map(cfPairKey))}>全选可处理记录</Button>
          <Button disabled={afterSaleBusy || Boolean(cfBatch) || !selectedCfKeys.length} onClick={() => setSelectedCfKeys([])}>清空选择</Button>
          <Button disabled={afterSaleBusy || Boolean(cfBatch) || Boolean(cfPreview) || !selectedCfKeys.length}
            onClick={() => void prepareCfBatchAction('bind')}>批量只绑定售后</Button>
          <Button type="primary" disabled={afterSaleBusy || Boolean(cfBatch) || Boolean(cfPreview) || !selectedCfKeys.length}
            onClick={() => void prepareCfBatchAction('overwrite')}>批量覆盖原单并通知</Button>
        </Space>
        <Table<CfFourPxRow> size="small" rowKey={cfPairKey} dataSource={afterSalePreview}
          rowSelection={{ selectedRowKeys: selectedCfKeys, onChange: (keys) => setSelectedCfKeys(keys.map(String)),
            getCheckboxProps: (row) => ({ disabled: !canSelectCf(row) || afterSaleBusy || Boolean(cfBatch) || Boolean(cfPreview) }) }}
          scroll={{ x: 600 }} pagination={{ pageSize: 10 }} columns={[
            { title: '客户单号', dataIndex: 'customer_reference' },
            { title: '4PX 单号', dataIndex: 'tracking_number' },
            { title: '原订单', dataIndex: 'order_number', render: (value?: string) => value || '未匹配' },
            { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'ready_to_bind' || value === 'original_order_only' ? 'green' : value === 'already_bound' ? 'blue' : 'red'}>{value === 'ready_to_bind' ? '可绑定' : value === 'already_bound' ? '已绑定' : value === 'original_order_only' ? '已匹配原订单（无售后单）' : value === 'after_sale_deleted' ? '售后单已删除' : `异常：${value}`}</Tag> },
            { title: '操作', render: (_: unknown, row: CfFourPxRow) => <Space wrap>
              <Button size="small" disabled={row.status !== 'ready_to_bind' || !row.after_sale_id || afterSaleBusy || Boolean(cfBatch)}
                onClick={() => row.after_sale_id && void bindAfterSale(row.after_sale_id, row.tracking_number)}>只绑定售后</Button>
              <Button size="small" type="primary" disabled={!row.order_id || !['ready_to_bind', 'already_bound', 'original_order_only'].includes(row.status) || afterSaleBusy || Boolean(cfBatch)}
                onClick={() => void prepareCfWriteback(row)}>覆盖原单并通知</Button>
            </Space> },
          ]} />
      </Card>
      <Modal title={cfBatch?.finished ? '批量处理结果' : cfBatch?.mode === 'bind' ? '确认批量只绑定售后' : '确认批量覆盖原单并通知'}
        width={900} open={Boolean(cfBatch)} closable={!afterSaleBusy} maskClosable={false}
        onCancel={() => { if (!afterSaleBusy) setCfBatch(null); }}
        onOk={() => cfBatch?.finished ? setCfBatch(null) : void confirmCfBatch()}
        confirmLoading={afterSaleBusy} cancelButtonProps={{ disabled: afterSaleBusy }}
        okText={cfBatch?.finished ? '完成' : cfBatch?.mode === 'bind' ? '确认绑定售后' : '确认覆盖并通知'}
        okButtonProps={{ disabled: !cfBatch?.finished && !cfBatch?.entries.some((entry) => entry.state === 'ready') }}>
        {cfBatch && <Space direction="vertical" style={{ width: '100%' }}>
          <Alert showIcon type={cfBatch.mode === 'overwrite' ? 'warning' : 'info'} message={cfBatch.mode === 'overwrite'
            ? '将覆盖原订单 Shopify 运单并通知客户。'
            : '仅绑定售后，不改原运单、不通知客户。'} />
          <Typography.Text>共 {cfBatch.entries.length} 条 · 待处理 {cfBatch.entries.filter((entry) => entry.state === 'ready').length}
            {' '}· 成功 {cfBatch.entries.filter((entry) => entry.state === 'success').length}
            {' '}· 跳过 {cfBatch.entries.filter((entry) => entry.state === 'skipped').length}
            {' '}· 失败 {cfBatch.entries.filter((entry) => entry.state === 'failed').length}</Typography.Text>
          <Table<CfBatchEntry> size="small" rowKey={cfPairKey} dataSource={cfBatch.entries}
            scroll={{ x: 720, y: 360 }} pagination={false} columns={[
              { title: '客户单号 / 原订单', render: (_, row) => `${row.customer_reference} / ${row.order_number || '未匹配'}` },
              ...(cfBatch.mode === 'overwrite' ? [{ title: '原 Shopify 单号', render: (_: unknown, row: CfBatchEntry) => row.expected_tracking_numbers?.join('、') || '—' }] : []),
              { title: '补发单号', dataIndex: 'tracking_number' },
              { title: '处理状态', render: (_, row) => <><Tag color={row.state === 'failed' ? 'red' : row.state === 'success' ? 'green' : 'blue'}>
                {{ ready: '待确认', success: '成功', skipped: '跳过', failed: '失败' }[row.state]}</Tag>{row.detail}</> },
            ]} />
          <Typography.Text type="secondary">失败记录需人工重试。</Typography.Text>
        </Space>}
      </Modal>
      <Modal title="确认覆盖原订单物流并通知客户" open={Boolean(cfPreview)} onCancel={() => !afterSaleBusy && setCfPreview(null)}
        onOk={() => void confirmCfWriteback()} confirmLoading={afterSaleBusy} okText="确认覆盖并通知"
        okButtonProps={{ disabled: !cfFulfillmentId }}>
        {cfPreview && <Space direction="vertical" style={{ width: '100%' }}>
          <Alert type="warning" showIcon message="将覆盖原物流号并通知客户。" />
          <Typography.Text>客户单号：{cfPreview.customer_reference} · 原订单：{cfPreview.order_number}</Typography.Text>
          <Typography.Text strong>补发物流：{cfPreview.tracking_number}</Typography.Text>
          <Select style={{ width: '100%' }} value={cfFulfillmentId} onChange={setCfFulfillmentId} placeholder="请选择要覆盖的原包裹（多包裹不会自动选择）"
            options={cfPreview.fulfillments.map((item) => ({ value: item.id, label: `${item.id.split('/').pop()} · 原单号：${item.tracking_numbers.join('、') || '未登记'}` }))} />
        </Space>}
      </Modal>
      <Card title="已备齐售后单" style={{ marginBottom: 16 }}>
        <Table<ReadyAfterSale> size="small" rowKey="id" dataSource={readyAfterSales} scroll={{ x: 650 }} pagination={{ pageSize: 10 }} columns={[
          { title: '售后单', dataIndex: 'customer_reference', render: (_: unknown, row) => <a onClick={() => history.push(`/after-sales?after_sale_id=${row.id}`)}>{row.customer_reference}</a> },
          { title: '原订单', dataIndex: 'order_number' },
          { title: '4PX 单号', dataIndex: 'tracking_number', render: (value: string) => value || '待对单' },
          { title: '面单', dataIndex: 'label_barcode', render: (value: string) => value ? <Tag color="green">已获取</Tag> : <Tag>待获取</Tag> },
          { title: '操作', render: (_: unknown, row) => <Button type="link" disabled={!row.tracking_number || afterSaleBusy} onClick={() => void openAfterSaleLabel(row.id)}>获取面单</Button> },
        ]} />
      </Card>
      <Card title={<Space><BarcodeOutlined /> 扫物流面单出库</Space>} style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: screens.md ? 560 : '100%' }}>
          <Input ref={scanRef} autoFocus value={scanNumber} placeholder="扫描实体面单上的条码"
            aria-label="扫描实体面单条码" onChange={(event) => setScanNumber(event.target.value)} onPressEnter={scan} />
          <Button type="primary" loading={scanning} disabled={!scanNumber.trim()} onClick={scan}>确认扫描</Button>
        </Space.Compact>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          多包裹须全部扫齐才完成出库。
        </Typography.Paragraph>
      </Card>
      <Card title="4PX 预申报对单预览" style={{ marginBottom: 16 }}>
        <Space wrap>
          <DatePicker onChange={(_, dateString) => { setPreviewDate(typeof dateString === 'string' ? dateString : undefined); setPreview(undefined); setSelectedPairKeys([]); }} />
          <Button loading={previewLoading} disabled={!previewDate} onClick={loadPreview}>查询当天预申报</Button>
        </Space>
        {preview && <>
          <Alert type={preview.exceptions ? 'warning' : 'info'} showIcon
            message={`共 ${preview.total} 条，可绑定 ${preview.ready_to_bind} 条，Shopify 已回填 ${preview.rows.filter((row) => row.status === 'shopify_already_fulfilled').length} 条，异常 ${preview.exceptions} 条`} style={{ marginBottom: 12 }} />
          <Space wrap style={{ marginBottom: 12 }}>
            <Button disabled={!selectableRows.length || batchBusy}
              onClick={() => setSelectedPairKeys(selectableRows.map(pairKey))}>全选可处理记录（{selectableRows.length}）</Button>
            <Button disabled={!selectedPairKeys.length || batchBusy} onClick={() => setSelectedPairKeys([])}>清空选择</Button>
            <Popconfirm title={`确认批量处理选中的 ${selectedPairKeys.length} 条 4PX 记录？`}
              description="会回填 Shopify 并通知客户；不出库。" onConfirm={batchBind}>
              <Button type="primary" loading={batchBusy} disabled={!selectedPairKeys.length}>
                {batchBusy ? batchProgress : `批量绑定并回填（${selectedPairKeys.length}）`}
              </Button>
            </Popconfirm>
          </Space>
          <Table<FourPxPreviewRow> rowKey={pairKey}
            size="small" scroll={{ x: 650 }} dataSource={preview.rows} pagination={{ pageSize: 20 }}
            rowSelection={{ selectedRowKeys: selectedPairKeys, preserveSelectedRowKeys: true,
              onChange: (keys) => setSelectedPairKeys(keys.map(String)),
              getCheckboxProps: (row) => ({ disabled: batchBusy || !row.customer_reference || !row.tracking_number ||
                (row.status !== 'ready_to_bind' && row.status !== 'already_bound') }) }}
            columns={[
              { title: '客户单号', dataIndex: 'customer_reference' },
              { title: '4PX 单号', dataIndex: 'tracking_number' },
              { title: '本地订单', dataIndex: 'order_number', render: (_: unknown, row) => row.order_id
                ? <a onClick={() => history.push(`/orders/detail/${row.order_id}`)}>{row.order_number}</a> : '-' },
              { title: '核对结果', dataIndex: 'status', render: (status: string) =>
                <Tag color={status === 'ready_to_bind' ? 'green' : status === 'already_bound' ? 'blue' : status === 'shopify_already_fulfilled' ? 'cyan' : 'red'}>
                  {fourPxStatusText[status] || status}</Tag> },
              { title: '操作', key: 'bind', render: (_: unknown, row) => row.status === 'ready_to_bind'
                ? <Button size="small" disabled={batchBusy} loading={bindingTracking === row.tracking_number} onClick={() => bind(row)}>单条绑定并回填</Button> : '-' },
            ]} />
        </>}
      </Card>
      <Modal title="4PX 批量回填结果" open={batchResultOpen} onCancel={() => setBatchResultOpen(false)}
        onOk={() => setBatchResultOpen(false)} cancelButtonProps={{ style: { display: 'none' } }}
        width={screens.md ? 760 : 'calc(100vw - 32px)'}>
        {batchError && <Alert type="error" showIcon message={batchError} style={{ marginBottom: 12 }} />}
        <Alert type={batchError || batchSkipped.length || batchResults.some((row) => row.shopify_status === 'failed') ? 'warning' : 'info'}
          showIcon style={{ marginBottom: 12 }}
          message={`订单 ${batchResults.length} 单：已回填 ${batchResults.filter((row) => row.shopify_status === 'updated').length}，原已回填 ${batchResults.filter((row) => row.shopify_status === 'already_fulfilled').length}，待回填 ${batchResults.filter((row) => row.shopify_status === 'ready_to_writeback').length}，待补包裹 ${batchResults.filter((row) => row.shopify_status === 'awaiting_parcels').length}，失败 ${batchResults.filter((row) => row.shopify_status === 'failed').length}，跳过 ${batchSkipped.length} 条`} />
        <Table<FourPxBatchOrderResult> rowKey="order_id" size="small"
          dataSource={batchResults} pagination={{ pageSize: 10 }} scroll={{ x: 620 }} columns={[
            { title: '订单', dataIndex: 'order_number', render: (_: unknown, row) =>
              <a onClick={() => history.push(`/orders/detail/${row.order_id}`)}>{row.order_number}</a> },
            { title: '新绑定', dataIndex: 'newly_bound' },
            { title: '已绑定', dataIndex: 'already_bound' },
            { title: 'Shopify', dataIndex: 'shopify_status', render: (status: string) => <Tag color={
              status === 'updated' ? 'green' : status === 'already_fulfilled' ? 'blue' : 'orange'}>
              {({ updated: '已回填并请求通知客户', already_fulfilled: '原已回填，不重复通知', ready_to_writeback: '待回填',
                awaiting_parcels: '包裹未齐，待回填', failed: '失败，需核对' } as Record<string, string>)[status] || status}</Tag> },
            { title: '说明', dataIndex: 'shopify_error', render: (value: string | null) => value || '-' },
          ]} />
        {batchSkipped.length > 0 && <Alert type="warning" showIcon style={{ marginTop: 12 }}
          message={`跳过 ${batchSkipped.length} 条异常记录`}
          description={<div>{batchSkipped.map((item) => <div key={item}>{item}</div>)}</div>} />}
      </Modal>
      <ProTable<ReadyOrder>
        rowKey="id" actionRef={actionRef} columns={columns} scroll={{ x: 950 }}
        search={{ labelWidth: 'auto' }}
        pagination={persistedPagination('shipping-ready', 20)}
        toolBarRender={() => [<Button key="refresh" icon={<ReloadOutlined />} onClick={() => actionRef.current?.reload()}>刷新队列</Button>]}
        request={async (params) => {
          try {
            const response = await listReadyOrders({
              shop_id: shopId, page: params.current, per_page: params.pageSize,
              search: params.search as string | undefined,
            });
            setSummary(response.data.summary);
            setReadyAfterSales(response.data.after_sales);
            return { data: response.data.orders, total: response.data.pagination.total, success: true };
          } catch (error) {
            message.error(getErrorMessage(error, '获取发货队列失败'));
            return { data: [], total: 0, success: false };
          }
        }}
      />
    </>}
  </PageContainer>;
};

export default Shipping;
