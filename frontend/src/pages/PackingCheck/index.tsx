import {
  CheckCircleOutlined,
  ClearOutlined,
  InboxOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ScanOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useModel } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import type { InputRef } from 'antd';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { PackingItems, RecipientInfo } from '@/components';
import PackingControls from '@/components/PackingControls';
import { lookupPackingList, type PackingList } from '@/services/shipping/api';
import { getErrorMessage } from '@/utils/error';
import { isPackingScannerMode, setPackingFocusMode, setPackingScannerMode } from '@/utils/layoutPrefs';
import { isCompletePackingLabel, isDuplicatePackingScan, normalizePackingScan, type PackingScanAttempt } from '@/utils/packingScan';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';

/** 太短的编号不自动查，避免边打字边发请求；回车可以强制查。 */
const MIN_AUTO_LENGTH = 3;

/**
 * 出库在哪做。
 *
 * 装箱核对只出清单、不改状态；真正的出库在「扫码出库」页（扫一次 + 可撤回）。
 * 这里只提示一句，把现场引到那个页面去。
 */
const CONFIRM_OUTBOUND_HINT = '要出库请到「扫码出库」页';
/** 手动输入时停顿多久自动查询（扫码枪会自己发回车，不受这个影响） */
const DEBOUNCE_MS = 350;

const STATUS_TEXT: Record<string, string> = {
  pending: '待发货',
  shipped: '已出库',
  legacy_unverified: '历史订单 · 未核对',
  legacy_partial_unverified: '历史订单 · 部分核对',
  pending_purchase: '待采购',
  purchasing: '采购中',
  ready: '待补发',
  ready_to_ship: '可出库',
  cancelled: '已取消',
};

/** 仓库状态颜色，打包台一眼能看出单子处于哪一步 */
const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  shipped: 'green',
  legacy_unverified: 'default',
  legacy_partial_unverified: 'default',
  pending_purchase: 'orange',
  purchasing: 'blue',
  ready: 'green',
  cancelled: 'red',
};

