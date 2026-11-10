import { ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import { Alert, Button, Card, DatePicker, Drawer, Input, message, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import React, { useCallback, useEffect, useState } from 'react';
import {
  applyFreightCosts,
  reconcileFreightCosts,
  type FreightCostReconcile,
  type FreightCostRow,
} from '@/services/expenses/freight';
import { syncFourPxFreight } from '@/services/orders/api';
import { getErrorMessage } from '@/utils/error';

const statusColor: Record<string, string> = {
  matched: 'green', diff: 'orange', cost_missing: 'red', freight_missing: 'blue',
  no_4px: 'default', cost_no_freight: 'purple',
};

interface Props {
  shopId: number | null;
  open: boolean;
  onClose: () => void;
}

export default function FreightReconcileDrawer({ shopId, open, onClose }: Props) {
  const [data, setData] = useState<FreightCostReconcile>();
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(30, 'day'), dayjs()]);
  const [status, setStatus] = useState<string>();
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<number[]>([]);

  const load = useCallback(async (nextPage = page) => {
    if (!shopId) return;
    setLoading(true);
    try {
      const response = await reconcileFreightCosts({
        shop_id: shopId, date_from: range[0].format('YYYY-MM-DD'), date_to: range[1].format('YYYY-MM-DD'),
        status, search: search.trim() || undefined, page: nextPage, per_page: 20,
      });
      setData(response.data);
      setPage(nextPage);
    } catch (error) {
      message.error(getErrorMessage(error, '加载物流费对账失败'));
    } finally {
      setLoading(false);
    }
  }, [shopId, range, status, search, page]);

  useEffect(() => { if (open && shopId) void load(1); }, [open, shopId]); // eslint-disable-line react-hooks/exhaustive-deps

  const syncMissing = async () => {
    if (!shopId) return;
    const ids = selected.length ? selected : (data?.items || []).filter((row) => row.status === 'freight_missing').map((row) => row.order_id);
    if (!ids.length) { message.info('没有需要同步 4PX 费用的订单'); return; }
    if (ids.length > 50) { message.warning('一次最多同步 50 单，请先勾选'); return; }
    setActionLoading(true);
    try {
      await syncFourPxFreight(ids);
      message.success(`已提交 ${ids.length} 单的 4PX 费用同步，稍后刷新查看`);
      await load(1);
    } catch (error) {
      message.error(getErrorMessage(error, '同步 4PX 费用失败'));
    } finally {
      setActionLoading(false);
    }
  };

  const fillMissing = async () => {
    if (!shopId) return;
    const ids = selected.length ? selected
      : (data?.items || []).filter((row) => row.status === 'cost_missing').map((row) => row.order_id);
    if (!ids.length) { message.info('没有可补记的订单'); return; }
    if (ids.length > 100) { message.warning('一次最多补记 100 单'); return; }
    setActionLoading(true);
    try {
      const response = await applyFreightCosts({ shop_id: shopId, order_ids: ids });
      message.success(`已补记 ${response.data.created} 笔，跳过 ${response.data.skipped} 笔`);
      setSelected([]);
      await load(1);
    } catch (error) {
      message.error(getErrorMessage(error, '补记物流费失败'));
    } finally {
      setActionLoading(false);
    }
  };

  const columns: ColumnsType<FreightCostRow> = [
    { title: '订单号', dataIndex: 'order_number', width: 110,
      render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
    { title: '日期', dataIndex: 'order_date', width: 100,
      render: (value?: string) => value || '—' },
    { title: '4PX 单号', dataIndex: 'tracking_number', width: 175,
      render: (value?: string) => value ? <Typography.Text copyable style={{ fontSize: 12 }}>{value}</Typography.Text> : '—' },
    { title: '4PX 费用', dataIndex: 'freight_total', width: 100, align: 'right',
      render: (value: number | null | undefined) => value == null ? '—' : `¥${value.toFixed(2)}` },
    { title: '已记物流费', dataIndex: 'cost_shipping', width: 120, align: 'right',
      render: (value: number, row) => <div>
        <div>¥{(value || 0).toFixed(2)}</div>
        {row.cost_source && <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {row.cost_source === '4px' ? '4PX' : row.cost_source === 'bill' ? '账单' : '手填'}
        </Typography.Text>}
      </div> },
    { title: '差额', dataIndex: 'diff', width: 90, align: 'right',
      render: (value: number | null | undefined) => value == null ? '—'
        : <Typography.Text type={Math.abs(value) > 0.01 ? 'warning' : 'secondary'}>¥{value.toFixed(2)}</Typography.Text> },
    { title: '状态', dataIndex: 'status', width: 130,
      render: (value: string) => <Tag color={statusColor[value] || 'default'}>{data?.statuses?.[value] || value}</Tag> },
    { title: '操作', width: 90, render: (_, row) => row.status === 'cost_missing'
      ? <Button size="small" type="link" onClick={async () => {
          setActionLoading(true);
          try {
            await applyFreightCosts({ shop_id: shopId as number, order_ids: [row.order_id] });
            message.success('已补记');
            await load();
          } finally { setActionLoading(false); }
        }}>补记</Button>
      : '—' },
  ];

  const counts = data?.summary.counts || {};

  return <Drawer
    title="4PX 物流费对账 / 迁移"
    width={1180}
    open={open}
    onClose={onClose}
    destroyOnClose
  >
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert type="info" showIcon message="这里只补「4PX 已有真实费用、订单还没记物流费」的订单；已有手工/账单费用不会覆盖，也不会自动扣账户余额。" />
      <Space wrap>
        <DatePicker.RangePicker value={range} onChange={(value) => value && setRange([value[0] as Dayjs, value[1] as Dayjs])} allowClear={false} />
        <Select allowClear placeholder="全部状态" style={{ width: 150 }} value={status}
          onChange={setStatus}
          options={Object.entries(data?.statuses || {}).map(([value, label]) => ({ value, label }))} />
        <Input.Search allowClear placeholder="订单号 / 4PX 单号" style={{ width: 220 }}
          onSearch={(value) => { setSearch(value); void load(1); }} />
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load(1)}>刷新</Button>
        <Button icon={<SyncOutlined />} loading={actionLoading} onClick={() => void syncMissing()}>
          同步 4PX 费用{selected.length ? `（${selected.length}）` : ''}
        </Button>
        <Button type="primary" loading={actionLoading} onClick={() => void fillMissing()}>
          补记未记物流费{selected.length ? `（${selected.length}）` : ''}
        </Button>
      </Space>
      <Space size={24} wrap>
        <Statistic title="订单数" value={data?.summary.orders || 0} />
        <Statistic title="待同步 4PX 费用" value={counts.freight_missing || 0} />
        <Statistic title="未记物流费" value={counts.cost_missing || 0} valueStyle={{ color: '#cf1322' }} />
        <Statistic title="金额不一致" value={counts.diff || 0} valueStyle={{ color: '#d46b08' }} />
        <Statistic title="已记且一致" value={counts.matched || 0} valueStyle={{ color: '#3f8600' }} />
        <Statistic title="无 4PX 单号" value={counts.no_4px || 0} />
      </Space>
      <Table<FreightCostRow>
        rowKey="order_id"
        size="small"
        loading={loading}
        dataSource={data?.items || []}
        columns={columns}
        scroll={{ x: 1000 }}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys as number[]) }}
        pagination={{
          current: page, pageSize: 20, total: data?.pagination.total || 0,
          showTotal: (total) => `共 ${total} 单`, onChange: (next) => void load(next),
        }}
      />
    </Space>
  </Drawer>;
}
