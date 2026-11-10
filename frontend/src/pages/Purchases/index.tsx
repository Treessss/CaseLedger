import {
  ClearOutlined,
  DownloadOutlined,
  FilePdfOutlined,
  DeleteOutlined,
  SearchOutlined,
  ShoppingCartOutlined,
} from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Drawer,
  Empty,
  Grid,
  Image,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Statistic,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createPurchaseList,
  createPurchaseReplenishment,
  confirmPurchaseList,
  deletePurchaseList,
  downloadPurchaseListPdf,
  downloadPurchaseListExcel,
  getPurchaseList,
  listPurchaseCandidates,
  listPurchaseLists,
  receivePurchaseList,
  type PurchaseCandidate,
  type AfterSalePurchaseCandidate,
  type PurchaseColor,
  type PurchaseItem,
  type PurchaseProduct,
  type PurchaseListDetail,
  type PurchaseListSummary,
} from '@/services/purchases';
import { getErrorMessage } from '@/utils/error';
import { persistedPagination } from '@/utils/pagination';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';
import { formatBeijingTime } from '@/utils/timezone';
import { ShopRequiredNotice } from '@/components';
import ItemEditor from './ItemEditor';
import PurchaseOrderLinks from './PurchaseOrderLinks';

const remainingFor = (item: PurchaseItem, purchase: PurchaseListDetail | null) => {
  const previous = purchase?.receipt?.find((row) => row.group_key === item.group_key);
  return Math.max(item.quantity - (previous?.received_quantity || 0) - (previous?.damaged_quantity || 0) - (previous?.confirmed_missing_quantity || 0), 0);
};

type ReceiptCount = { received_quantity: number; damaged_quantity: number; missing_quantity: number };
const emptyCount = (): ReceiptCount => ({ received_quantity: 0, damaged_quantity: 0, missing_quantity: 0 });

/** 一张清单的四个数：合格 / 破损 / 缺货 / 未到。 */
const countsOf = (row: Pick<PurchaseListSummary, 'total_quantity' | 'received_quantity' | 'damaged_quantity' | 'shortage_quantity' | 'pending_quantity'>) => {
  const received = row.received_quantity ?? 0;
  const damaged = row.damaged_quantity ?? 0;
  const shortage = row.shortage_quantity ?? 0;
  const pending = row.pending_quantity ?? Math.max(row.total_quantity - received - damaged - shortage, 0);
  return { received, damaged, shortage, pending };
};

const ProgressBar = ({ received, damaged, shortage, pending, total }: {
  received: number; damaged: number; shortage: number; pending: number; total: number;
}) => {
  const width = (value: number) => `${total > 0 ? Math.min(value / total * 100, 100) : 0}%`;
  return <div className="purchase-progress-bar" aria-hidden>
    <span className="is-good" style={{ width: width(received) }} />
    <span className="is-bad" style={{ width: width(damaged + shortage) }} />
    <span className="is-pending" style={{ width: width(pending) }} />
  </div>;
};

const receiptDraftKey = (id: number) => `caseledger_purchase_receipt_${id}`;

