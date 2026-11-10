import { PageContainer } from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Alert, Button, Card, Drawer, Empty, Input, message, Pagination, Popconfirm, Progress, Radio, Space, Table, Tabs, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';
import { getErrorMessage } from '@/utils/error';
import { formatBeijingTime } from '@/utils/timezone';
import { listTracking, listTrackingAudits, reconcileTracking, refreshTracking, setTrackingHandled,
  type TrackingAudits, type TrackingResult, type TrackingRow, type TrackingTab, type TrackingHandling, type TrackingAge, type OutboundResult } from '@/services/logistics/api';

const titles: Record<TrackingTab, string> = {
  preinfo: '已出库 · 仅一条预报', pending: '未出库 · 已有运输轨迹', overdue: '预报满15天 · 未送达',
};

export default function Logistics() {
  const selectedShop = getSelectedShopId();
  const shopId = selectedShop === ALL_SHOPS ? null : Number(selectedShop);
  const access = useAccess();
  const [tab, setTab] = useState<TrackingTab>('preinfo');
  const [data, setData] = useState<TrackingResult>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [handling, setHandling] = useState<TrackingHandling>('unhandled');
  const [age, setAge] = useState<TrackingAge>('within24h');
  const [orderSort, setOrderSort] = useState<'asc' | 'desc'>('asc');
  const [handlingId, setHandlingId] = useState<number>();
  const [selected, setSelected] = useState<React.Key[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [results, setResults] = useState<OutboundResult[]>([]);
  const [detail, setDetail] = useState<TrackingRow>();
  const [audits, setAudits] = useState<TrackingAudits>();
  const requestId = useRef(0);
  const alive = useRef(true);
  const actionLock = React.useRef(false);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const current = () => alive.current && Number(getSelectedShopId()) === shopId;
  const load = useCallback(async (quiet = false) => {
    if (!shopId) return;
    const id = ++requestId.current;
    if (!quiet) setLoading(true);
    try {
      const response = await listTracking({shop_id: shopId, tab, search, page, per_page: pageSize,
        order: orderSort,
        age: tab === 'preinfo' ? age : undefined,
        handling: tab === 'overdue' || (tab === 'preinfo' && age === 'within24h') ? handling : undefined});
      if (id === requestId.current) { setData(response.data); setError(''); }
    } catch (err) {
      if (id === requestId.current) setError(getErrorMessage(err, '物流核查列表加载失败'));
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [shopId, tab, search, page, pageSize, handling, age, orderSort]);
  const latestLoad = useRef(load);
  latestLoad.current = load;
  useEffect(() => {void load(); return () => {requestId.current++;};}, [load]);
  useEffect(() => {setData(undefined); setSelected([]); setResults([]); setDetail(undefined); setAudits(undefined); setPage(1); setRefreshing(false); setHandling('unhandled'); setAge('within24h'); setOrderSort('asc'); setHandlingId(undefined);}, [shopId]);
  const showHandling = tab === 'overdue' || tab === 'preinfo';
  const handlingCounts = data?.handling_counts;
  const running = data?.job && ['queued', 'running'].includes(data.job.status);
  const backgroundRunning = running || (data?.history_job && ['queued', 'running'].includes(data.history_job.status));
  useEffect(() => {
    const timer = window.setInterval(() => {if (!document.hidden) void load(true);}, backgroundRunning ? 5000 : 30000);
    return () => window.clearInterval(timer);
  }, [backgroundRunning, load]);
  const refresh = async (scope: TrackingTab | 'due' | 'single', bindingId?: number) => {
    if (!shopId || refreshing) return;
    setRefreshing(true);
    try {
      const response = await refreshTracking(shopId, scope, bindingId);
      if (current()) {
        if (!response.data.total) message.info('没有到期运单，沿用上次结果，无需重复查询');
        else message.success('已提交后台核查，可以离开页面');
        void load(true);
      }
    } catch (err) {
      if (current()) message.error(getErrorMessage(err, '无法提交后台核查'));
    } finally {if (current()) setRefreshing(false);}
  };
  const reconcile = async () => {
    if (!shopId || actionLock.current || !selected.length) return;
    actionLock.current = true;
    setBusy(true); setResults([]);
    const done: OutboundResult[] = [];
    try {
      const ids = selected.map(Number);
      for (let offset = 0; offset < ids.length; offset += 20) {
        if (!current()) break;
        setProgress(`正在重新核实4PX并补记 ${Math.min(offset + 20, ids.length)}/${ids.length}`);
        const response = await reconcileTracking(shopId, ids.slice(offset, offset + 20));
        done.push(...response.data.results);
        if (current()) setResults([...done]);
      }
      if (current()) {setSelected([]); await load();}
    } catch (err) {
      if (current()) message.error(getErrorMessage(err, '部分请求结果未确认，请刷新核对；不要盲目重复补记'));
    } finally {
      actionLock.current = false;
      if (alive.current) {setBusy(false); setProgress('');}
    }
  };
  const showAudits = async (next = 1) => {
    if (!shopId) return;
    try {const response = await listTrackingAudits(shopId, next); if (current()) setAudits(response.data);}
    catch (err) {if (current()) message.error(getErrorMessage(err, '补记记录加载失败'));}
  };
  const handle = async (row: TrackingRow) => {
    if (!shopId || actionLock.current) return;
    actionLock.current = true;
    setHandlingId(row.id);
    try {
      await setTrackingHandled(shopId, row.id, !row.handled);
      if (current()) {
        message.success(row.handled ? '已恢复为未处理' : '已标记处理；不改变物流、出库或库存状态');
        setPage(1);
        await latestLoad.current(true);
      }
    } catch (err) {
      if (current()) message.error(getErrorMessage(err, '更新处理状态失败'));
    } finally {
      actionLock.current = false;
      if (current()) setHandlingId(undefined);
    }
  };
  const columns: ColumnsType<TrackingRow> = [
    {title: '订单 / 包裹', key: 'order', width: 190, sorter: true,
      sortOrder: orderSort === 'asc' ? 'ascend' : 'descend', render: (_, row) => <div>
      <Typography.Text strong>{row.order_number}</Typography.Text>{row.after_sale_id && <Tag color="purple">售后</Tag>}
      <div><a href={row.url} target="_blank" rel="noreferrer">{row.number}</a></div>
    </div>},
    {title: '仓库状态', key: 'warehouse', width: 155, render: (_, row) => <>
      <Tag color={row.outbound ? 'green' : 'orange'}>{row.outbound ? '已出库' : '未出库'}</Tag>
      {row.observing && <Tag>出库未满24小时 · 观察中</Tag>}
      {row.outbound_at && <div>{formatBeijingTime(row.outbound_at)}</div>}
    </>},
    {title: '最新物流轨迹', key: 'latest', render: (_, row) => <div style={{minWidth: 180, wordBreak: 'break-word'}}>
      <div>{row.latest?.message || '尚无可靠轨迹'}</div>
      {row.latest && <div>{formatBeijingTime(row.latest.happenedAt)}</div>}
      <Tag>{row.event_count}条轨迹</Tag>
      {row.error && <Typography.Text type="warning">{row.error}</Typography.Text>}
    </div>},
    {title: '首次预报 / 核查时间', key: 'times', width: 190, render: (_, row) => <div>
      <div>预报：{row.first_preinfo_at ? formatBeijingTime(row.first_preinfo_at) : '未取得'}</div>
      <div>核查：{row.checked_at ? formatBeijingTime(row.checked_at) : '尚未查询'}</div>
      <div>下次：{row.next_check_at ? formatBeijingTime(row.next_check_at) : '已结束自动查询'}</div>
    </div>},
    ...(showHandling ? [{title: '处理状态', key: 'handling', width: 160,
      render: (_: unknown, row: TrackingRow) => <div>
        <Tag color={row.handled ? 'green' : 'orange'}>{row.handled ? '已处理' : '未处理'}</Tag>
        {row.handled_at && <div>{formatBeijingTime(row.handled_at)}</div>}
        {row.handled_by_name && <div>处理人：{row.handled_by_name}</div>}
      </div>}] : []),
    {title: '操作', key: 'action', width: 130, render: (_, row) => <Space direction="vertical">
      <Button size="small" onClick={() => setDetail(row)}>轨迹详情</Button>
      <Button size="small" disabled={refreshing} onClick={() => void refresh('single', row.id)}>强制刷新此单</Button>
      {showHandling && <Button size="small" type={row.handled ? 'default' : 'primary'}
        loading={handlingId === row.id} disabled={handlingId !== undefined || busy} onClick={() => void handle(row)}>
        {row.handled ? '恢复未处理' : '标记已处理'}
      </Button>}
    </Space>},
  ];
  return <PageContainer title="物流轨迹核查" subTitle="4PX 官方接口 · 按店铺隔离">
    {!shopId ? <Empty description="请先选择具体店铺" /> : <Space direction="vertical" style={{width: '100%'}} size="middle">
      <Alert type="info" showIcon message="只更新到期运单，不重复全量核查" />
      <Card size="small"><Space wrap>
        <Button onClick={() => void refresh(tab)} loading={refreshing} disabled={Boolean(running)}>更新本类到期运单</Button>
        <Button onClick={() => void refresh('due')} disabled={refreshing || Boolean(running)}>更新全部到期运单</Button>
        <Button onClick={() => void load()}>刷新列表</Button>
        <Button onClick={() => void showAudits()}>补记与账实差异记录</Button>
        <Typography.Text type="secondary">已核查 {data?.coverage.checked || 0}/{data?.coverage.total || 0} 个物流号 · 尚未初查 {data?.coverage.unqueried || 0}</Typography.Text>
      </Space>
      {data?.job && <div style={{marginTop: 12}}>
        <Typography.Text>最近增量任务：{data.job.status} · 完成 {data.job.done}/{data.job.total} · 跳过重复/未到期 {data.job.skipped || 0} · 查询失败 {data.job.failed}（失败不代表物流异常）</Typography.Text>
        <Progress percent={data.job.total ? Math.floor(data.job.done / data.job.total * 100) : 100} status={running ? 'active' : data.job.status === 'failed' ? 'exception' : 'normal'} />
        {data.job.error && <Alert type="warning" message={data.job.error} />}
      </div>}
      {data?.history_job && <Typography.Paragraph type="secondary" style={{marginTop: 8, marginBottom: 0}}>
        独立历史首次补查：{data.history_job.status} · 本批 {data.history_job.done}/{data.history_job.total}
      </Typography.Paragraph>}
      </Card>
      {error && <Alert type="error" message={error} showIcon />}
      <Card>
        <Tabs activeKey={tab} onChange={key => {setTab(key as TrackingTab); setPage(1); setSelected([]); setHandling('unhandled');}} items={Object.entries(titles).map(([key, title]) => ({key, label: `${title}（${data?.counts[key as TrackingTab] || 0}）`}))} />
        <Space wrap style={{marginBottom: 16}}>
          {tab === 'preinfo' && <Radio.Group aria-label="出库时间范围" optionType="button" buttonStyle="solid"
            value={age} onChange={event => {setAge(event.target.value); setPage(1); setSelected([]); setHandling('unhandled');}}
            options={[
              {value: 'within24h', label: `出库24小时内（${data?.preinfo_age_counts?.within24h ?? 0}）`},
              {value: 'beyond24h', label: `出库24小时以外（${data?.preinfo_age_counts?.beyond24h ?? 0}）`},
            ]} />}
          {showHandling && <Radio.Group aria-label="异常处理状态" optionType="button" buttonStyle="solid"
            value={handling} onChange={event => {setHandling(event.target.value); setPage(1);}}
            options={[
              {value: 'unhandled', label: `未处理（${handlingCounts?.unhandled ?? (tab === 'overdue' ? data?.counts.overdue : data?.preinfo_age_counts?.within24h) ?? 0}）`},
              {value: 'handled', label: `已处理（${handlingCounts?.handled || 0}）`},
              {value: 'all', label: '全部'},
            ]} />}
          <Input.Search allowClear placeholder="精确查询订单号 / CF号 / 物流单号" onSearch={value => {setSearch(value); setPage(1);}} style={{width: 320}} disabled={busy} />
          {tab === 'pending' && access.trackingForceOutbound && <Popconfirm title={`确认强制补记选中的${selected.length}个包裹？`}
            description="只扣现有库存，缺口记账实差异。"
            onConfirm={() => reconcile()} okText="确认补记出库" cancelText="取消" disabled={busy || !selected.length}>
            <Button danger type="primary" disabled={busy || !selected.length}>一键批量补记出库（{selected.length}）</Button>
          </Popconfirm>}
          {busy && <Typography.Text>{progress}</Typography.Text>}
        </Space>
        {!!results.length && <Alert type={results.some(row => row.state === 'failed') ? 'warning' : 'success'} showIcon style={{marginBottom: 12}}
          message={`成功${results.filter(row => row.state === 'succeeded').length}，已出库跳过${results.filter(row => row.state === 'skipped').length}，失败${results.filter(row => row.state === 'failed').length}`}
          description={results.filter(row => row.state === 'failed' || row.stock_result?.unaccounted_quantity).map(row => <div key={row.id}>记录{row.id}：{row.message || `账实差异${row.stock_result?.unaccounted_quantity}件，见补记记录`}</div>)} />}
        <Table<TrackingRow> rowKey="id" columns={columns} dataSource={data?.items || []} loading={loading}
          rowSelection={tab === 'pending' && access.trackingForceOutbound ? {selectedRowKeys: selected,
            onChange: keys => setSelected(keys), preserveSelectedRowKeys: true,
            getCheckboxProps: () => ({disabled: busy})} : undefined}
          onChange={(pagination, _filters, sorter) => {
            // 翻页时 antd 也会触发 Table.onChange（sorter 仍是上次的排序）；
            // 之前这里无条件 setPage(1)，导致排序过后翻页永远被拉回第 1 页。
            if (pagination?.current && pagination.current !== page) return;
            const current = Array.isArray(sorter) ? sorter[0] : sorter;
            if ((current?.columnKey ?? current?.column?.key) !== 'order') return;
            const nextSort = current?.order === 'descend' ? 'desc' : 'asc';
            if (nextSort !== orderSort) {
              setOrderSort(nextSort);
              setPage(1);
            }
          }}
          pagination={{current: page, pageSize, total: data?.total || 0, showSizeChanger: true,
            showTotal: total => `共${total}个包裹`, onChange: (next, size) => {setPage(next); setPageSize(size);}}}
          scroll={{x: 900}} />
      </Card>
    </Space>}
    <Drawer title={`${detail?.order_number || ''} · 轨迹详情`} open={Boolean(detail)} width={640} onClose={() => setDetail(undefined)}>
      <Typography.Paragraph>{detail?.number} · 共{detail?.event_count || 0}条（最多展示最近100条）</Typography.Paragraph>
      {detail?.events.map((event, index) => <Card key={`${event.happenedAt}-${index}`} size="small" style={{marginBottom: 8}}>
        <div>{formatBeijingTime(event.happenedAt)}</div><div>{event.message}</div>
      </Card>)}
    </Drawer>
    <Drawer title="物流补记与账实差异记录" open={Boolean(audits)} width={720} onClose={() => setAudits(undefined)}>
      {audits?.items.map(audit => <Card key={audit.id} size="small" style={{marginBottom: 12}} title={audit.evidence.number}>
        <div>{formatBeijingTime(audit.created_at)} · 操作人ID：{audit.user_id ?? '—'}</div>
        <div>实际扣减{audit.stock_result.deducted_quantity}件；账实差异{audit.stock_result.unaccounted_quantity}件</div>
        {audit.stock_result.shortages.map((line, index) => <div key={`${line.sku}-${index}`}>{line.title} / {line.variant_title} × {line.quantity}</div>)}
        <Typography.Text type="secondary">依据：{audit.evidence.latest.message}</Typography.Text>
      </Card>)}
      {audits?.items.length === 0 && <Empty description="暂无补记记录" />}
      {audits && <Pagination current={audits.page} pageSize={audits.per_page} total={audits.total} showSizeChanger={false} onChange={next => void showAudits(next)} />}
    </Drawer>
  </PageContainer>;
}
