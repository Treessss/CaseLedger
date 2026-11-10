import { history } from '@umijs/max';
import { Alert, Button, Drawer, Empty, Grid, Input, Pagination, Space, Tag, Typography } from 'antd';
import React, { useMemo, useState } from 'react';
import type { InventoryItem, InventoryOccupiedOrder } from '@/services/inventory/api';

type DemandOrder = InventoryOccupiedOrder & { lines: InventoryItem[] };

// 原单与售后单各自计数；同一订单的不同型号仍保留明细，不合并变体。
export function collectDemandOrders(variants: InventoryItem[]): DemandOrder[] {
  const orders = new Map<string, DemandOrder>();
  for (const variant of variants) {
    for (const entry of variant.demand_orders || []) {
      if (entry.quantity <= 0) continue;
      const key = entry.after_sale_id ? `cf:${entry.after_sale_id}` : `order:${entry.order_id}`;
      let order = orders.get(key);
      if (!order) {
        order = { ...entry, quantity: 0, lines: [] };
        orders.set(key, order);
      }
      order.quantity += entry.quantity;
      order.lines.push({ ...variant, demand_quantity: entry.quantity });
    }
  }
  return Array.from(orders.values()).sort((a, b) => a.order_number.localeCompare(b.order_number, 'zh-CN', { numeric: true }));
}

export default function InventoryDemandOrders({ title, variants }: { title: string; variants: InventoryItem[] }) {
  const screens = Grid.useBreakpoint();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const orders = useMemo(() => collectDemandOrders(variants), [variants]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return orders.filter((order) => !query || [order.order_number, ...order.lines.flatMap((line) => [line.color, line.model, line.sku])]
      .some((text) => text.toLowerCase().includes(query)));
  }, [orders, search]);
  const currentPage = Math.min(page, Math.max(1, Math.ceil(filtered.length / 20)));
  return <div onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
    <Button type="link" style={{ minHeight: 44, paddingInline: 0 }} disabled={!orders.length}
      onClick={() => { setSearch(''); setPage(1); setOpen(true); }}>
      {orders.length ? `查看需求订单（${orders.length} 单）` : '暂无需求订单'}
    </Button>
    <Drawer title={`需求订单 · ${title}`} open={open} onClose={() => setOpen(false)} width={screens.md ? 680 : '100%'}>
      <Alert type="info" showIcon message="这些订单需要此商品，仅用于查看需求，不占用或绑定库存。实际扫码出库时才扣减库存。" style={{ marginBottom: 16 }} />
      <Input.Search aria-label="搜索需求订单" placeholder="搜索订单号、CF 售后单、颜色、型号或 SKU" allowClear
        value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} style={{ marginBottom: 12 }} />
      <Typography.Paragraph type="secondary">{filtered.length} 单 · 共 {filtered.reduce((sum, order) => sum + order.quantity, 0)} 件需求</Typography.Paragraph>
      {filtered.length ? filtered.slice((currentPage - 1) * 20, currentPage * 20).map((order) => <section key={order.after_sale_id ? `cf:${order.after_sale_id}` : `order:${order.order_id}`}
        aria-label={`${order.order_number}需求明细`} style={{ border: '1px solid #e8e8e8', borderRadius: 8, padding: 12, marginBottom: 12 }}>
        <Space wrap style={{ marginBottom: 8 }}>
          <Button type="link" style={{ minHeight: 44, paddingInline: 0 }} onClick={() => history.push(order.after_sale_id
            ? `/after-sales?after_sale_id=${order.after_sale_id}` : `/orders/detail/${order.order_id}`)}>{order.order_number}</Button>
          <Tag color={order.after_sale_id ? 'purple' : 'blue'}>{order.after_sale_id ? '售后补发' : '原订单'}</Tag>
          <Tag color="gold">需要 {order.quantity} 件</Tag>
        </Space>
        {order.lines.map((line, index) => <div key={`${line.group_key}:${index}`} style={{ display: 'flex', gap: 12, justifyContent: 'space-between', paddingBlock: 6 }}>
          <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
            <Typography.Text>{line.color || '未标注颜色'} / </Typography.Text><Typography.Text strong>{line.model || '默认款'}</Typography.Text>
            {line.sku && <div><Typography.Text type="secondary">SKU：{line.sku}</Typography.Text></div>}
          </div>
          <Typography.Text strong style={{ whiteSpace: 'nowrap' }}>× {line.demand_quantity}</Typography.Text>
        </div>)}
      </section>) : <Empty description="没有匹配的需求订单" />}
      {filtered.length > 20 && <Pagination current={currentPage} pageSize={20} total={filtered.length}
        onChange={setPage} showSizeChanger={false} simple />}
    </Drawer>
  </div>;
}
