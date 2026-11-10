import { CopyOutlined, DownloadOutlined, QuestionCircleOutlined, ReloadOutlined, UnorderedListOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Alert, Button, Card, Descriptions, Drawer, Input, Modal, Progress, Space, Switch, Table, Tabs, Tag, Tooltip, Typography, message } from 'antd';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  checkFourPxDeclaration,
  exportFourPxDeclaration,
  getFourPxDeclarationJob,
  getFourPxDeclarationSettings,
  listFourPxDeclarationJobs,
  planFourPxDeclaration,
  saveFourPxDeclarationSettings,
  startFourPxDeclarationJob,
  syncFourPxManualDeclarations,
  type DeclarationCheckPage,
  type DeclarationCheckRow,
  type DeclarationCreateResult,
  type DeclarationJob,
  type DeclarationPlanResult,
  type DeclarationSyncResult,
} from '@/services/shipping/api';
import { ShopRequiredNotice } from '@/components';
import { getErrorMessage } from '@/utils/error';
import { getCurrentBeijingTime } from '@/utils/timezone';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';

type TabKey = 'ready' | 'blocked';

const PAGE_SIZE = 20;

/**
 * 4PX 预申报：一个页面管完「可预申报」和「待人工处理」两类订单。
 * 只读检查 + 人工确认后向 4PX 建单，不做定时自动。
 */
