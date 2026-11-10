import { ArrowDownOutlined, ArrowUpOutlined, ReloadOutlined } from '@ant-design/icons';
import { Alert, Button, Card, DatePicker, Empty, Image, Input, Select, Space, Spin, Statistic, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs, { type Dayjs } from 'dayjs';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  listInventoryMovements,
  type InventoryMovement,
  type InventoryMovementDay,
  type InventoryMovementsResult,
} from '@/services/inventory';
import { getErrorMessage } from '@/utils/error';
import { formatBeijingTime } from '@/utils/timezone';

interface Props {
  shopId: number | null;
}

/** 库存的每日出入库流水：入库来自采购到货，出库来自扫码出库与人工调整。 */
const Movements: React.FC<Props> = ({ shopId }) => {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(6, 'day').startOf('day'),
    dayjs().endOf('day'),
  ]);
  const [data, setData] = useState<InventoryMovementsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [movementType, setMovementType] = useState('all');
  const [direction, setDirection] = useState('all');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    if (!shopId) return;
    const id = ++requestId.current;
    setLoading(true);
    setError('');
    try {
      const response = await listInventoryMovements({
        shop_id: shopId,
        start_date: range[0].format('YYYY-MM-DD'),
        end_date: range[1].format('YYYY-MM-DD'),
        movement_type: movementType, direction, search, page, per_page: pageSize,
      });
      if (id === requestId.current) setData(response.data);
    } catch (err) {
      if (id === requestId.current) { setError(getErrorMessage(err, '获取出入库流水失败')); setData(null); }
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [shopId, range, movementType, direction, search, page, pageSize]);

  useEffect(() => {
    void load();
    return () => { requestId.current++; };
  }, [load]);

  useEffect(() => { setPage(1); setData(null); }, [shopId]);

  if (!shopId) {
    return <Empty description="请先在顶部选择具体店铺" />;
  }

  const dayColumns: ColumnsType<InventoryMovementDay> = [
    { title: '日期', dataIndex: 'date', width: 140 },
    {
      title: '入库',
      dataIndex: 'in_quantity',
      align: 'right',
      width: 140,
      render: (value: number, row) => (
        <Space size={4}>
          <Typography.Text strong style={{ color: '#16A34A' }}>
            +{value}
          </Typography.Text>
          <Typography.Text type="secondary">({row.in_records} 笔)</Typography.Text>
        </Space>
      ),
    },
    {
      title: '出库',
      dataIndex: 'out_quantity',
      align: 'right',
      width: 140,
      render: (value: number, row) => (
        <Space size={4}>
          <Typography.Text strong style={{ color: '#DC2626' }}>
            -{value}
          </Typography.Text>
          <Typography.Text type="secondary">({row.out_records} 笔)</Typography.Text>
        </Space>
      ),
    },
    {
      title: '净变化',
      key: 'net',
      align: 'right',
      width: 120,
      render: (_, row) => {
        const net = row.in_quantity - row.out_quantity;
        return (
          <Typography.Text strong type={net === 0 ? 'secondary' : net > 0 ? 'success' : 'danger'}>
            {net > 0 ? `+${net}` : net}
          </Typography.Text>
        );
      },
    },
  ];

  const movementColumns: ColumnsType<InventoryMovement> = [
    {
      title: '时间',
      dataIndex: 'at',
      width: 170,
      render: (value: string) => formatBeijingTime(value),
    },
    { title: '业务类型', dataIndex: 'movement_type_label', width: 115,
      render: (value: string, row) => <Tag color={row.movement_type === 'scan_outbound' ? 'blue'
        : row.movement_type === 'manual_adjustment' ? 'orange' : 'default'}>{value}</Tag> },
    { title: '订单 / 售后单', dataIndex: 'order_number', width: 125, render: (value: string) => value || '—' },
    {
      title: '类型',
      dataIndex: 'direction',
      width: 90,
      render: (value: 'in' | 'out') =>
        value === 'in' ? (
          <Tag color="green" icon={<ArrowDownOutlined />}>
            入库
          </Tag>
        ) : (
          <Tag color="red" icon={<ArrowUpOutlined />}>
            出库
          </Tag>
        ),
    },
    {
      title: '商品',
      dataIndex: 'title',
      render: (_, row) => (
        <Space size={10}>
          {row.image_url ? (
            <Image src={row.image_url} width={40} height={40} preview={false} style={{ objectFit: 'cover', borderRadius: 6 }} />
          ) : (
            <div style={{ width: 40, height: 40, borderRadius: 6, background: '#f1f5f9' }} />
          )}
          <div>
            <div>{row.variant_title || row.title}</div>
            {row.variant_title && <Typography.Text type="secondary">{row.title}</Typography.Text>}
          </div>
        </Space>
      ),
    },
    {
      title: '数量',
      dataIndex: 'quantity',
      width: 100,
      align: 'right',
      render: (value: number, row) => (
        <Typography.Text strong style={{ color: row.direction === 'in' ? '#16A34A' : '#DC2626' }}>
          {row.direction === 'in' ? `+${value}` : `-${value}`}
        </Typography.Text>
      ),
    },
    { title: '来源', dataIndex: 'source', ellipsis: true },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space wrap align="center">
          <span>统计区间：</span>
          <DatePicker.RangePicker
            value={range}
            onChange={(dates) => {
              if (dates?.[0] && dates?.[1]) { setRange([dates[0], dates[1]]); setPage(1); }
            }}
            allowClear={false}
          />
          <Select aria-label="流水业务类型" value={movementType} style={{ width: 155 }} onChange={(value) => { setMovementType(value); setPage(1); }}
            options={[
              { value: 'all', label: '全部业务类型' }, { value: 'scan_outbound', label: '扫码出库' },
              { value: 'manual_adjustment', label: '手动调整' }, { value: 'purchase_receipt', label: '采购入库' },
              { value: 'stock_transfer', label: '库存调配' }, { value: 'scan_undo', label: '撤回出库' },
            ]} />
          <Select aria-label="出入库方向" value={direction} style={{ width: 120 }} onChange={(value) => { setDirection(value); setPage(1); }}
            options={[{ value: 'all', label: '全部方向' }, { value: 'in', label: '入库' }, { value: 'out', label: '出库' }]} />
          <Input.Search aria-label="搜索库存流水" value={query} allowClear placeholder="订单号 / 商品 / 型号 / SKU"
            style={{ width: 280, maxWidth: '100%' }} onChange={(event) => { setQuery(event.target.value); if (!event.target.value) { setSearch(''); setPage(1); } }}
            onSearch={(value) => { setSearch(value.trim()); setPage(1); }} />
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
            刷新
          </Button>
          <Typography.Text type="secondary">按北京时间统计</Typography.Text>
        </Space>
      </Card>

      {error && <Card size="small">{error}</Card>}

      <div className="inventory-summary">
        <Statistic title="区间入库" value={data?.totals.in_quantity || 0} valueStyle={{ color: '#16A34A' }} />
        <Statistic title="区间出库" value={data?.totals.out_quantity || 0} valueStyle={{ color: '#DC2626' }} />
        <Statistic
          title="净变化"
          value={(data?.totals.in_quantity || 0) - (data?.totals.out_quantity || 0)}
        />
        <Statistic title="有记录天数" value={data?.days.length || 0} />
      </div>

      {data && data.totals.out_quantity === 0 && (
        <Alert
          type="info"
          showIcon
          message="当前筛选条件下没有出库记录"
        />
      )}

      <Card size="small" title="按日汇总">
        <Table<InventoryMovementDay>
          rowKey="date"
          size="small"
          loading={loading}
          columns={dayColumns}
          dataSource={data?.days || []}
          pagination={false}
          locale={{ emptyText: '所选区间没有出入库记录' }}
        />
      </Card>

      <Card size="small" title={`出入库明细（${data?.pagination.total || 0} 条）`}>
        {loading && !data ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : (
          <Table<InventoryMovement>
            rowKey="id"
            size="small"
            columns={movementColumns}
            loading={loading}
            scroll={{ x: 1000 }}
            dataSource={data?.movements || []}
            pagination={{ current: page, pageSize, total: data?.pagination.total || 0, showSizeChanger: true,
              onChange: (nextPage, nextSize) => { setPage(nextSize === pageSize ? nextPage : 1); setPageSize(nextSize); } }}
            locale={{ emptyText: '所选区间没有出入库记录' }}
          />
        )}
      </Card>
    </Space>
  );
};

export default Movements;
