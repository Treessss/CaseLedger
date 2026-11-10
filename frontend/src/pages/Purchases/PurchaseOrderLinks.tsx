import React from 'react';
import { Button, Input, Popover, Tag, Typography } from 'antd';
import { history } from '@umijs/max';
import type { PurchaseListDetail } from '@/services/purchases';

export default function PurchaseOrderLinks({ purchase }: { purchase: Pick<PurchaseListDetail, 'orders' | 'after_sale_orders'> }) {
  const [search, setSearch] = React.useState('');
  const orders = purchase.orders || [];
  const sales = purchase.after_sale_orders || [];
  const active = sales.filter((sale) => !sale.removed);
  const removed = sales.filter((sale) => sale.removed);
  const keyword = search.trim().replace(/^#/, '').toLowerCase();
  const matches = (number: string) => number.toLowerCase().includes(keyword);
  if (!orders.length && !sales.length) return null;
  const renderSale = (sale: typeof sales[number]) => (
    <div key={sale.id} style={{ padding: '4px 0' }}>
      <Tag color={sale.removed ? 'default' : 'purple'}>{sale.customer_reference}</Tag>
      <Button type="link" size="small" onClick={() => history.push(`/orders/detail/${sale.order_id}`)}>
        原单 {sale.order_number}
      </Button>
      {sale.deleted && <Typography.Text type="secondary">已删除售后</Typography.Text>}
      {sale.removed && <Tag>已移出采购 · 不计采购数量</Tag>}
    </div>
  );
  return <div style={{ marginTop: 8 }}>
    <Popover trigger="click" placement="bottomLeft" title="关联订单与售后" content={
      <div style={{ maxHeight: 360, overflow: 'auto', minWidth: 260 }}>
        <Input allowClear placeholder="查找原单号 / CF 售后单号" value={search} onChange={(event) => setSearch(event.target.value)} style={{ marginBottom: 8 }} />
        {!!active.length && <Typography.Text strong>售后采购 · {active.length} 单</Typography.Text>}
        {active.filter((sale) => matches(sale.customer_reference) || matches(sale.order_number)).map(renderSale)}
        {!!removed.length && <Typography.Text strong>历史关联（已移出采购）· {removed.length} 单</Typography.Text>}
        {removed.filter((sale) => matches(sale.customer_reference) || matches(sale.order_number)).map(renderSale)}
        {!!orders.length && <Typography.Text strong>原订单 · {orders.length} 单</Typography.Text>}
        {orders.filter((order) => matches(order.order_number)).map((order) => <div key={order.id}>
          <Button type="link" size="small" onClick={() => history.push(`/orders/detail/${order.id}`)}>{order.order_number}</Button>
        </div>)}
      </div>
    }>
      <Button size="small">关联订单 {orders.length + active.length} 单{removed.length ? ` · 历史关联 ${removed.length} 单` : ''}</Button>
    </Popover>
  </div>;
}
