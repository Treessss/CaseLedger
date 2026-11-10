import { history } from '@umijs/max';
import { Alert, Button, DatePicker, Drawer, Empty, Grid, Input, Pagination, Select, Space, Spin, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import React, { useEffect, useRef, useState } from 'react';
import { listOutboundFailures } from '@/services/shipping/api';
import type { OutboundFailurePage } from '@/services/shipping/api';
import { getErrorMessage } from '@/utils/error';
import { formatBeijingTime, getCurrentBeijingTime } from '@/utils/timezone';

export default function FailureRecords({ shopId, onClosed }: { shopId: number; onClosed?: () => void }) {
  const screens = Grid.useBreakpoint();
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(() => getCurrentBeijingTime('YYYY-MM-DD'));
  const [category, setCategory] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<OutboundFailurePage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const seq = useRef(0);
  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(search.trim()); setPage(1); }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    if (!open) return;
    const current = ++seq.current;
    setBusy(true);
    setError('');
    void listOutboundFailures({ shop_id: shopId, date, category: category || undefined, search: query || undefined, page })
      .then((response) => { if (seq.current === current) setData(response.data); })
      .catch((reason) => { if (seq.current === current) { setData(null); setError(getErrorMessage(reason, '获取异常记录失败')); } })
      .finally(() => { if (seq.current === current) setBusy(false); });
    return () => { seq.current += 1; };
  }, [open, shopId, date, category, query, page, refresh]);
  return <>
    <Button style={{ minHeight: 44 }} onClick={() => {
      setDate(getCurrentBeijingTime('YYYY-MM-DD')); setPage(1); setRefresh((value) => value + 1); setOpen(true);
    }}>出库异常记录</Button>
    <Drawer title="每日出库异常记录" open={open} onClose={() => setOpen(false)} width={screens.md ? 760 : '100%'}
      afterOpenChange={(visible) => { if (!visible) onClosed?.(); }}>
      <Alert type="info" showIcon message="按北京时间保存；同一原因合并计数" style={{ marginBottom: 16 }} />
      <Space wrap style={{ marginBottom: 12 }}>
        <DatePicker aria-label="异常日期（北京时间）" allowClear={false} value={dayjs(date)}
          onChange={(value) => { if (value) { setDate(value.format('YYYY-MM-DD')); setPage(1); } }} />
        <Select aria-label="异常分类" value={category} style={{ minWidth: 170 }}
          options={[{ value: '', label: '全部分类' }, ...Object.entries(data?.categories || {}).map(([value, label]) => ({ value, label }))]}
          onChange={(value) => { setCategory(value); setPage(1); }} />
        <Button onClick={() => setRefresh((value) => value + 1)} loading={busy}>刷新</Button>
      </Space>
      <Input.Search aria-label="搜索异常单号" allowClear placeholder="搜索订单号、CF 售后单或物流条码"
        value={search} onChange={(event) => setSearch(event.target.value)} style={{ marginBottom: 12 }} />
      <Space wrap style={{ marginBottom: 12 }}>
        {Object.entries(data?.summary || {}).map(([key, counts]) => <Tag key={key}>{data?.categories[key] || key}：{counts.records} 条 / {counts.attempts} 次</Tag>)}
      </Space>
      {error && <Alert type="error" showIcon message={error} />}
      <Spin spinning={busy}>
        {data?.records.length ? data.records.map((record) => <section key={record.id}
          aria-label={`${record.scan_code}异常记录`} style={{ border: '1px solid #e8e8e8', borderRadius: 8, padding: 12, marginBottom: 12, overflowWrap: 'anywhere' }}>
          <Space wrap>
            <Tag color="orange">{data.categories[record.category] || record.category}</Tag>
            {record.order_id ? <Button type="link" style={{ minHeight: 44 }} onClick={() => history.push(record.after_sale_id
              ? `/after-sales?after_sale_id=${record.after_sale_id}` : `/orders/detail/${record.order_id}`)}>{record.order_number || record.order_id}</Button>
              : <Typography.Text type="secondary">未匹配订单</Typography.Text>}
            <Tag>{record.attempts} 次</Tag>
          </Space>
          <div><Typography.Text strong>{record.scan_code}</Typography.Text></div>
          <Typography.Paragraph style={{ marginBlock: 8 }}>{record.reason}</Typography.Paragraph>
          <Typography.Text type="secondary">首次 {formatBeijingTime(record.created_at)} · 最近 {formatBeijingTime(record.last_seen_at)}</Typography.Text>
          {record.operator && <div><Typography.Text type="secondary">最近操作人：{record.operator}</Typography.Text></div>}
        </section>) : !busy && !error && <Empty description="当天没有此类出库异常" />}
      </Spin>
      {data && data.pagination.total > 20 && <Pagination simple current={page} total={data.pagination.total} pageSize={20}
        showSizeChanger={false} onChange={setPage} />}
    </Drawer>
  </>;
}
