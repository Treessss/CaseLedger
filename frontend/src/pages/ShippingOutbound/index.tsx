import {
  ClearOutlined,
  InboxOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RollbackOutlined,
  ScanOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  message,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { InputRef } from 'antd';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { PackingItems, RecipientInfo, ShopRequiredNotice } from '@/components';
import PackingControls from '@/components/PackingControls';
import FailureRecords from './FailureRecords';
import {
  listRecentOutbound,
  lookupPackingList,
  scanShippingLabel,
  undoOutbound,
  type OutboundRecord,
  type PackingList,
} from '@/services/shipping/api';
import { getErrorData, getErrorMessage } from '@/utils/error';
import { isPackingScannerMode, setPackingFocusMode, setPackingScannerMode } from '@/utils/layoutPrefs';
import { isCompletePackingLabel, isDuplicatePackingScan, isOutboundLabel, normalizePackingScan, type PackingScanAttempt } from '@/utils/packingScan';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';
import { formatBeijingTime } from '@/utils/timezone';
import { readPagination, savePagination } from '@/utils/pagination';

/** 太短的编号不自动查，避免边打字边发请求。 */
const MIN_AUTO_LENGTH = 3;
const DEBOUNCE_MS = 350;
const SCANNER_IDLE_MS = 120;
const MAX_SCAN_QUEUE = 20;

const STATUS_TEXT: Record<string, string> = {
  pending: '待出库',
  shipped: '已出库',
  pending_purchase: '待采购',
  purchasing: '采购中',
  ready: '待出库',
};

const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  shipped: 'green',
  pending_purchase: 'orange',
  purchasing: 'blue',
  ready: 'green',
};

/** 现场基本不看屏幕，用声音区分成功/失败；浏览器不授权音频时静默跳过。 */
function beep(ok: boolean): void {
  try {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.value = ok ? 1046 : 240;
    gain.gain.value = 0.06;
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start();
    oscillator.stop(ctx.currentTime + (ok ? 0.12 : 0.32));
    oscillator.onended = () => {
      void ctx.close();
    };
  } catch {
    // 静音环境或浏览器限制：忽略即可
  }
}

/**
 * 扫码出库：仓库专用的出库台。
 *
 * 和装箱核对一样是「一个输入框 + 扫到谁显示谁」，区别在于这里负责真正的出库：
 *   1. 扫描面单：核对出库条件后立即完成出库；
 *   2. 瞬间重复扫描忽略，已扫码的包裹不会重复出库；
 *   3. 扫错了就在页面下方「最近出库」里点撤回，订单直接退回待出库。
 *
 * 出库是本地仓储动作，不会改 Shopify 的发货状态——那一步仍在发货中心单独做。
 */