const Purchases: React.FC = () => {
  const screens = Grid.useBreakpoint();
  const listsActionRef = useRef<ActionType>(null);
  const candidatesActionRef = useRef<ActionType>(null);
  const selectedShop = getSelectedShopId();
  const shopId = selectedShop === ALL_SHOPS ? null : Number(selectedShop);
  const [tab, setTab] = useState('create');
  const [selectedOrderIds, setSelectedOrderIds] = useState<number[]>([]);
  const [selectedAfterSaleIds, setSelectedAfterSaleIds] = useState<number[]>([]);
  const [eligibleOrderIds, setEligibleOrderIds] = useState<number[]>([]);
  const [eligibleAfterSaleIds, setEligibleAfterSaleIds] = useState<number[]>([]);
  const [visibleAfterSales, setVisibleAfterSales] = useState<AfterSalePurchaseCandidate[]>([]);
  const [purchaseView, setPurchaseView] = useState<'needs_purchase' | 'unprocured' | 'replenish' | 'purchased' | 'purchasing' | 'stocked' | 'after_sale'>('needs_purchase');
  const [categoryCounts, setCategoryCounts] = useState({ needs_purchase: 0, unprocured: 0, replenish: 0, purchased: 0, purchasing: 0, stocked: 0, after_sale: 0 });
  const [selectionLimit, setSelectionLimit] = useState(1000);
  const selectAllCount = purchaseView === 'after_sale'
    ? selectedOrderIds.length + eligibleAfterSaleIds.length
    : purchaseView === 'needs_purchase'
      ? eligibleOrderIds.length + eligibleAfterSaleIds.length
      : eligibleOrderIds.length + selectedAfterSaleIds.length;
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState<PurchaseListDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailSearch, setDetailSearch] = useState('');
  const [exportingPdf, setExportingPdf] = useState<string | null>(null);
  const [exportingExcel, setExportingExcel] = useState<string | null>(null);
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [itemEditor, setItemEditor] = useState<{ purchase: PurchaseListDetail | null; shopId: number } | null>(null);
  const [receiptOpen, setReceiptOpen] = useState(false);
  const [receiptReviewed, setReceiptReviewed] = useState(false);
  const [finalizeReceipt, setFinalizeReceipt] = useState(true);
  const [receiptBatchId, setReceiptBatchId] = useState('');
  const [receiptDraft, setReceiptDraft] = useState<Record<string, ReceiptCount>>({});
  const [receiptSearch, setReceiptSearch] = useState('');
  const [receiptShowPendingOnly, setReceiptShowPendingOnly] = useState(true);
  const [receiptExceptions, setReceiptExceptions] = useState<Record<string, boolean>>({});
  const [receiptRestoredAt, setReceiptRestoredAt] = useState<string | null>(null);
  const [replenishBusy, setReplenishBusy] = useState(false);

  useEffect(() => {
    setSelectedOrderIds([]);
    setSelectedAfterSaleIds([]);
  }, [shopId]);

  const receiptStats = useMemo(() => {
    const items = detail?.items || [];
    return items.reduce((stats, item) => {
      const remaining = remainingFor(item, detail);
      const entered = receiptDraft[item.group_key] || emptyCount();
      stats.remaining += remaining;
      stats.received += entered.received_quantity;
      stats.damaged += entered.damaged_quantity;
      stats.missing += entered.missing_quantity;
      if (entered.received_quantity + entered.damaged_quantity + entered.missing_quantity > remaining) stats.invalid += 1;
      return stats;
    }, { remaining: 0, received: 0, damaged: 0, missing: 0, invalid: 0 });
  }, [detail, receiptDraft]);
  const receiptUncounted = Math.max(receiptStats.remaining - receiptStats.received - receiptStats.damaged - receiptStats.missing, 0);
  const receiptCounted = receiptStats.received + receiptStats.damaged + receiptStats.missing;
  const receiptMissing = receiptStats.missing + (finalizeReceipt ? receiptUncounted : 0);

  const visibleReceiptProducts = useMemo(() => {
    const query = receiptSearch.trim().toLocaleLowerCase();
    return (detail?.products || []).map((product) => ({
      ...product,
      colors: product.colors.map((color) => ({
        ...color,
        variants: color.variants.filter((item) => {
          if (receiptShowPendingOnly && remainingFor(item, detail) === 0) return false;
          return !query || [product.title, color.name, item.model_title, item.variant_title, item.sku]
            .some((text) => text?.toLocaleLowerCase().includes(query));
        }),
      })).filter((color) => color.variants.length > 0),
    })).filter((product) => product.colors.length > 0);
  }, [detail, receiptSearch, receiptShowPendingOnly]);

  const visibleDetailProducts = useMemo(() => {
    const query = detailSearch.trim().toLocaleLowerCase();
    if (!query) return detail?.products || [];
    return (detail?.products || []).map((product) => {
      const productMatch = product.title.toLocaleLowerCase().includes(query);
      const colors = product.colors.map((color) => {
        const colorMatch = color.name.toLocaleLowerCase().includes(query);
        const variants = color.variants.filter((item) => productMatch || colorMatch ||
          [item.model_title, item.variant_title, item.sku].some((text) => text?.toLocaleLowerCase().includes(query)));
        return { ...color, variants, variant_count: variants.length, quantity: variants.reduce((sum, item) => sum + item.quantity, 0) };
      }).filter((color) => color.variants.length > 0);
      return {
        ...product,
        colors,
        color_count: colors.length,
        variant_count: colors.reduce((sum, color) => sum + color.variant_count, 0),
        quantity: colors.reduce((sum, color) => sum + color.quantity, 0),
        order_numbers: [...new Set(colors.flatMap((color) => color.variants.flatMap((item) => item.order_numbers)))],
      };
    }).filter((product) => product.colors.length > 0);
  }, [detail, detailSearch]);

  const confirmProcurement = async () => {
    if (!detail) return;
    setWorkflowBusy(true);
    try {
      const response = await confirmPurchaseList(detail.id);
      setDetail(response.data);
      listsActionRef.current?.reload();
      const skipped = response.data.skipped_orders || [];
      const skippedAfterSales = response.data.skipped_after_sales || [];
      const trimmed = response.data.trimmed_orders || [];
      if (skipped.length || skippedAfterSales.length || trimmed.length) {
        const parts = [];
        if (skipped.length) parts.push(`移除 ${skipped.length} 个已出库/失效订单：${skipped.map((row) => `${row.order_number}（${row.reason}）`).join('、')}`);
        if (skippedAfterSales.length) parts.push(`移除 ${skippedAfterSales.length} 个已出库/失效售后：${skippedAfterSales.map((row) => `${row.customer_reference}（${row.reason}）`).join('、')}`);
        if (trimmed.length) parts.push(`移除 ${trimmed.reduce((sum, row) => sum + row.quantity, 0)} 件已被其他采购单/库存覆盖的商品`);
        message.warning({ content: `已确认发起采购；自动${parts.join('；')}`, duration: 10 });
      } else {
        message.success('已确认发起采购，相关订单已标记为采购中');
      }
    } catch (error) {
      message.error(getErrorMessage(error, '确认采购失败'));
    } finally {
      setWorkflowBusy(false);
    }
  };

  const replenish = async () => {
    if (!detail) return;
    setReplenishBusy(true);
    try {
      const response = await createPurchaseReplenishment(detail.id);
      const refreshed = await getPurchaseList(detail.id);
      setDetail(refreshed.data);
      listsActionRef.current?.reload();
      message.success(`已生成补采清单 #${response.data.id}（待发起，核对后再确认采购）`);
    } catch (error) {
      message.error(getErrorMessage(error, '生成补采清单失败'));
    } finally {
      setReplenishBusy(false);
    }
  };

  const openReceipt = (purchase: PurchaseListDetail | null = detail) => {
    if (!purchase) return;
    const base: Record<string, ReceiptCount> = Object.fromEntries(purchase.items.map((item) => [
      item.group_key, emptyCount(),
    ]));
    let restored: Record<string, ReceiptCount> | null = null;
    let savedAtLabel: string | null = null;
    try {
      const raw = window.localStorage.getItem(receiptDraftKey(purchase.id));
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed?.savedAt && Date.now() - parsed.savedAt < 24 * 60 * 60 * 1000) {
          const merged = { ...base };
          let any = false;
          for (const [key, value] of Object.entries(parsed.draft || {})) {
            const count = value as ReceiptCount;
            if (!merged[key] || !count) continue;
            if (count.received_quantity || count.damaged_quantity || count.missing_quantity) {
              merged[key] = { received_quantity: count.received_quantity || 0,
                damaged_quantity: count.damaged_quantity || 0, missing_quantity: count.missing_quantity || 0 };
              any = true;
            }
          }
          if (any) {
            restored = merged;
            savedAtLabel = new Date(parsed.savedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
          }
        }
      }
    } catch {
      // localStorage 不可用时按新清点处理
    }
    setReceiptDraft(restored || base);
    setReceiptExceptions({});
    setReceiptRestoredAt(savedAtLabel);
    setReceiptReviewed(false);
    setFinalizeReceipt(false);
    setReceiptSearch('');
    setReceiptShowPendingOnly(true);
    // getRandomValues is supported by Safari 13; randomUUID is not.
    setReceiptBatchId(Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')).join(''));
    setReceiptOpen(true);
  };

  const clearReceiptDraft = () => {
    if (!detail) return;
    try { window.localStorage.removeItem(receiptDraftKey(detail.id)); } catch { /* ignore */ }
    setReceiptDraft(Object.fromEntries(detail.items.map((item) => [item.group_key, emptyCount()])));
    setReceiptExceptions({});
    setReceiptRestoredAt(null);
    setReceiptReviewed(false);
  };

  const updateReceiptQuantity = (item: PurchaseListDetail['items'][number], field: keyof ReceiptCount, amount: number | null) => {
    const remaining = remainingFor(item, detail);
    setReceiptDraft((current) => {
      const previous = current[item.group_key] || emptyCount();
      const next = { ...previous, [field]: Math.min(Math.max(amount ?? 0, 0), remaining) };
      let overflow = next.received_quantity + next.damaged_quantity + next.missing_quantity - remaining;
      if (overflow > 0) {
        // 现场常见顺序是先填实到、再补破损/少发：超出时应自动从其它字段回退，
        // 而不是把用户刚填的数字悄悄夹回 0。
        for (const key of ['missing_quantity', 'damaged_quantity', 'received_quantity'] as const) {
          if (key === field || overflow <= 0) continue;
          const take = Math.min(next[key], overflow);
          next[key] -= take;
          overflow -= take;
        }
        if (overflow > 0) next[field] = Math.max(next[field] - overflow, 0);
      }
      return { ...current, [item.group_key]: next };
    });
    setReceiptReviewed(false);
  };

  const fillReceiptItems = (items: PurchaseListDetail['items'], fill: boolean) => {
    setReceiptDraft((current) => ({ ...current, ...Object.fromEntries(items.map((item) => [
      item.group_key, { ...emptyCount(), received_quantity: fill ? remainingFor(item, detail) : 0 },
    ])) }));
    setReceiptReviewed(false);
  };

  useEffect(() => {
    if (!receiptOpen || !detail) return;
    const key = receiptDraftKey(detail.id);
    const hasInput = Object.values(receiptDraft).some((count) =>
      count.received_quantity || count.damaged_quantity || count.missing_quantity);
    try {
      if (hasInput) window.localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), draft: receiptDraft }));
      else window.localStorage.removeItem(key);
    } catch {
      // 隐私模式下忽略草稿写入
    }
  }, [receiptDraft, receiptOpen, detail]);

  const confirmReceipt = async () => {
    if (!detail) return;
    const items = detail.items.map((item) => ({ group_key: item.group_key, ...(receiptDraft[item.group_key] || emptyCount()) }));
    if (!receiptReviewed || receiptStats.invalid) {
      message.error('请先核对数量并勾选确认');
      return;
    }
    if (!finalizeReceipt && receiptCounted === 0) {
      message.error('本批次请至少清点一件商品，或选择结束到货');
      return;
    }
    if (items.some((item, index) => item.received_quantity + item.damaged_quantity + item.missing_quantity > remainingFor(detail.items[index], detail))) {
      message.error('本批次清点数不能超过尚未清点的数量');
      return;
    }
    setWorkflowBusy(true);
    try {
      const response = await receivePurchaseList(detail.id, items, finalizeReceipt, receiptBatchId);
      try { window.localStorage.removeItem(receiptDraftKey(detail.id)); } catch { /* ignore */ }
      setDetail(response.data);
      setReceiptOpen(false);
      listsActionRef.current?.reload();
      candidatesActionRef.current?.reload();
      message.success(receiptMissing > 0 ? `已保存清点，少发 ${receiptMissing} 件已转补采；仍缺的商品可在清单详情生成补采清单` : finalizeReceipt ? '已结束本单到货；破损可生成补采清单' : '本批次已入库，可继续清点下一批');
      // 到货后如果还有富余库存，后端会自动补给其它等货订单（避免重复采购），这里给个提示
      const autoBound = response.data?.allocated_orders || [];
      if (autoBound.length) {
        const summary = Array.from(
          new Set(autoBound.map((item) => item.order_number || item.order_id)),
        ).join('、');
        message.success(
          `富余库存已自动分配给订单 ${summary}，这些订单不用再采购`,
          6,
        );
      }
    } catch (error) {
      message.error(getErrorMessage(error, '确认入库失败'));
    } finally {
      setWorkflowBusy(false);
    }
  };

  const openDetail = async (id: number, startReceipt = false) => {
    setDetailOpen(true);
    setDetailLoading(true);
    setDetail(null);
    setDetailSearch('');
    try {
      const response = await getPurchaseList(id);
      setDetail(response.data);
      if (startReceipt && ['ordered', 'partially_received'].includes(response.data.status)) openReceipt(response.data);
    } catch (error) {
      message.error(getErrorMessage(error, '获取采购清单失败'));
      setDetailOpen(false);
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get('view') === 'after_sale') setPurchaseView('after_sale');
    const purchaseId = Number(query.get('purchase_id'));
    if (purchaseId > 0) { setTab('history'); void openDetail(purchaseId); }
  }, []);

  const generate = async () => {
    if (!shopId || selectedOrderIds.length + selectedAfterSaleIds.length === 0) return;
    if (selectedOrderIds.length + selectedAfterSaleIds.length > selectionLimit) {
      message.warning(`单份清单最多选择 ${selectionLimit} 笔订单或售后单`);
      return;
    }
    setCreating(true);
    try {
      const response = await createPurchaseList({
        shop_id: shopId,
        order_ids: selectedOrderIds,
        after_sale_ids: selectedAfterSaleIds,
        name: name.trim() || undefined,
      });
      message.success(`已生成 ${response.data.total_quantity} 件商品的采购清单`);
      setSelectedOrderIds([]);
      setSelectedAfterSaleIds([]);
      setName('');
      setTab('history');
      setDetail(response.data);
      setDetailOpen(true);
      listsActionRef.current?.reload();
      candidatesActionRef.current?.reload();
    } catch (error) {
      message.error(getErrorMessage(error, '生成采购清单失败'));
    } finally {
      setCreating(false);
    }
  };

  const removePurchase = async (row: PurchaseListSummary) => {
    try {
      await deletePurchaseList(row.id);
      if (detail?.id === row.id) {
        setDetailOpen(false);
        setDetail(null);
      }
      listsActionRef.current?.reload();
      candidatesActionRef.current?.reload();
      message.success(row.status === 'ordered' ? '已撤销采购并释放待采购占用' : '采购清单已删除');
    } catch (error) {
      message.error(getErrorMessage(error, '删除采购清单失败'));
    }
  };

  const download = async (productIndex?: number, productTitle?: string) => {
    if (!detail) return;
    setExportingExcel(productIndex !== undefined ? String(productIndex) : 'all');
    try {
      const content = await downloadPurchaseListExcel(detail.id, productIndex);
      const url = URL.createObjectURL(content);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${[detail.name, productTitle].filter(Boolean).join('-').replace(/[\\/:*?"<>|]/g, '-')}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      message.error(getErrorMessage(error, 'Excel 导出失败'));
    } finally {
      setExportingExcel(null);
    }
  };

  const exportPdf = async (productIndex?: number, productTitle?: string) => {
    if (!detail) return;
    const key = productIndex ? String(productIndex) : 'all';
    setExportingPdf(key);
    try {
      const content = await downloadPurchaseListPdf(detail.id, productIndex);
      const url = URL.createObjectURL(content);
      const link = document.createElement('a');
      const filename = [detail.name, productTitle].filter(Boolean).join('-').replace(/[\\/:*?"<>|]/g, '-');
      link.href = url;
      link.download = `${filename}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      message.error(getErrorMessage(error, 'PDF 导出失败'));
    } finally {
      setExportingPdf(null);
    }
  };

  const orderColumns: ProColumns<PurchaseCandidate>[] = [
    {
      title: '订单号',
      dataIndex: 'order_number',
      width: 160,
      render: (_, row) => (
        <a onClick={() => history.push(`/orders/detail/${row.id}`)}>{row.order_number}</a>
      ),
    },
    {
      title: '客户',
      dataIndex: 'customer_name',
      hideInSearch: true,
      width: 160,
      render: (_, row) => row.customer_name || '-',
    },
    {
      title: '付款状态',
      dataIndex: 'financial_status',
      hideInSearch: true,
      width: 150,
      valueEnum: {
        paid: { text: '已付款', status: 'Success' },
        partially_paid: { text: '部分付款', status: 'Warning' },
        partially_refunded: { text: '部分退款', status: 'Warning' },
        refunded: { text: '已退款', status: 'Error' },
        pending: { text: '待付款', status: 'Default' },
      },
    },
    {
      title: '发货状态',
      dataIndex: 'fulfillment_status',
      hideInSearch: true,
      width: 140,
      valueEnum: {
        unfulfilled: { text: '未发货', status: 'Warning' },
        partial: { text: '部分发货', status: 'Processing' },
        fulfilled: { text: '已发货', status: 'Success' },
      },
    },
    {
      title: '采购备货',
      key: 'procurement_status',
      hideInSearch: true,
      width: 125,
      render: (_, row) => row.procurement_category === 'stocked'
        ? <Tag color="green">已备齐 · 可发货</Tag>
        : row.procurement_category === 'purchasing'
          ? <Tag color="blue">采购中</Tag>
          : row.procurement_category === 'replenish'
            ? <Tag color="orange">待补采</Tag>
            : <Tag>未采购</Tag>,
    },
    {
      title: '待采购件数',
      dataIndex: 'procurement_needed_quantity',
      hideInSearch: true,
      width: 120,
      align: 'right',
    },
    {
      title: '订单时间',
      dataIndex: 'order_date',
      hideInSearch: true,
      width: 180,
      render: (_, row) => formatBeijingTime(row.order_date || ''),
    },
    {
      title: '详情更新（1小时内复用）',
      dataIndex: 'shopify_details_synced_at',
      hideInSearch: true,
      width: 200,
      render: (_, row) => row.shopify_details_synced_at
        ? formatBeijingTime(row.shopify_details_synced_at) : '生成时更新',
    },
  ];

  const afterSaleColumns: ProColumns<AfterSalePurchaseCandidate>[] = [
    { title: '售后单', dataIndex: 'customer_reference', render: (_, row) => <a onClick={() => history.push(`/after-sales?after_sale_id=${row.id}`)}>{row.customer_reference}</a> },
    { title: '原订单', dataIndex: 'order_number', render: (_, row) => <a onClick={() => history.push(`/orders/detail/${row.order_id}`)}>{row.order_number}</a> },
    { title: '类型', dataIndex: 'kind', render: (value) => ({ resend: '原品补发', exchange: '换品补发', missing: '漏发补发' }[String(value)] || String(value)) },
    { title: '待采购件数', dataIndex: 'needed_quantity' },
    { title: '发起时间', dataIndex: 'created_at', render: (value) => formatBeijingTime(String(value)) },
  ];

  const listColumns: ProColumns<PurchaseListSummary>[] = [
    {
      title: '清单名称',
      dataIndex: 'name',
      width: 200,
      ellipsis: true,
      render: (_, row) => <Space size="small" wrap><a className="purchase-list-name" title={row.name} onClick={() => openDetail(row.id)}>{row.name}</a>{!!row.after_sale_ids?.length && <Tooltip title={(row.customer_references || []).join('、')}>
        <Tag color="purple">售后 {row.after_sale_ids.length}</Tag>
      </Tooltip>}</Space>,
    },
    {
      title: '到货进度',
      key: 'received_progress',
      width: 210,
      render: (_, row) => {
        const counts = countsOf(row);
        if (row.status === 'draft') return <Typography.Text type="secondary">尚未发起采购</Typography.Text>;
        return <div className="purchase-list-progress">
          <ProgressBar {...counts} total={row.total_quantity} />
          <Typography.Text>合格 {counts.received} / 计划 {row.total_quantity} 件</Typography.Text>
        </div>;
      },
    },
    {
      title: '异常 / 未到',
      key: 'exceptions',
      width: 180,
      render: (_, row) => {
        if (row.status === 'draft') return <Typography.Text type="secondary">—</Typography.Text>;
        const counts = countsOf(row);
        return <Space size={4} wrap>
          {counts.shortage > 0 && <Tag color="orange">缺货 {counts.shortage}</Tag>}
          {counts.damaged > 0 && <Tag color="volcano">破损 {counts.damaged}</Tag>}
          {counts.pending > 0 && <Tag>未到 {counts.pending}</Tag>}
          {!counts.shortage && !counts.damaged && !counts.pending && <Typography.Text type="secondary">全部到齐</Typography.Text>}
        </Space>;
      },
    },
    {
      title: '采购状态',
      dataIndex: 'status',
      width: 130,
      render: (_, row) => {
        const counts = countsOf(row);
        if (row.status === 'received') {
          const exception = counts.shortage + counts.damaged;
          return <Tag color={exception ? 'orange' : 'green'}>{exception ? '已结束 · 有异常' : '已清点入库'}</Tag>;
        }
        if (row.status === 'partially_received') return <Tag color="cyan">到货中</Tag>;
        if (row.status === 'ordered') return <Tag color="blue">采购中</Tag>;
        return <Tag>待发起</Tag>;
      },
    },
    {
      title: '生成时间',
      dataIndex: 'created_at',
      width: 180,
      responsive: ['lg'],
      render: (_, row) => formatBeijingTime(row.created_at),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 210,
      render: (_, row) => <Space size="small" wrap>
        <Button type="link" onClick={() => openDetail(row.id)}>查看</Button>
        {['ordered', 'partially_received'].includes(row.status) && <Button type="link" onClick={() => openDetail(row.id, true)}>清点到货</Button>}
        {(!row.after_sale_ids?.length || row.status === 'draft') && ['draft', 'ordered'].includes(row.status) && <Popconfirm
          title={row.status === 'ordered' ? '撤销这份采购清单？' : '删除这份采购清单？'}
          description={row.status === 'ordered' ? '撤销后会释放未入库的采购占用，订单可重新采购。' : '删除后无法恢复。'}
          okText={row.status === 'ordered' ? '确认撤销' : '确认删除'}
          cancelText="取消"
          onConfirm={() => removePurchase(row)}
        ><Button type="link" danger icon={<DeleteOutlined />}>{row.status === 'ordered' ? '撤销' : '删除'}</Button></Popconfirm>}
      </Space>,
    },
  ];

  const setReceiptValues = (key: string, patch: Partial<ReceiptCount>) => {
    setReceiptDraft((current) => ({ ...current, [key]: { ...(current[key] || emptyCount()), ...patch } }));
    setReceiptReviewed(false);
  };

  const renderReceiptColor = (product: PurchaseProduct, color: PurchaseColor) => (
    <section key={`${product.group_key}-${color.original_name || color.name}`} className="purchase-receipt-color">
      <div className="purchase-receipt-color__header">
        <div className="purchase-receipt-color__heading">
          <Typography.Text strong>{color.name}</Typography.Text>
          <Typography.Text type="secondary">{color.variants.length} 个型号 · 尚待清点 {color.variants.reduce((sum, item) => sum + remainingFor(item, detail), 0)} 件</Typography.Text>
        </div>
        <Space size="small" wrap>
          <Button size="small" onClick={() => fillReceiptItems(color.variants, true)}>所示型号全部到齐</Button>
          <Button size="small" onClick={() => fillReceiptItems(color.variants, false)}>清零</Button>
        </Space>
      </div>
      <div className="purchase-receipt-color__body">
        {color.image_url ? <Image
          src={color.image_url}
          alt={`${product.title} ${color.name}`}
          width={96}
          height={96}
          style={{ objectFit: 'contain', borderRadius: 8 }}
        /> : <div className="purchase-color__placeholder">暂无图片</div>}
        <div className="purchase-receipt-models">
          {color.variants.map((item) => {
            const value = receiptDraft[item.group_key] || emptyCount();
            const previous = detail?.receipt?.find((row) => row.group_key === item.group_key);
            const remaining = remainingFor(item, detail);
            const left = Math.max(remaining - value.received_quantity - value.damaged_quantity - value.missing_quantity, 0);
            const exceptionOpen = Boolean(receiptExceptions[item.group_key])
              || value.damaged_quantity > 0 || value.missing_quantity > 0;
            return <div key={item.group_key} className="purchase-receipt-model">
              <div className="purchase-receipt-model__name">
                <Typography.Text strong>{item.model_title || item.variant_title || '默认款'}</Typography.Text>
                <Typography.Text type="secondary">
                  采购 {item.quantity} · 此前已到 {previous?.received_quantity || 0} · 此前破损 {previous?.damaged_quantity || 0} · 本批可清点 {remaining}
                </Typography.Text>
              </div>
              <div className="purchase-receipt-model__primary">
                <label className="purchase-receipt-field purchase-receipt-field--main" htmlFor={`receipt-good-${encodeURIComponent(item.group_key)}`}>
                  实到
                  <InputNumber
                    id={`receipt-good-${encodeURIComponent(item.group_key)}`}
                    aria-label="实到件数"
                    min={0}
                    max={remaining}
                    precision={0}
                    value={value.received_quantity}
                    onChange={(number) => updateReceiptQuantity(item, 'received_quantity', number)}
                  />
                  件
                </label>
                <Space size={4} wrap>
                  <Button size="small" disabled={remaining === 0} onClick={() => setReceiptValues(item.group_key, {
                    ...emptyCount(),
                    received_quantity: Math.max(remaining - value.damaged_quantity - value.missing_quantity, 0),
                  })}>到齐</Button>
                  <Button size="small" disabled={remaining === 0} onClick={() => setReceiptValues(item.group_key, {
                    missing_quantity: Math.max(remaining - value.received_quantity - value.damaged_quantity, 0),
                  })}>全部少发</Button>
                  <Button size="small" type="link" onClick={() => setReceiptExceptions((current) => ({
                    ...current, [item.group_key]: !exceptionOpen,
                  }))}>{exceptionOpen ? '收起异常' : '记录破损/少发'}</Button>
                  <Button size="small" type="link" onClick={() => setReceiptValues(item.group_key, emptyCount())}>清空</Button>
                </Space>
              </div>
              {exceptionOpen && <div className="purchase-receipt-exception">
                <label className="purchase-receipt-field" htmlFor={`receipt-damaged-${encodeURIComponent(item.group_key)}`}>
                  破损
                  <InputNumber
                    id={`receipt-damaged-${encodeURIComponent(item.group_key)}`}
                    aria-label="破损件数"
                    min={0}
                    max={remaining}
                    precision={0}
                    value={value.damaged_quantity}
                    onChange={(number) => updateReceiptQuantity(item, 'damaged_quantity', number)}
                  />
                </label>
                <label className="purchase-receipt-field" htmlFor={`receipt-missing-${encodeURIComponent(item.group_key)}`}>
                  少发
                  <InputNumber
                    id={`receipt-missing-${encodeURIComponent(item.group_key)}`}
                    aria-label="少发件数"
                    min={0}
                    max={remaining}
                    precision={0}
                    value={value.missing_quantity}
                    onChange={(number) => updateReceiptQuantity(item, 'missing_quantity', number)}
                  />
                </label>
                <Typography.Text type="secondary">少发自动转补采；破损不计库存，可补采或报损。</Typography.Text>
              </div>}
              {left > 0 && <Typography.Text type="secondary" className="purchase-receipt-model__left">本批还剩 {left} 件未清点</Typography.Text>}
            </div>;
          })}
        </div>
      </div>
    </section>
  );

  const renderColor = (color: PurchaseColor) => (
    <section key={color.original_name || color.name} className="purchase-color">
      <div className="purchase-color__header">
        <Typography.Text strong>{color.name}</Typography.Text>
        <Typography.Text type="secondary">{color.variant_count} 个具体变体</Typography.Text>
        <Tag color="geekblue">小计 {color.quantity} 件</Tag>
      </div>
      <div className="purchase-color__body">
        {color.image_url ? (
          <Image
            src={color.image_url}
            alt={`${color.name} 商品图`}
            width={96}
            height={96}
            style={{ objectFit: 'cover', borderRadius: 8 }}
          />
        ) : <div className="purchase-color__placeholder">暂无图片</div>}
        <div className="purchase-models">
          {color.variants.map((item) => (
            <div key={item.group_key} className="purchase-model">
              <div className="purchase-model__description">
                <Typography.Text strong className="purchase-model__name">
                  {item.model_title || item.variant_title || '默认款'}
                </Typography.Text>
                {detail && detail.status !== 'draft' && detail.receipt?.filter((row) => row.group_key === item.group_key).map((row) => (
                  <Typography.Text key={row.group_key} type="secondary" className="purchase-model__receipt">
                    入库 {row.received_quantity} · 破损 {row.damaged_quantity} · 缺货 {row.confirmed_missing_quantity ?? (detail?.status === 'received' ? row.missing_quantity : 0)} · 待清点 {detail?.status === 'received' ? 0 : row.pending_quantity ?? row.missing_quantity}
                  </Typography.Text>
                ))}
              </div>
              <strong className="purchase-model__quantity">× {item.quantity}</strong>
            </div>
          ))}
        </div>
      </div>
    </section>
  );

  const renderProduct = (product: PurchaseProduct) => {
    const originalIndex = (detail?.products.findIndex((entry) => entry.group_key === product.group_key) ?? -1) + 1;
    return <Card key={product.group_key} size="small" className="purchase-product-card">
      <div className="purchase-product-card__header">
        <div className="purchase-product-card__title">
          <Typography.Text strong>{product.title}</Typography.Text>
          <Typography.Text type="secondary">
            {product.color_count} 个颜色组 · {product.variant_count} 个具体变体 · {product.order_numbers.length} 笔订单
          </Typography.Text>
        </div>
        <Button
          size="small"
          icon={<FilePdfOutlined />}
          loading={exportingPdf === String(originalIndex)}
          disabled={exportingPdf !== null && exportingPdf !== String(originalIndex)}
          onClick={() => exportPdf(originalIndex, product.title)}
        >
          导出此商品 PDF
        </Button>
        <Button
          size="small"
          icon={<DownloadOutlined />}
          loading={exportingExcel === String(originalIndex)}
          disabled={originalIndex < 1 || (exportingExcel !== null && exportingExcel !== String(originalIndex))}
          onClick={() => download(originalIndex, product.title)}
        >
          导出此商品 Excel
        </Button>
        <Tag color="blue">合计 {product.quantity} 件</Tag>
      </div>
      <div className="purchase-colors">{product.colors.map(renderColor)}</div>
    </Card>;
  };

  const detailCounts = detail ? countsOf(detail) : null;

  return (
    <PageContainer title="采购管理" subTitle="按商品 → 颜色 → 变体汇总采购"
      extra={shopId ? <Button onClick={() => setItemEditor({ purchase: null, shopId })}>手动创建采购单</Button> : undefined}>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'create',
            label: '生成采购清单',
            children: (
              <>
                {!shopId && (
                  <ShopRequiredNotice reason="采购清单按店铺生成，请先选择店铺。" />
                )}
                {shopId && (
                  <>
                    <Card size="small" className="purchase-selection-bar">
                      <Space wrap style={{ marginBottom: 12 }}>
                        {([
                          ['needs_purchase', '全部待采购'],
                          ['unprocured', '未采购'],
                          ['replenish', '待补采'],
                          ['purchased', '已采购总览'],
                          ['purchasing', '采购中'],
                          ['stocked', '已备齐'],
                          ['after_sale', '售后待采购'],
                        ] as const).map(([key, label]) => (
                          <Button key={key} type={purchaseView === key ? 'primary' : 'default'} onClick={() => setPurchaseView(key)}>
                            {label}（{categoryCounts[key]}）
                          </Button>
                        ))}
                      </Space>
                      <Space wrap>
                        <Typography.Text strong>已选 {selectedOrderIds.length} 笔普通订单、{selectedAfterSaleIds.length} 笔售后单</Typography.Text>
                        <Input
                          aria-label="采购清单名称"
                          placeholder="清单名称（可选）"
                          value={name}
                          maxLength={120}
                          onChange={(event) => setName(event.target.value)}
                          style={{ width: screens.sm ? 260 : '100%' }}
                        />
                        <Button
                          type="primary"
                          icon={<ShoppingCartOutlined />}
                          disabled={!selectedOrderIds.length && !selectedAfterSaleIds.length || selectedOrderIds.length + selectedAfterSaleIds.length > selectionLimit}
                          loading={creating}
                          onClick={generate}
                        >
                          {creating ? '核对缓存、批量更新并生成…' : '生成采购清单'}
                        </Button>
                        <Button
                          icon={<ClearOutlined />}
                          disabled={!selectedOrderIds.length && !selectedAfterSaleIds.length}
                          onClick={() => { setSelectedOrderIds([]); setSelectedAfterSaleIds([]); }}
                        >
                          清空选择
                        </Button>
                        <Button
                          disabled={!eligibleOrderIds.length && !eligibleAfterSaleIds.length || selectAllCount > selectionLimit}
                          onClick={() => {
                            if (purchaseView !== 'after_sale') setSelectedOrderIds(eligibleOrderIds);
                            if (purchaseView === 'after_sale' || purchaseView === 'needs_purchase') setSelectedAfterSaleIds(eligibleAfterSaleIds);
                          }}
                        >
                          全选待采购（{eligibleOrderIds.length + eligibleAfterSaleIds.length}）
                        </Button>
                      </Space>
                      {eligibleOrderIds.length + eligibleAfterSaleIds.length > selectionLimit && (
                        <Typography.Text type="secondary">
                          单份清单最多 {selectionLimit} 笔，请分批生成。
                        </Typography.Text>
                      )}
                    </Card>
                    {purchaseView === 'after_sale' ? <ProTable<AfterSalePurchaseCandidate>
                      rowKey="id" actionRef={candidatesActionRef} params={{ view: purchaseView }}
                      headerTitle="售后待采购" search={false} pagination={persistedPagination('purchase-after-sale', 20)}
                      columns={afterSaleColumns}
                      rowSelection={{ selectedRowKeys: selectedAfterSaleIds, preserveSelectedRowKeys: true,
                        onChange: (keys) => setSelectedAfterSaleIds(keys.map(Number)) }}
                      request={async (params) => {
                        const response = await listPurchaseCandidates({ shop_id: shopId, page: params.current, per_page: params.pageSize, view: 'after_sale' });
                        setCategoryCounts(response.data.category_counts);
                        setEligibleAfterSaleIds(response.data.all_after_sale_ids);
                        setEligibleOrderIds([]);
                        return { data: response.data.after_sales || [], total: response.data.pagination.total, success: response.success };
                      }}
                    /> : <ProTable<PurchaseCandidate>
                      rowKey="id"
                      actionRef={candidatesActionRef}
                      params={{ view: purchaseView }}
                      headerTitle="订单采购状态"
                      columns={orderColumns}
                      search={{ labelWidth: 'auto' }}
                      pagination={{ ...persistedPagination('purchase-order-picker', 20), showSizeChanger: true }}
                      scroll={{ x: 820 }}
                      rowSelection={{
                        selectedRowKeys: selectedOrderIds,
                        preserveSelectedRowKeys: true,
                        getCheckboxProps: (row) => ({ disabled: row.procurement_needed_quantity <= 0 }),
                        onChange: (keys) => setSelectedOrderIds(keys.map(Number)),
                      }}
                      request={async (params) => {
                        const response = await listPurchaseCandidates({
                          shop_id: shopId,
                          page: params.current,
                          per_page: params.pageSize,
                          search: String(params.order_number || '').trim() || undefined,
                          view: purchaseView,
                        });
                        setEligibleOrderIds(response.data.all_order_ids);
                        setEligibleAfterSaleIds(response.data.all_after_sale_ids);
                        setVisibleAfterSales(response.data.after_sales || []);
                        setCategoryCounts(response.data.category_counts);
                        setSelectionLimit(response.data.selection_limit);
                        return {
                          data: response.data.orders,
                          total: response.data.pagination.total,
                          success: response.success,
                        };
                      }}
                    />}
                    {purchaseView === 'needs_purchase' && visibleAfterSales.length > 0 && <ProTable<AfterSalePurchaseCandidate>
                      rowKey="id" headerTitle="售后待采购（可与上方订单一起勾选）" search={false}
                      options={false} pagination={{ pageSize: 20 }} dataSource={visibleAfterSales} columns={afterSaleColumns}
                      rowSelection={{ selectedRowKeys: selectedAfterSaleIds, preserveSelectedRowKeys: true,
                        onChange: (keys) => setSelectedAfterSaleIds(keys.map(Number)) }}
                    />}
                  </>
                )}
              </>
            ),
          },
          {
            key: 'history',
            label: '已生成清单',
            children: (
              <ProTable<PurchaseListSummary>
                rowKey="id"
                actionRef={listsActionRef}
                headerTitle="采购清单记录"
                search={false}
                columns={listColumns}
                tableLayout="fixed"
                pagination={{ ...persistedPagination('purchase-lists', 10), showSizeChanger: true }}
                request={async (params) => {
                  const response = await listPurchaseLists({ page: params.current, per_page: params.pageSize });
                  return {
                    data: response.data.purchase_lists,
                    total: response.data.pagination.total,
                    success: response.success,
                  };
                }}
              />
            ),
          },
        ]}
      />
      <Drawer
        title={detail?.name || '采购清单'}
        open={detailOpen}
        width={screens.md ? 960 : '100%'}
        onClose={() => setDetailOpen(false)}
        extra={detail && (
          <Space size="small" wrap>
            {(!detail.after_sale_ids?.length || detail.status === 'draft') && ['draft', 'ordered'].includes(detail.status) && <Popconfirm
              title={detail.status === 'ordered' ? '撤销这份采购清单？' : '删除这份采购清单？'}
              description={detail.status === 'ordered' ? '撤销后会释放未入库的采购占用。' : '删除后无法恢复。'}
              onConfirm={() => removePurchase(detail)}
              okText="确认"
              cancelText="取消"
            ><Button danger icon={<DeleteOutlined />}>{screens.sm ? (detail.status === 'ordered' ? '撤销采购' : '删除') : ''}</Button></Popconfirm>}
            <Button
              icon={<FilePdfOutlined />}
              loading={exportingPdf === 'all'}
              disabled={exportingPdf !== null && exportingPdf !== 'all'}
              onClick={() => exportPdf()}
            >
              {screens.sm ? '导出整份 PDF' : 'PDF'}
            </Button>
            <Button icon={<DownloadOutlined />} loading={exportingExcel === 'all'} disabled={exportingExcel !== null && exportingExcel !== 'all'} onClick={() => download()}>
              {screens.sm ? '导出 Excel' : 'Excel'}
            </Button>
          </Space>
        )}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>
        ) : detail ? (
          <>
            <div className="purchase-summary">
              <Statistic title="订单数" value={detail.order_count} />
              <Statistic title="商品大类" value={detail.product_count} />
              <Statistic title="采购件数" value={detail.total_quantity} />
              <Statistic title="合格入库" value={detailCounts?.received ?? 0} />
            </div>
            {detailCounts && <div className="purchase-detail-breakdown">
              <Tag color="green">合格 {detailCounts.received}</Tag>
              <Tag color="volcano">破损 {detailCounts.damaged}</Tag>
              <Tag color="orange">缺货 {detailCounts.shortage}</Tag>
              <Tag>未到 {detailCounts.pending}</Tag>
            </div>}
            <Typography.Text type="secondary">
              {detail.shop_name || '当前店铺'} · {detail.color_count} 个颜色组 · {detail.item_count} 个具体变体 · 创建于 {formatBeijingTime(detail.created_at)}
            </Typography.Text>
            <PurchaseOrderLinks purchase={detail} />
            <div className="purchase-workflow-bar">
              {detail.can_edit_items && <Button disabled={workflowBusy} onClick={() => setItemEditor({ purchase: detail, shopId: detail.shop_id })}>添加商品 / 编辑数量</Button>}
              <Tag color={detail.status === 'received'
                ? ((detailCounts?.shortage || 0) + (detailCounts?.damaged || 0) ? 'orange' : 'green')
                : detail.status === 'partially_received' ? 'cyan' : detail.status === 'ordered' ? 'blue' : 'default'}>
                {detail.status === 'received'
                  ? ((detailCounts?.shortage || 0) + (detailCounts?.damaged || 0) ? '已结束 · 有异常' : '已清点入库')
                  : detail.status === 'partially_received' ? '分批到货中' : detail.status === 'ordered' ? '采购中' : '待发起采购'}
              </Tag>
              {detail.status === 'draft' && <Popconfirm
                title="确认发起采购？"
                description="确认后会占用对应订单的待采购数量；请先核对清单。"
                onConfirm={confirmProcurement}
              ><Button type="primary" loading={workflowBusy}>确认发起采购</Button></Popconfirm>}
              {['ordered', 'partially_received'].includes(detail.status) && <Button type="primary" loading={workflowBusy} onClick={() => openReceipt()}>
                {detail.status === 'partially_received' ? '继续清点到货' : '清点并确认入库'}
              </Button>}
              {['received', 'partially_received'].includes(detail.status) && <Typography.Text type="secondary">
                {detail.receipt?.reduce((sum, row) => sum + row.received_quantity, 0) || 0} 件合格入库，
                {detail.receipt?.reduce((sum, row) => sum + row.damaged_quantity, 0) || 0} 件破损，
                {detail.receipt?.reduce((sum, row) => sum + (row.confirmed_missing_quantity ?? (detail.status === 'received' ? row.missing_quantity : 0)), 0) || 0} 件缺货，
                {detail.status === 'received' ? 0 : detail.receipt?.reduce((sum, row) => sum + (row.pending_quantity ?? row.missing_quantity), 0) || 0} 件待清点
              </Typography.Text>}
            </div>
            {detail.status !== 'draft' && detailCounts && <div className="purchase-detail-progress">
              <div className="purchase-detail-progress__text">
                <Typography.Text>合格 {detailCounts.received} · 破损 {detailCounts.damaged} · 缺货 {detailCounts.shortage} · 未到 {detailCounts.pending}</Typography.Text>
                <Typography.Text type="secondary">计划 {detail.total_quantity} 件</Typography.Text>
              </div>
              <ProgressBar {...detailCounts} total={detail.total_quantity} />
            </div>}
            <Alert
              showIcon
              type={detail.status === 'received' ? 'success' : 'info'}
              message={detail.status === 'draft' ? '先核对商品、颜色和型号，确认后再发起采购。' : detail.status === 'ordered' ? '货到后先填实到，破损/少发另记。' : detail.status === 'partially_received' ? '未到商品可下一批继续清点。' : '清单已结束到货；缺货或破损可直接生成补采清单。'}
              style={{ marginTop: 14 }}
            />
            {detailCounts && ['received', 'partially_received'].includes(detail.status)
              && detailCounts.shortage + detailCounts.damaged > 0 && <Alert
                type="warning"
                showIcon
                style={{ marginTop: 12 }}
                message={`有 ${detailCounts.shortage + detailCounts.damaged} 件需要补采（缺货 ${detailCounts.shortage}、破损 ${detailCounts.damaged}）`}
                description={detail.replenishment_ids?.length
                  ? '已生成，发起后才占用。'
                  : '按仍缺数量生成草稿。'}
                action={detail.replenishment_ids?.length
                  ? <Button onClick={() => openDetail(detail.replenishment_ids![0])}>查看补采单 #{detail.replenishment_ids[0]}</Button>
                  : <Button type="primary" loading={replenishBusy} onClick={replenish}>生成补采清单</Button>}
              />}
            <div className="purchase-detail-toolbar">
              <Input
                prefix={<SearchOutlined />}
                aria-label="在采购单中搜索商品、颜色、型号或 SKU"
                placeholder="搜索商品、颜色、型号或 SKU"
                value={detailSearch}
                allowClear
                onChange={(event) => setDetailSearch(event.target.value)}
              />
              <Typography.Text type="secondary">显示 {visibleDetailProducts.length} / {detail.product_count} 个商品大类</Typography.Text>
            </div>
            <div className="purchase-items">{visibleDetailProducts.length ? visibleDetailProducts.map(renderProduct) : <Empty description="没有匹配的商品或型号" />}</div>
          </>
        ) : <Empty description="暂无清单" />}
      </Drawer>
      {itemEditor && <ItemEditor purchase={itemEditor.purchase} shopId={itemEditor.shopId} onClose={() => setItemEditor(null)} onSaved={updated => {
        setItemEditor(null); setDetail(updated); setDetailOpen(true); setTab('history');
        listsActionRef.current?.reload(); candidatesActionRef.current?.reload();
      }} />}
      <Modal
        title={`清点到货 · ${detail?.name || '采购清单'}`}
        open={receiptOpen}
        centered
        className="purchase-receipt-modal"
        width={screens.md ? 1040 : 'calc(100vw - 24px)'}
        confirmLoading={workflowBusy}
        onOk={confirmReceipt}
        onCancel={() => setReceiptOpen(false)}
        okText={finalizeReceipt ? '结束到货并入库' : '保存本批入库'}
        okButtonProps={{ disabled: !receiptReviewed || receiptStats.invalid > 0 || (!finalizeReceipt && receiptCounted === 0) }}
        cancelButtonProps={{ disabled: workflowBusy }}
        closable={!workflowBusy}
        maskClosable={!workflowBusy}
        destroyOnHidden
        footer={(_, { OkBtn, CancelBtn }) => <div className="purchase-receipt-footer">
          <Checkbox checked={receiptReviewed} onChange={(event) => setReceiptReviewed(event.target.checked)}>
            我已核对：实到 {receiptStats.received} 件、破损 {receiptStats.damaged} 件、少发 {receiptMissing} 件{!finalizeReceipt && `、留待下一批 ${receiptUncounted} 件`}
          </Checkbox>
          <Space><CancelBtn /><OkBtn /></Space>
        </div>}
      >
        <Alert type="info" showIcon message="只填实到；破损/少发点「记录破损/少发」再填。" style={{ marginBottom: 16 }} />
        {receiptRestoredAt && <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`已恢复 ${receiptRestoredAt} 未提交的清点草稿`}
          action={<Button size="small" onClick={clearReceiptDraft}>清空重填</Button>}
        />}
        <div className="purchase-receipt-overview" aria-live="polite">
          <Statistic title="本单待清点" value={receiptStats.remaining} suffix="件" />
          <Statistic title="本批实到" value={receiptStats.received} suffix="件" />
          <Statistic title="本批破损" value={receiptStats.damaged} suffix="件" />
          <Statistic title="少发转补采" value={receiptMissing} suffix="件" />
          {!finalizeReceipt && <Statistic title="留待下一批" value={receiptUncounted} suffix="件" />}
        </div>
        <div className="purchase-receipt-toolbar">
          <Input
            prefix={<SearchOutlined />}
            aria-label="搜索商品、颜色、型号或 SKU"
            placeholder="搜索商品、颜色、型号或 SKU"
            value={receiptSearch}
            allowClear
            onChange={(event) => setReceiptSearch(event.target.value)}
          />
          <Checkbox checked={receiptShowPendingOnly} onChange={(event) => setReceiptShowPendingOnly(event.target.checked)}>只看尚待清点</Checkbox>
          <Popconfirm title="把所有待清点商品记为实到？" description={`将把 ${receiptStats.remaining} 件记为实到，覆盖已填破损/少发。`} onConfirm={() => detail && fillReceiptItems(detail.items, true)} okText="确认填满" cancelText="取消">
            <Button>全部到齐</Button>
          </Popconfirm>
          <Button onClick={() => detail && fillReceiptItems(detail.items, false)}>全部清零</Button>
        </div>
        <div className="purchase-receipt-scroll">
          {visibleReceiptProducts.length ? visibleReceiptProducts.map((product) => <section key={product.group_key} className="purchase-receipt-product">
            <Typography.Text strong className="purchase-receipt-product__title">{product.title}</Typography.Text>
            {product.colors.map((color) => renderReceiptColor(product, color))}
          </section>) : <Empty description={receiptSearch ? '没有匹配的商品或型号' : '没有待清点的商品'} />}
        </div>
        <Checkbox checked={finalizeReceipt} onChange={(event) => { setFinalizeReceipt(event.target.checked); setReceiptReviewed(false); }} className="purchase-receipt-finalize">
          这是最后一批，结束本采购单到货；剩余未清点商品按少发处理
        </Checkbox>
        {finalizeReceipt && receiptUncounted > 0 && <Alert type="warning" showIcon message={`${receiptUncounted} 件未清点将记为少发；结束后不可再入库。`} className="purchase-receipt-warning" />}
      </Modal>
    </PageContainer>
  );
};

export default Purchases;