const PackingCheck: React.FC = () => {
  const selectedShop = getSelectedShopId();
  const shopId = selectedShop === ALL_SHOPS ? null : Number(selectedShop);
  const { initialState, setInitialState } = useModel('@@initialState');
  const menuHidden = Boolean(initialState?.packingFocus);

  const [code, setCode] = useState('');
  const [scannerMode, setScannerMode] = useState(isPackingScannerMode);
  const [result, setResult] = useState<PackingList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef<InputRef>(null);
  // 查询序号：只认最后一次请求的结果，避免快速连扫时旧结果覆盖新结果
  const requestSeq = useRef(0);
  // 最近一枪扫的编号（输入框扫完就清空，这里留一份给界面显示）
  const [lastCode, setLastCode] = useState('');
  // 提交时立即消费当前内容，不能等待网络返回后才清空。
  const codeRef = useRef('');
  const lastAttempt = useRef<PackingScanAttempt | null>(null);

  const runLookup = useCallback(
    async (raw: string, refresh = false) => {
      const value = normalizePackingScan(raw);
      if (!shopId || !value) return;

      // Consume the input before awaiting the network, including failed lookups.
      // The next scan must start from an empty field, not append to this label.
      if (codeRef.current === raw) {
        codeRef.current = '';
        setCode('');
      }
      const attempt = { shopId, value, startedAt: Date.now() };
      if (!refresh && isDuplicatePackingScan(lastAttempt.current, attempt)) return;
      lastAttempt.current = attempt;
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
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setResult(null);
        setError(getErrorMessage(err, '查询失败'));
      } finally {
        if (seq === requestSeq.current) {
          setLoading(false);
          // 保持焦点，下一枪扫码直接开始
          window.setTimeout(() => {
            inputRef.current?.focus();
            window.scrollTo(0, 0);
          }, 0);
        }
      }
    },
    [shopId],
  );

  // 手动输入：停顿一下自动查，不用按回车
  useEffect(() => {
    const value = code;
    if (!shopId || value.length < MIN_AUTO_LENGTH) return;
    const timer = window.setTimeout(() => runLookup(value), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [code, shopId, runLookup]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

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
  };

  const toggleInputMode = () => {
    const next = !scannerMode;
    inputRef.current?.blur(); // Dismiss an already-open iPad keyboard before refocusing read-only.
    setPackingScannerMode(next);
    setScannerMode(next);
    codeRef.current = '';
    setCode('');
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  // Read-only prevents iPad's virtual keyboard; HID scanner keydown events still arrive.
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

  // 打包台现场基本只用商品清单，把左侧菜单收起来；需要时一键展开
  const toggleMenu = () => {
    const next = !menuHidden;
    setPackingFocusMode(next);
    setInitialState((prev) => ({ ...prev, packingFocus: next }));
  };

  const items = result?.items || [];
  const statusLabel = result ? STATUS_TEXT[result.status] || result.status : '';
  const statusColor = result ? STATUS_COLOR[result.status] || 'blue' : 'blue';
  const isLegacy = Boolean(result?.status?.startsWith('legacy_'));

  /**
   * 出库提示条：告诉现场这单现在能不能出库、该去哪一出库。
   *
   * 装箱核对只负责核对，不做任何状态变更（出库在「扫码出库」页，那边能撤回）。
   * 这样现场只有一个地方会真的出库，不会出现「在 A 页能出、在 B 页不能撤」的困惑。
   */
  const outboundBanner = () => {
    const outbound = result?.outbound;
    if (!result || !outbound) return null;
    const shipped = result.status === 'shipped';

    if (outbound.ready) {
      return (
        <Alert
          type="success"
          showIcon
          icon={<CheckCircleOutlined />}
          message="商品已备齐，可以出库"
          description={CONFIRM_OUTBOUND_HINT}
          action={
            <Button
              type="primary"
              onClick={() => history.push('/shipping-outbound')}
            >
              去扫码出库
            </Button>
          }
        />
      );
    }

    if (shipped || outbound.already_scanned) {
      return (
        <Alert
          type="info"
          showIcon
          message={
            outbound.scanned_parcel_count >= outbound.expected_parcel_count
              ? '整单包裹都已扫码，订单已完成出库'
              : '这张面单已经扫过'
          }
          description={
            outbound.scanned_parcel_count >= outbound.expected_parcel_count
              ? '如需取消请到「扫码出库」撤回。'
              : `已扫 ${outbound.scanned_parcel_count}/${outbound.expected_parcel_count} 个包裹，把剩下的面单扫一遍即可完成出库。`
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
          message="这次扫的是订单号，只能看清单"
          description="出库请扫实体面单条码"
        />
      );
    }

    return null;
  };

  const resultCard = () => {
    if (loading && !result) {
      return (
        <Card>
          <Spin>
            <div style={{ padding: 48, textAlign: 'center', color: '#94a3b8' }}>正在查询…</div>
          </Spin>
        </Card>
      );
    }
    if (!result) {
      return (
        <Card>
          <Empty
            image={<InboxOutlined style={{ fontSize: 56, color: '#cbd5e1' }} />}
            description="扫描或输入订单号 / 物流单号，结果会立即显示"
          />
        </Card>
      );
    }

    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card
          className="packing-products-card"
          size="small"
          title={<div className="packing-products-heading">
            <span>{result.reference} · {items.length} 款商品</span>
            <span className="packing-total-quantity">
              应装总计 <strong>{result.total_quantity}</strong> 件
            </span>
          </div>}
          extra={<Space wrap size={4}>
            {shopId && <PackingControls compact shopId={shopId} result={result} onChanged={() => void runLookup(lastCode, true)} />}
            <Tag color={result.cancelled ? 'red' : result.outbound?.ready ? 'green' : statusColor}>
              {result.cancelled ? '已取消，勿装箱' : result.outbound?.ready ? '商品已备齐' : statusLabel}
            </Tag>
            {(result.reason || result.order_note || result.note) && <Tag color="orange">有备注，请核对下方备注</Tag>}
          </Space>}
        >
          <div className="packing-check-content">
            <div className="packing-check-products"><PackingItems items={items} /></div>
            <RecipientInfo result={result} />
          </div>
        </Card>
        {outboundBanner()}
        {result.reason && (
          <Alert type="info" showIcon message="售后原因" description={result.reason} />
        )}
        {(result.order_note || (result.type === 'order' ? result.note : null))?.trim() && (
          <Alert
            type="warning"
            showIcon
            message="当前订单备注（Shopify）"
            description={<span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {result.order_note || result.note}
            </span>}
          />
        )}
        {result.type === 'after_sale' && result.note?.trim() && (
          <Alert type="warning" showIcon message="售后处理备注" description={result.note} />
        )}
        {isLegacy && (
          <Alert
            type="info"
            showIcon
            message="历史订单"
          />
        )}

        {/* 订单信息放商品下面：现场先看实物，再回头核对收件信息 */}
        <Card size="small" styles={{ body: { padding: '12px 16px' } }}>
          <Space wrap size={[24, 8]} align="center">
            <Space size={8}>
              <Typography.Text strong style={{ fontSize: 16 }}>
                {result.type === 'after_sale' ? '售后补发单' : '订单'} {result.reference}
              </Typography.Text>
              <Tag color={result.cancelled ? 'red' : statusColor}>
                {statusLabel || '状态未知'}
              </Tag>
              {result.type === 'after_sale' && <Tag color="purple">补发</Tag>}
              {result.original_order_number && (
                <Typography.Text type="secondary">
                  原订单 {result.original_order_number}
                </Typography.Text>
              )}
            </Space>
            <Typography.Text type="secondary">
              客户：{result.customer_name || '—'}
            </Typography.Text>
            {lastCode && (
              <Typography.Text type="secondary">
                本次扫描：{lastCode}
              </Typography.Text>
            )}
            <Space size={4}>
              <Typography.Text type="secondary">
                物流单号（{result.labels.length}/{result.expected_parcel_count} 个包裹）：
              </Typography.Text>
              {result.labels.length
                ? result.labels.map((label) => (
                    <Tag
                      key={`${label.tracking_number}-${label.barcode}`}
                      color={label.scanned ? 'green' : 'default'}
                    >
                      {label.tracking_number || label.barcode || '未登记'}
                      {label.scanned ? ' · 已扫描' : ''}
                    </Tag>
                  ))
                : '—'}
            </Space>
          </Space>
        </Card>
      </Space>
    );
  };

  return (
    <PageContainer
      className="packing-check-page"
      title="装箱核对"
      subTitle="扫描或输入订单号 / 物流单号"
      extra={[
        <Button key="input-mode" onClick={toggleInputMode}>
          {scannerMode ? '切换手动输入' : '切换扫码枪模式'}
        </Button>,
        <Button
          key="menu"
          icon={menuHidden ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={toggleMenu}
        >
          {menuHidden ? '显示菜单' : '隐藏菜单'}
        </Button>,
        <Button
          key="clear"
          icon={<ClearOutlined />}
          onClick={reset}
          disabled={!code && !result}
        >
          清空
        </Button>,
      ]}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {!shopId && (
          <Alert
            type="warning"
            showIcon
            message="请先在右上角选择具体店铺"
          />
        )}

        {/* 输入框刻意弱化：现场主要是核对商品，扫完就不看了 */}
        <Card className="packing-scanner-card" size="small" styles={{ body: { padding: '10px 12px' } }}>
          <Input
            ref={inputRef}
            allowClear
            readOnly={scannerMode}
            inputMode={scannerMode ? 'none' : 'text'}
            autoComplete="off"
            aria-label={scannerMode ? '扫码枪输入（不弹键盘）' : '手动输入订单号或物流单号'}
            disabled={!shopId}
            prefix={<ScanOutlined style={{ color: '#cbd5e1' }} />}
            placeholder="扫描或输入订单号 / 物流单号"
            value={code}
            onKeyDown={onScannerKeyDown}
            onChange={(event) => {
              const value = event.target.value.toUpperCase();
              codeRef.current = value;
              setCode(value);
              setError('');
              // Complete 4PX labels can submit without a scanner Enter suffix.
              if (isCompletePackingLabel(value)) void runLookup(value);
            }}
            onPressEnter={() => { if (!scannerMode) void runLookup(codeRef.current); }}
          />
          <div className="packing-scanner-status">
            {scannerMode ? '扫码枪模式 · 不弹软键盘' : '手动输入模式 · 回车查询'}
          </div>
        </Card>

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

        {resultCard()}
        <details className="packing-help">
          <summary>使用说明 · 本页只核对，不出库</summary>
          <p>扫一枪看应装清单；查不到可直接重扫。</p>
          <p>出库请到「扫码出库」页扫实体面单。</p>
        </details>
      </Space>
    </PageContainer>
  );
};

export default PackingCheck;