const DeclarationCheck: React.FC = () => {
  const selectedShop = getSelectedShopId();
  const shopId = selectedShop === ALL_SHOPS ? null : Number(selectedShop);

  const [tab, setTab] = useState<TabKey>('ready');
  const [data, setData] = useState<DeclarationCheckPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [orderFrom, setOrderFrom] = useState('');
  const [orderTo, setOrderTo] = useState('');
  const [range, setRange] = useState<{ from?: string; to?: string }>({});
  const [page, setPage] = useState(1);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectingAll, setSelectingAll] = useState(false);
  const [preview, setPreview] = useState<DeclarationCheckRow | null>(null);
  const [plan, setPlan] = useState<DeclarationPlanResult | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createResult, setCreateResult] = useState<DeclarationCreateResult | null>(null);
  const [job, setJob] = useState<DeclarationJob | null>(null);
  const jobIdRef = useRef<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<DeclarationSyncResult | null>(null);
  const [exporting, setExporting] = useState(false);
  const [autoWriteback, setAutoWriteback] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);

  const load = useCallback(async (
    keyword: string, targetPage: number, currentRange: { from?: string; to?: string } = {},
    status: TabKey = 'ready',
  ) => {
    if (!shopId) return;
    setLoading(true);
    setError('');
    try {
      const response = await checkFourPxDeclaration(shopId, {
        search: keyword.trim() || undefined,
        page: targetPage,
        per_page: PAGE_SIZE,
        order_from: currentRange.from || undefined,
        order_to: currentRange.to || undefined,
        status,
      });
      setData(response.data);
    } catch (err) {
      setData(null);
      setError(getErrorMessage(err, '加载失败，请重试'));
    } finally {
      setLoading(false);
    }
  }, [shopId]);

  useEffect(() => {
    setData(null);
    setSearch('');
    setOrderFrom('');
    setOrderTo('');
    setRange({});
    setPage(1);
    setTab('ready');
    setSelectedIds([]);
    setAutoWriteback(false);
    setJob(null);
    jobIdRef.current = null;
    if (shopId) {
      void load('', 1, {}, 'ready');
      void getFourPxDeclarationSettings(shopId)
        .then((response) => setAutoWriteback(Boolean(response.data.auto_writeback)))
        .catch(() => setAutoWriteback(false));
      void listFourPxDeclarationJobs(shopId)
        .then((response) => {
          const latest = response.data.jobs?.[0];
          if (latest) {
            setJob(latest);
            jobIdRef.current = latest.id;
          }
        })
        .catch(() => undefined);
    }
  }, [shopId, load]);

  useEffect(() => {
    if (!shopId || !job || !['queued', 'running'].includes(job.status)) return undefined;
    const timer = window.setInterval(() => {
      if (!jobIdRef.current) return;
      void getFourPxDeclarationJob(shopId, jobIdRef.current)
        .then((response) => {
          setJob(response.data);
          if (response.data.status === 'done' || response.data.status === 'failed') {
            window.clearInterval(timer);
          }
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [shopId, job?.id, job?.status]);

  const toggleAutoWriteback = async (checked: boolean) => {
    if (!shopId) return;
    const previous = autoWriteback;
    setAutoWriteback(checked);
    setSettingsLoading(true);
    try {
      await saveFourPxDeclarationSettings(shopId, checked);
      message.success(checked ? '已开启：建单后自动回填 Shopify' : '已关闭自动回填');
    } catch (err) {
      setAutoWriteback(previous);
      message.error(getErrorMessage(err, '保存失败，请重试'));
    } finally {
      setSettingsLoading(false);
    }
  };

  const switchTab = (key: string) => {
    const next = key as TabKey;
    setTab(next);
    setPage(1);
    setSelectedIds([]);
    void load(search, 1, range, next);
  };

  const applyRange = () => {
    const next = { from: orderFrom.trim() || undefined, to: orderTo.trim() || undefined };
    setRange(next);
    setPage(1);
    setSelectedIds([]);
    void load(search, 1, next, tab);
  };

  const clearRange = () => {
    setOrderFrom('');
    setOrderTo('');
    setRange({});
    setPage(1);
    setSelectedIds([]);
    void load(search, 1, {}, tab);
  };

  const selectAllFiltered = async () => {
    if (!shopId) return;
    setSelectingAll(true);
    try {
      const ids: number[] = [];
      let currentPage = 1;
      for (;;) {
        const response = await checkFourPxDeclaration(shopId, {
          search: search.trim() || undefined, page: currentPage, per_page: 100,
          order_from: range.from || undefined, order_to: range.to || undefined, status: 'ready',
        });
        const pageData = response.data;
        ids.push(...pageData.orders.filter((row) => row.ready).map((row) => row.order_id));
        if (currentPage >= (pageData.pagination?.pages || 1) || ids.length >= 1000) break;
        currentPage += 1;
      }
      setSelectedIds(ids.slice(0, 1000));
      message.success(`已选择 ${Math.min(ids.length, 1000)} 单`);
    } catch (err) {
      message.error(getErrorMessage(err, '全选失败，请重试'));
    } finally {
      setSelectingAll(false);
    }
  };

  const exportCsv = async () => {
    if (!shopId) return;
    setExporting(true);
    try {
      const content = await exportFourPxDeclaration(shopId, search.trim() || undefined, range, { status: tab });
      const url = URL.createObjectURL(content);
      const link = document.createElement('a');
      link.href = url;
      link.download = `4px-declaration-${tab}-${getCurrentBeijingTime('YYYY-MM-DD')}-shop-${shopId}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      message.success('已导出');
    } catch (err) {
      message.error(getErrorMessage(err, '导出失败'));
    } finally {
      setExporting(false);
    }
  };

  const buildPlan = async () => {
    if (!shopId || !selectedIds.length) return;
    setPlanLoading(true);
    try {
      setPlan((await planFourPxDeclaration(shopId, selectedIds)).data);
    } catch (err) {
      message.error(getErrorMessage(err, '生成清单失败'));
    } finally {
      setPlanLoading(false);
    }
  };

  const createOrders = async () => {
    if (!shopId || !selectedIds.length) return;
    setCreating(true);
    try {
      const response = await startFourPxDeclarationJob(shopId, selectedIds);
      setJob(response.data);
      jobIdRef.current = response.data.id;
      setCreateOpen(false);
      setSelectedIds([]);
      message.success('预申报任务已开始，可在页面上方查看进度');
      await load(search, page, range, tab);
    } catch (err) {
      message.error(getErrorMessage(err, '建单任务启动失败，请稍后重试'));
    } finally {
      setCreating(false);
    }
  };

  const syncManualOrders = async (payload: { orderIds?: number[]; jobId?: string }) => {
    if (!shopId) return;
    setSyncing(true);
    try {
      const response = await syncFourPxManualDeclarations(shopId, payload);
      setSyncResult(response.data);
      await load(search, page, range, tab);
    } catch (err) {
      message.error(getErrorMessage(err, '同步失败，请稍后重试'));
    } finally {
      setSyncing(false);
    }
  };

  const openJobResult = () => {
    if (!job) return;
    setCreateResult({
      summary: { requested: job.requested, created: job.created, failed: job.failed },
      results: job.results || [],
    });
  };

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success('已复制');
    } catch {
      message.warning('复制失败');
    }
  };

  const rowText = (row: DeclarationCheckRow) => {
    const address = [row.recipient?.street, row.recipient?.house_number, row.recipient?.city,
      row.recipient?.state, row.recipient?.post_code, row.country_name].filter(Boolean).join(', ');
    return `${row.order_number} | ${row.recipient?.first_name || ''} ${row.recipient?.last_name || ''} | `
      + `${row.recipient?.phone || '无电话'} | ${address}`;
  };

  const tabItems = [
    { key: 'ready', label: `可预申报（${data?.summary?.ready ?? 0}）` },
    { key: 'blocked', label: `待人工处理（${data?.summary?.blocked ?? 0}）` },
  ];

  const readyColumns = [
    {
      title: '订单号',
      dataIndex: 'order_number',
      render: (value: string, row: DeclarationCheckRow) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.order_date?.slice(0, 10) || ''}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '目的地',
      render: (_: unknown, row: DeclarationCheckRow) => (
        <Space direction="vertical" size={0}>
          <span>{row.country_name}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.postal_code}</Typography.Text>
        </Space>
      ),
    },
    { title: '件数', dataIndex: 'quantity', width: 70 },
    {
      title: '建议产品',
      render: (_: unknown, row: DeclarationCheckRow) => (
        <Space direction="vertical" size={0}>
          <span><Tag color="blue">{row.product?.code}</Tag>{row.product?.name}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.product_reason}</Typography.Text>
        </Space>
      ),
    },
    { title: '预报重（克）', dataIndex: 'weight_grams', width: 110 },
    {
      title: '申报价值',
      dataIndex: 'declared_value',
      width: 110,
      render: (value: number, row: DeclarationCheckRow) => `${row.declared_currency} ${value.toFixed(2)}`,
    },
    {
      title: '操作',
      width: 110,
      render: (_: unknown, row: DeclarationCheckRow) => (
        <Button size="small" onClick={() => setPreview(row)}>查看请求体</Button>
      ),
    },
  ];

  const blockedColumns = [
    {
      title: '订单号',
      dataIndex: 'order_number',
      render: (value: string, row: DeclarationCheckRow) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{value}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.order_date?.slice(0, 10) || ''}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '目的地',
      render: (_: unknown, row: DeclarationCheckRow) => `${row.country_name} ${row.postal_code}`,
    },
    { title: '件数', dataIndex: 'quantity', width: 70 },
    {
      title: '收件信息',
      render: (_: unknown, row: DeclarationCheckRow) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{row.recipient?.first_name} {row.recipient?.last_name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {row.recipient?.phone || '缺电话'} · {row.recipient?.street || '缺地址'}
            {row.recipient?.house_number ? ` ${row.recipient.house_number}` : ''}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '拦截原因',
      render: (_: unknown, row: DeclarationCheckRow) => (
        <Space size={[4, 4]} wrap>
          {row.blockers.map((reason) => <Tag color="red" key={reason}>{reason}</Tag>)}
        </Space>
      ),
    },
    {
      title: '操作',
      width: 300,
      render: (_: unknown, row: DeclarationCheckRow) => (
        <Space wrap size={4}>
          <Button size="small" loading={syncing}
            onClick={() => void syncManualOrders({ orderIds: [row.order_id] })}>
            同步 4PX
          </Button>
          <Button size="small" icon={<CopyOutlined />} onClick={() => void copyText(rowText(row))}>复制</Button>
          <Button size="small" type="link" onClick={() => history.push(`/orders/detail/${row.order_id}`)}>订单详情</Button>
        </Space>
      ),
    },
  ];

  if (!shopId) {
    return (
      <PageContainer title="4PX 预申报">
        <ShopRequiredNotice reason="先选择具体店铺再检查。" />
      </PageContainer>
    );
  }

  const ruleTip = (
    <Space direction="vertical" size={4}>
      <span>产品：轻小件 S5058 · 标准挂号 QC · 智选 S5191 · 岛屿 S5118</span>
      <span>申报：US/AU 1.20、CA/GB/NZ 1.00 USD/件；手机链 1.00、保护膜 1.20</span>
      <span>待人工处理：地址/邮编/州不一致、占位信息、缺电话邮箱、国家不支持</span>
    </Space>
  );

  return (
    <PageContainer
      title="4PX 预申报"
      subTitle="只读检查；确认后才向 4PX 建单，不做定时自动"
      extra={[
        tab === 'ready' ? (
          <Button
            key="plan"
            icon={<UnorderedListOutlined />}
            disabled={!selectedIds.length}
            loading={planLoading}
            onClick={() => void buildPlan()}
          >
            生成清单{selectedIds.length ? `（${selectedIds.length}）` : ''}
          </Button>
        ) : null,
        tab === 'ready' ? (
          <Button
            key="create"
            danger
            type="primary"
            disabled={!selectedIds.length || job?.status === 'running' || job?.status === 'queued'}
            loading={creating}
            onClick={() => setCreateOpen(true)}
          >
            真实预申报{selectedIds.length ? `（${selectedIds.length}）` : ''}
          </Button>
        ) : null,
        <Button
          key="sync"
          disabled={!selectedIds.length || syncing}
          loading={syncing}
          onClick={() => void syncManualOrders({ orderIds: selectedIds })}
        >
          同步 4PX 已有单{selectedIds.length ? `（${selectedIds.length}）` : ''}
        </Button>,
        <Button key="export" icon={<DownloadOutlined />} loading={exporting} onClick={() => void exportCsv()}>
          导出
        </Button>,
        <Button key="reload" icon={<ReloadOutlined />} loading={loading} onClick={() => void load(search, page, range, tab)}>
          刷新
        </Button>,
      ].filter(Boolean)}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon message={error} />}

        {job && (
          <Alert
            type={job.status === 'failed' ? 'error' : job.status === 'done' ? 'success' : 'info'}
            showIcon
            message={
              job.status === 'queued' ? '预申报任务排队中…'
                : job.status === 'running' ? '预申报进行中（后台执行，关页面不丢）'
                  : job.status === 'done' ? '预申报已完成'
                    : '预申报任务失败，请查看已建结果'
            }
            description={
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <Progress
                  percent={job.requested ? Math.round((job.processed / job.requested) * 100) : 0}
                  status={job.status === 'failed' ? 'exception' : job.status === 'done' ? 'success' : 'active'}
                />
                <Typography.Text type="secondary">
                  已处理 {job.processed}/{job.requested} · 成功 {job.created} · 失败/跳过 {job.failed}
                  {job.error ? ` · ${job.error}` : ''}
                </Typography.Text>
              </Space>
            }
            action={
              <Space direction="vertical" size={4}>
                {(job.status === 'done' || job.status === 'failed') && job.failed > 0 && (
                  <Button size="small" loading={syncing}
                    onClick={() => void syncManualOrders({ jobId: job.id })}>
                    同步失败的 {job.failed} 单
                  </Button>
                )}
                {(job.status === 'done' || job.status === 'failed') && (
                  <Button size="small" onClick={openJobResult}>查看结果</Button>
                )}
                {(job.status === 'done' || job.status === 'failed') && (
                  <Button size="small" type="link" onClick={() => setJob(null)}>清除</Button>
                )}
              </Space>
            }
          />
        )}

        <Tabs activeKey={tab} onChange={switchTab} items={tabItems} />

        <Card size="small">
          <Space wrap size={[12, 8]} align="center">
            <Input.Search
              placeholder="订单号"
              allowClear
              value={search}
              style={{ width: 180 }}
              onChange={(event) => setSearch(event.target.value)}
              onSearch={(value) => { setPage(1); void load(value, 1, range, tab); }}
            />
            <Input placeholder="起始单号" value={orderFrom} style={{ width: 110 }}
              onChange={(event) => setOrderFrom(event.target.value)} />
            <Typography.Text type="secondary">至</Typography.Text>
            <Input placeholder="结束单号" value={orderTo} style={{ width: 110 }}
              onChange={(event) => setOrderTo(event.target.value)} />
            <Button size="small" onClick={applyRange}>筛选</Button>
            <Button size="small" type="link" onClick={clearRange}>清除</Button>
            {tab === 'ready' && (
              <Button size="small" loading={selectingAll} onClick={() => void selectAllFiltered()}>
                全选筛选结果{selectedIds.length ? `（${selectedIds.length}）` : ''}
              </Button>
            )}
            <Space size={4} align="center">
              <Typography.Text>建单后自动回填 Shopify</Typography.Text>
              <Switch size="small" checked={autoWriteback} loading={settingsLoading}
                onChange={(checked) => void toggleAutoWriteback(checked)} />
              <Tooltip title="开启后，预申报建单成功即回填 Shopify 并发客户发货通知；包裹不齐（多包裹单）会等补齐后回填。默认关闭。">
                <QuestionCircleOutlined style={{ color: '#94a3b8' }} />
              </Tooltip>
            </Space>
            <Tooltip title={ruleTip}>
              <QuestionCircleOutlined style={{ color: '#94a3b8' }} />
            </Tooltip>
          </Space>
        </Card>

        <Table<DeclarationCheckRow>
          rowKey="order_id"
          size="small"
          loading={loading}
          columns={tab === 'ready' ? readyColumns : blockedColumns}
          dataSource={data?.orders || []}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys.map(Number)),
            getCheckboxProps: (row) => ({ disabled: tab === 'ready' && !row.ready }),
          }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.pagination?.total || 0,
            showSizeChanger: false,
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 单`,
            onChange: (targetPage) => { setPage(targetPage); void load(search, targetPage, range, tab); },
          }}
          locale={{ emptyText: tab === 'ready' ? '没有可预申报订单' : '没有需要人工处理的订单' }}
        />
      </Space>

      <Drawer
        title={preview ? `${preview.order_number} 请求体` : '请求体'}
        width={620}
        open={Boolean(preview)}
        onClose={() => setPreview(null)}
      >
        {preview && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="产品">{preview.product?.code} {preview.product?.name}</Descriptions.Item>
              <Descriptions.Item label="预报重">{preview.weight_grams} 克</Descriptions.Item>
              <Descriptions.Item label="申报价值">{preview.declared_currency} {preview.declared_value.toFixed(2)}</Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Text strong>报关明细</Typography.Text>
              <ul style={{ margin: '6px 0 0', paddingLeft: 20 }}>
                {preview.declared_items.map((item) => (
                  <li key={item.name_en}>
                    {item.name_en}（{item.name_cn}）× {item.quantity}，{preview.declared_currency} {item.unit_price.toFixed(2)}/件
                  </li>
                ))}
              </ul>
            </div>
            <pre style={{ background: '#f8fafc', padding: 12, borderRadius: 8, fontSize: 12, overflow: 'auto', maxHeight: 360 }}>
              {JSON.stringify(preview.payload_preview, null, 2)}
            </pre>
          </Space>
        )}
      </Drawer>

      <Modal
        title="确认向 4PX 建单？"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void createOrders()}
        okText="确认建单"
        okButtonProps={{ danger: true }}
        confirmLoading={creating}
      >
        <Alert
          type="warning"
          showIcon
          message={`将向 4PX 创建 ${selectedIds.length} 张直发委托单`}
          description="会真实占用 4PX 单号；任务在后台按 50 单一批执行，关掉弹窗或刷新页面进度都不会丢。已有包裹或 4PX 已存在的单自动跳过/接回。"
        />
      </Modal>

      <Modal
        title="真实预申报结果"
        open={Boolean(createResult)}
        width={720}
        onCancel={() => setCreateResult(null)}
        footer={<Button onClick={() => setCreateResult(null)}>关闭</Button>}
      >
        {createResult && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type={createResult.summary.failed ? 'warning' : 'success'}
              showIcon
              message={`共 ${createResult.summary.requested} 单：成功 ${createResult.summary.created}，失败/跳过 ${createResult.summary.failed}`}
            />
            <Table
              rowKey={(row) => `${row.order_id}-${row.status}`}
              size="small"
              pagination={false}
              dataSource={createResult.results}
              columns={[
                { title: '订单号', dataIndex: 'order_number', render: (value) => value || '—' },
                {
                  title: '结果',
                  dataIndex: 'status',
                  render: (value: string) => (
                    <Tag color={['created', 'created_after_retry', 'already_exists'].includes(value) ? 'green' : value === 'failed' ? 'red' : 'orange'}>
                      {value === 'created' ? '已建单' : value === 'created_after_retry' ? '已建单(回查)' : value === 'already_exists' ? '已存在' : value === 'skipped' ? '跳过' : '失败'}
                    </Tag>
                  ),
                },
                { title: '4PX 单号', dataIndex: 'tracking_number', render: (value) => value || '—' },
                { title: '说明', dataIndex: 'error', render: (value) => value || '—' },
              ]}
            />
          </Space>
        )}
      </Modal>

      <Modal
        title="同步 4PX 已有单结果"
        width={760}
        open={Boolean(syncResult)}
        onCancel={() => setSyncResult(null)}
        footer={<Button onClick={() => setSyncResult(null)}>关闭</Button>}
      >
        {syncResult && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type={syncResult.summary.not_found || syncResult.summary.failed ? 'warning' : 'success'}
              showIcon
              message={`已绑定 ${syncResult.summary.bound} 单，已有包裹 ${syncResult.summary.already_bound} 单，未找到 ${syncResult.summary.not_found} 单，失败 ${syncResult.summary.failed} 单`}
              description="已绑定的订单不会重复建单；未找到表示 4PX 上没有用该订单号建的委托单（客户单号不一致或还没申报）。"
            />
            <Table
              rowKey={(row) => `${row.order_id}-${row.status}`}
              size="small"
              pagination={false}
              dataSource={syncResult.results}
              columns={[
                { title: '订单号', dataIndex: 'order_number', render: (value) => value || '—' },
                {
                  title: '结果',
                  dataIndex: 'status',
                  render: (value: string) => (
                    <Tag color={value === 'bound' ? 'green' : value === 'already_bound' ? 'blue' : value === 'not_found' ? 'orange' : 'red'}>
                      {value === 'bound' ? '已绑定' : value === 'already_bound' ? '已有包裹' : value === 'not_found' ? '4PX 未找到' : value === 'conflict' ? '单号冲突' : '失败'}
                    </Tag>
                  ),
                },
                { title: '4PX 单号', dataIndex: 'tracking_number', render: (value) => value || '—' },
                { title: '说明', dataIndex: 'error', render: (value) => value || '—' },
              ]}
            />
          </Space>
        )}
      </Modal>

      <Modal
        title="批量预申报清单"
        open={Boolean(plan)}
        width={820}
        onCancel={() => setPlan(null)}
        footer={<Button onClick={() => setPlan(null)}>关闭</Button>}
      >
        {plan && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message={`选中 ${plan.summary.requested} 单：可预申报 ${plan.summary.eligible}，需人工 ${plan.summary.blocked}，无效 ${plan.summary.invalid}`}
            />
            <Table
              rowKey={(row) => row.product.code}
              size="small"
              pagination={false}
              dataSource={plan.groups}
              columns={[
                { title: '产品', render: (_, row) => `${row.product.code} ${row.product.name}` },
                { title: '单数', dataIndex: 'order_count', width: 80 },
                { title: '件数', dataIndex: 'quantity', width: 80 },
                { title: '预报重', dataIndex: 'weight_grams', width: 100 },
                { title: '申报价值', dataIndex: 'declared_value', width: 110, render: (value: number) => `USD ${value.toFixed(2)}` },
                { title: '订单号', render: (_, row) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.order_numbers.join('、')}</Typography.Text> },
              ]}
            />
          </Space>
        )}
      </Modal>
    </PageContainer>
  );
};

export default DeclarationCheck;