const ShippingOutbound: React.FC = () => {
  const selectedShop = getSelectedShopId();
  const shopId = selectedShop === ALL_SHOPS ? null : Number(selectedShop);
  const { initialState, setInitialState } = useModel('@@initialState');
  const menuHidden = Boolean(initialState?.packingFocus);
  const toggleMenu = () => {
    const next = !menuHidden;
    setPackingFocusMode(next);
    setInitialState((prev) => ({ ...prev, packingFocus: next }));
  };

  const [code, setCode] = useState('');
  const [scannerMode, setScannerMode] = useState(isPackingScannerMode);
  const [todayQuantity, setTodayQuantity] = useState<number | null>(null);
  const [todayOrders, setTodayOrders] = useState<number | null>(null);
  const [result, setResult] = useState<PackingList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [lastCode, setLastCode] = useState('');
  const [shipping, setShipping] = useState(false);
  const [queuedScans, setQueuedScans] = useState(0);
  const [scanFeedback, setScanFeedback] = useState<{code: string; detail: string; duplicate?: boolean} | null>(null);
  const [recent, setRecent] = useState<OutboundRecord[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const [recentQuery, setRecentQuery] = useState(() => ({...readPagination('outbound-history', 20), search: ''}));
  const [recentTotal, setRecentTotal] = useState(0);
  const [recentSearch, setRecentSearch] = useState('');
  const [recentError, setRecentError] = useState('');
  const recentQueryRef = useRef(recentQuery);
  recentQueryRef.current = recentQuery;
  const recentRequestSeq = useRef(0);
  const recentShopRef = useRef(shopId);
  const [undoTarget, setUndoTarget] = useState<OutboundRecord | null>(null);
  const [undoReason, setUndoReason] = useState('');
  const [undoBusy, setUndoBusy] = useState(false);

  const inputRef = useRef<InputRef>(null);
  const requestSeq = useRef(0);
  const codeRef = useRef('');
  const lastAttempt = useRef<PackingScanAttempt | null>(null);
  const shippingRef = useRef(false);
  const activeShopRef = useRef<number | null>(null);
  const scanQueue = useRef<string[]>([]);
  const pendingCodes = useRef(new Set<string>());
  const recentRefreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    scanQueue.current = [];
    if (recentRefreshTimer.current) clearTimeout(recentRefreshTimer.current);
  }, []);
  useEffect(() => {
    if (!shipping) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeLeaving);
    return () => window.removeEventListener('beforeunload', warnBeforeLeaving);
  }, [shipping]);
  // 出库动作通过 ref 调用：runLookup 要先定义，shipByCode 又要用 runLookup，
  // 用 ref 打断这个循环依赖，比把两个 useCallback 互相塞进依赖数组干净。
  const shipRef = useRef<((code: string) => void) | null>(null);

  const loadRecent = useCallback(async () => {
    if (!shopId) return;
    const seq = ++recentRequestSeq.current;
    const query = recentQueryRef.current;
    setRecentLoading(true);
    setRecentError('');
    try {
      const data = (await listRecentOutbound(shopId, {
        page: query.current, per_page: query.pageSize, search: query.search,
      })).data;
      if (seq !== recentRequestSeq.current || Number(getSelectedShopId()) !== shopId) return;
      setRecent(data?.events || []);
      setRecentTotal(data?.pagination?.total ?? data?.events?.length ?? 0);
      if (data?.pagination && data.pagination.page !== query.current) {
        const page = data.pagination.page;
        savePagination('outbound-history', page, query.pageSize);
        setRecentQuery((prev) => ({...prev, current: page}));
      }
      setTodayQuantity(data?.today?.quantity ?? null);
      setTodayOrders(data?.today?.total_orders ?? null);
    } catch (err) {
      if (seq !== recentRequestSeq.current) return;
      setRecent([]);
      setRecentTotal(0);
      setRecentError(getErrorMessage(err, '获取出库记录失败，请重试'));
      setTodayQuantity(null);
      setTodayOrders(null);
      // 列表拉不到不影响扫码出库本身
    } finally {
      if (seq === recentRequestSeq.current) setRecentLoading(false);
    }
  }, [shopId]);

  const toggleInputMode = () => {
    const next = !scannerMode;
    inputRef.current?.blur();
    setPackingScannerMode(next);
    setScannerMode(next);
    codeRef.current = '';
    setCode('');
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const onScannerKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (!scannerMode || !shopId || event.ctrlKey || event.metaKey || event.altKey || event.nativeEvent.isComposing) return;
    const key = event.key;
    if (key === 'Enter' || key === 'Tab') {
      event.preventDefault();
      void runLookup(codeRef.current);
      return;
    }
    let value = codeRef.current;
    if (key === 'Backspace') value = value.slice(0, -1);
    else if (key === 'Escape') value = '';
    else if (key.length === 1) value += key;
    else return;
    event.preventDefault();
    codeRef.current = value.slice(-256).toUpperCase();
    setCode(codeRef.current);
    setError('');
    if (isCompletePackingLabel(codeRef.current)) void runLookup(codeRef.current);
  };

  useEffect(() => {
    setTodayQuantity(null);
    setTodayOrders(null);
    if (recentShopRef.current !== shopId) {
      recentShopRef.current = shopId;
      setRecentSearch('');
      setRecentQuery({...readPagination('outbound-history', 20), search: ''});
    }
    return () => { recentRequestSeq.current += 1; };
  }, [loadRecent]);

  useEffect(() => { void loadRecent(); }, [loadRecent, recentQuery]);

  const runLookup = useCallback(
    async (raw: string, refresh = false) => {
      const value = normalizePackingScan(raw);
      if (!shopId || !value) return;

      // Consume before any network request, even when lookup fails or a scan repeats.
      if (!refresh && codeRef.current === raw) {
        codeRef.current = '';
        setCode('');
      }
      if (!refresh) {
        const attempt = { shopId, value, startedAt: Date.now() };
        if (isDuplicatePackingScan(lastAttempt.current, attempt)) return;
        lastAttempt.current = attempt;
        if (isOutboundLabel(value)) {
          // The scan endpoint already validates labels, stock, holds and duplicates atomically.
          // Avoid a redundant packing/stock lookup before that authoritative transaction.
          shipRef.current?.(value);
          return;
        }
        if (shippingRef.current) {
          message.warning('正在出库，请等待完成后再扫描下一张面单');
          return;
        }
      }
      setLastCode(value);

      const seq = requestSeq.current + 1;
      requestSeq.current = seq;
      setLoading(true);
      setError('');

      try {
        const response = await lookupPackingList(shopId, value);
        if (seq !== requestSeq.current) return;
        const data = response.data;
        setResult(data);
        setLastCode(value);
        const alreadyScanned = Boolean(data.outbound?.already_scanned);
        if (!refresh && data.outbound?.ready && data.outbound.code_matches_label && !alreadyScanned) {
          shipRef.current?.(value);
        }
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setResult(null);
        setError(getErrorMessage(err, '查询失败'));
      } finally {
        if (seq === requestSeq.current) {
          setLoading(false);
          window.setTimeout(() => inputRef.current?.focus(), 0);
        }
      }
    },
    [shopId],
  );

  const shipByCode = useCallback(
    async (value: string) => {
      if (!shopId || !value || pendingCodes.current.has(value)) return;
      if (shippingRef.current && activeShopRef.current !== shopId) {
        message.warning('上一店铺的出库正在完成，请稍后扫描');
        return;
      }
      if (scanQueue.current.length >= MAX_SCAN_QUEUE) {
        setScanFeedback({code: value, detail: '扫码队列已满，请稍后重新扫描；本次未提交出库'});
        beep(false);
        return;
      }
      pendingCodes.current.add(value);
      scanQueue.current.push(value);
      setQueuedScans(scanQueue.current.length);
      if (shippingRef.current) return;
      shippingRef.current = true;
      activeShopRef.current = shopId;
      setShipping(true);
      try {
        while (scanQueue.current.length && Number(getSelectedShopId()) === shopId) {
          const currentCode = scanQueue.current.shift() as string;
          setQueuedScans(scanQueue.current.length);
          requestSeq.current += 1;
          setLastCode(currentCode);
          setError('');
          setResult(null);
          setLoading(true);
          let confirmed = false;
          // 重复出库不写异常记录，但现场仍要看到商品图片和收货地址，所以补查一次详情。
          let needsDetail = false;
          try {
            const response = await scanShippingLabel(shopId, currentCode);
            if (Number(getSelectedShopId()) !== shopId) break;
            const data = response.data;
            confirmed = true;
            beep(true);
            setScanFeedback(null);
            message.success({key: 'outbound-scan-success', content: data.warehouse_status === 'shipped'
              ? `${data.order_number} 已出库`
              : `${data.order_number} 已扫 ${data.scanned_parcel_count}/${data.expected_parcel_count} 个包裹，还有包裹没扫`, duration: 3});
          } catch (err) {
            if (Number(getSelectedShopId()) !== shopId) break;
            const detail = getErrorMessage(err, '扫码出库失败；请核对店铺与面单');
            const failure = getErrorData<{scan_category?: string; scan_failure_recorded?: boolean}>(err);
            const duplicate = failure?.scan_category === 'duplicate' || ['该包裹已扫码，禁止重复出库', '该售后包裹已扫码出库，请勿重复操作', '订单已完成出库，请勿重复扫码'].includes(detail);
            if (!duplicate) beep(false);
            setScanFeedback({code: currentCode, duplicate, detail: duplicate ? '该包裹已出库，本次重复扫码已忽略（不记录异常）'
              : `${detail}；${failure?.scan_failure_recorded ? '已保存到出库异常记录' : '请核对出库状态；本次未确认异常记录是否已保存'}`});
            if (lastAttempt.current?.value === currentCode) lastAttempt.current = null;
            // 已经出库的包裹再扫一次不会出库，但依然是有效的核对场景：把清单和地址查回来。
            if (duplicate) needsDetail = true;
          } finally {
            pendingCodes.current.delete(currentCode);
          }
          if (!scanQueue.current.length && Number(getSelectedShopId()) === shopId) {
            // Rendering and counters do not hold the scanner lock or delay the next label.
            if (confirmed || needsDetail) void runLookup(currentCode, true);
            else setLoading(false);
            if (recentRefreshTimer.current) clearTimeout(recentRefreshTimer.current);
            recentRefreshTimer.current = setTimeout(() => {void loadRecent();}, 200);
          }
        }
      } finally {
        shippingRef.current = false;
        activeShopRef.current = null;
        setShipping(false);
        setQueuedScans(scanQueue.current.length);
        inputRef.current?.focus();
      }
    },
    [shopId, runLookup, loadRecent],
  );

  useEffect(() => {
    shipRef.current = shipByCode;
  }, [shipByCode]);

  useEffect(() => {
    const value = code;
    if (!shopId || value.length < MIN_AUTO_LENGTH) return;
    const timer = window.setTimeout(() => {
      if (codeRef.current === value) void runLookup(value);
    }, scannerMode ? SCANNER_IDLE_MS : DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [code, shopId, runLookup, scannerMode]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    requestSeq.current += 1;
    lastAttempt.current = null;
    scanQueue.current = [];
    pendingCodes.current.clear();
    setQueuedScans(0);
    setScanFeedback(null);
    if (recentRefreshTimer.current) clearTimeout(recentRefreshTimer.current);
    codeRef.current = '';
    setCode('');
    setLastCode('');
    setResult(null);
    setError('');
    setLoading(false);
  }, [shopId]);

  const reset = () => {
    requestSeq.current += 1;
    lastAttempt.current = null;
    codeRef.current = '';
    setCode('');
    setLastCode('');
    setResult(null);
    setError('');
    setLoading(false);
    inputRef.current?.focus();
    setScanFeedback(null);
  };

  const submitUndo = async () => {
    if (!shopId || !undoTarget) return;
    setUndoBusy(true);
    try {
      await undoOutbound({
        shop_id: shopId,
        order_id: undoTarget.kind === 'order' ? undoTarget.order_id : undefined,
        after_sale_id:
          undoTarget.kind === 'after_sale' ? undoTarget.after_sale_id ?? undefined : undefined,
        reason: undoReason.trim() || undefined,
      });
      message.success(`${undoTarget.title} 已撤回出库，订单回到待出库`);
      setUndoTarget(null);
      setUndoReason('');
      if (lastCode) await runLookup(lastCode, true);
      await loadRecent();
    } catch (err) {
      message.error(getErrorMessage(err, '撤回失败'), 6);
    } finally {
      setUndoBusy(false);
    }
  };

  const items = result?.items || [];
  const outbound = result?.outbound;
  const statusLabel = result ? STATUS_TEXT[result.status] || result.status : '';
  const statusColor = result ? STATUS_COLOR[result.status] || 'blue' : 'blue';

  const hintBanner = () => {
    if (!result || !outbound) return null;
    if (outbound.ready) {
      return (
        <Alert
          type="success"
          showIcon
          message={shipping ? '正在出库，请等待完成' : '商品已备齐'}
          description="扫面单出库；可在下方撤回"
        />
      );
    }
    if (outbound.already_scanned || result.status === 'shipped') {
      const complete =
        result.status === 'shipped' ||
        outbound.scanned_parcel_count >= outbound.expected_parcel_count;
      return (
        <Alert
          type="info"
          showIcon
          message={complete ? '这单已经出库了' : '这张面单已经扫过'}
          description={
            complete
              ? '扫错可到下方「最近出库」撤回'
              : `已扫 ${outbound.scanned_parcel_count}/${outbound.expected_parcel_count} 个包裹，把剩下的面单扫完即可完成出库。`
          }
        />
      );
    }
    if (outbound.blockers.length) {
      return (
        <Alert
          type="warning"
          showIcon
          message="暂时不能出库"
          description={outbound.blockers.join('；')}
        />
      );
    }
    if (!outbound.code_matches_label) {
      return (
        <Alert
          type="info"
          showIcon
          message="这不是出库用的面单编号"
          description="出库请扫面单条码"
        />
      );
    }
    return null;
  };

  const columns = [
    {
      title: '单号',
      dataIndex: 'title',
      render: (_: unknown, row: OutboundRecord) => (
        <Space size={6}>
          <Typography.Text strong>{row.title}</Typography.Text>
          {row.kind === 'after_sale' && <Tag color="purple">售后</Tag>}
        </Space>
      ),
    },
    {
      title: '客户',
      dataIndex: 'subtitle',
      render: (value: string) => value || '—',
    },
    {
      title: '面单编号',
      dataIndex: 'code',
      render: (value: string) => value || '—',
    },
    {
      title: '出库时间（北京时间）',
      dataIndex: 'created_at',
      width: 190,
      render: (value: string) => formatBeijingTime(value || ''),
    },
    {
      title: '操作',
      width: 110,
      render: (_: unknown, row: OutboundRecord) =>
        row.undoable ? (
          <Button
            size="small"
            danger
            icon={<RollbackOutlined />}
            onClick={() => {
              setUndoReason('');
              setUndoTarget(row);
            }}
          >
            撤回
          </Button>
        ) : (
          <Tag>已撤回</Tag>
        ),
    },
  ];

  if (!shopId) {
    return (
      <PageContainer title="扫码出库" subTitle="扫描面单一次即出库，出库后可撤回">
        <ShopRequiredNotice reason="出库按店铺隔离" />
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title="扫码出库"
      subTitle="扫描面单一次即出库；出库后可撤回"
      extra={[
        <FailureRecords key={`failures:${shopId}`} shopId={shopId} onClosed={() => inputRef.current?.focus()} />,
        <Button key="menu" icon={menuHidden ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={toggleMenu}>
          {menuHidden ? '显示菜单' : '隐藏菜单'}
        </Button>,
        <Button key="mode" onClick={toggleInputMode} disabled={shipping}>
          {scannerMode ? '切换手动输入' : '切换扫码枪'}
        </Button>,
        <Button key="clear" icon={<ClearOutlined />} onClick={reset} disabled={shipping || (!code && !result && !scanFeedback && !error)}>
          清空
        </Button>,
      ]}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div className="packing-total-quantity" style={{ marginLeft: 0 }}>
          今日已出库 <strong>{todayOrders === null ? '—' : todayOrders}</strong> 单
          <span style={{ marginLeft: 12 }}>商品 <strong>{todayQuantity === null ? '—' : todayQuantity}</strong> 件</span>
          <span style={{ marginLeft: 12, fontSize: 12 }}>北京时间 · 当前店铺 · 含售后补发</span>
        </div>
        <Card size="small" styles={{ body: { padding: '10px 12px' } }}>
          <Input
            ref={inputRef}
            allowClear
            size="large"
            prefix={<ScanOutlined style={{ color: '#cbd5e1' }} />}
            placeholder="扫描面单条码"
            value={code}
            readOnly={scannerMode}
            inputMode={scannerMode ? 'none' : 'text'}
            aria-label={scannerMode ? '扫码枪输入（不弹键盘）' : '手动输入订单号或物流单号'}
            onKeyDown={onScannerKeyDown}
            onChange={(event) => {
              const value = event.target.value.toUpperCase();
              codeRef.current = value;
              setCode(value);
              setError('');
              if (isCompletePackingLabel(value)) void runLookup(value);
            }}
            onPressEnter={() => void runLookup(codeRef.current)}
          />
          <div style={{ marginTop: 4, color: '#94a3b8', fontSize: 12 }}>
            扫面单即出库；扫错可在下方撤回
          </div>
        </Card>

        {shipping && <Alert type="info" showIcon message={`正在确认出库；等待处理 ${queuedScans} 张面单`} description="队列清空前请勿离开页面" />}
        {scanFeedback && <Alert type={scanFeedback.duplicate ? 'info' : 'warning'} showIcon
          message={`${scanFeedback.code}：${scanFeedback.detail}`} />}

        {error && (
          <Alert
            type="error"
            showIcon
            message={error}
            description={`编号：${lastCode || '（空）'} · 可直接重新扫描或检查右上角店铺是否选对`}
          />
        )}

        {loading && result && (
          <Typography.Text type="secondary">
            <Spin size="small" /> 正在刷新…
          </Typography.Text>
        )}

        {hintBanner()}

        {result ? (
          <>
            <Card size="small" title={`应装商品（${items.length} 款 / ${result.total_quantity} 件）`}>
              {/* 和装箱核对一样：左边商品图片，右边收货信息核对（窄屏自动堆叠）。 */}
              <div className="packing-check-content">
                <div className="packing-check-products"><PackingItems items={items} /></div>
                <RecipientInfo result={result} />
              </div>
            </Card>
            <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
              {shopId && <PackingControls shopId={shopId} result={result} onChanged={() => void runLookup(lastCode, true)} />}
              <Space wrap size={[24, 8]} align="center">
                <Space size={8}>
                  <Typography.Text strong style={{ fontSize: 16 }}>
                    {result.type === 'after_sale' ? '售后补发单' : '订单'} {result.reference}
                  </Typography.Text>
                  <Tag color={result.cancelled ? 'red' : statusColor}>{statusLabel || '状态未知'}</Tag>
                </Space>
                <Typography.Text type="secondary">客户：{result.customer_name || '—'}</Typography.Text>
                {lastCode && (
                  <Typography.Text type="secondary">本次扫描：{lastCode}</Typography.Text>
                )}
                <Typography.Text type="secondary">
                  面单（{result.labels.filter((label) => label.scanned).length}/
                  {result.expected_parcel_count} 已扫）
                </Typography.Text>
              </Space>
            </Card>
          </>
        ) : (
          !loading && (
            <Card>
              <Empty
                image={<InboxOutlined style={{ fontSize: 56, color: '#cbd5e1' }} />}
                description="扫描面单条码一次完成出库"
              />
            </Card>
          )
        )}

        <Card
          size="small"
          title="最近出库（可撤回）"
          extra={
            <Button size="small" onClick={() => void loadRecent()} loading={recentLoading}>
              刷新
            </Button>
          }
        >
          <Space wrap style={{ marginBottom: 12 }}>
            <Input.Search
              aria-label="搜索出库记录"
              placeholder="订单号 / CF售后单号 / 面单号"
              value={recentSearch}
              enterButton="查询"
              allowClear
              onChange={(event) => setRecentSearch(event.target.value)}
              onSearch={(value) => {
                savePagination('outbound-history', 1, recentQuery.pageSize);
                setRecentQuery((prev) => ({...prev, current: 1, search: value.trim()}));
              }}
              style={{ width: 300, maxWidth: '100%' }}
            />
          </Space>
          {recentError && <Alert type="error" showIcon message={recentError} style={{ marginBottom: 12 }} />}
          <Table<OutboundRecord>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={recent}
            loading={recentLoading}
            pagination={{
              current: recentQuery.current, pageSize: recentQuery.pageSize, total: recentTotal,
              showSizeChanger: true, showQuickJumper: true, pageSizeOptions: [20, 50, 100],
              showTotal: (total) => `共 ${total} 条出库记录`,
              onChange: (current, pageSize) => {
                savePagination('outbound-history', current, pageSize);
                setRecentQuery((prev) => ({...prev, current, pageSize}));
              },
            }}
            locale={{ emptyText: '这个店铺还没有出库记录' }}
          />
        </Card>
      </Space>

      <Modal
        title={`撤回出库：${undoTarget?.title || ''}`}
        open={Boolean(undoTarget)}
        onCancel={() => {
          setUndoTarget(null);
          setUndoReason('');
        }}
        onOk={() => void submitUndo()}
        okText="确认撤回"
        cancelText="取消"
        confirmLoading={undoBusy}
        okButtonProps={{ danger: true }}
      >
        <Typography.Paragraph type="secondary">
          撤回后库存和面单恢复，不影响 Shopify。
        </Typography.Paragraph>
        <Input.TextArea
          rows={2}
          value={undoReason}
          onChange={(event) => setUndoReason(event.target.value)}
          placeholder="撤回原因（选填，会记录在操作流水里）"
          maxLength={255}
        />
      </Modal>
    </PageContainer>
  );
};

export default ShippingOutbound;
