import { history } from '@umijs/max';
import { Button, Popover, Space, Tag, Typography } from 'antd';
import React from 'react';
import type { InventoryPoolMetrics } from '@/services/inventory/api';

const metrics = [
  ['demand_quantity', '总需求', 'gold'], ['incoming_quantity', '已采购未到', 'blue'],
  ['on_hand_quantity', '在库', 'green'], ['purchase_needed_quantity', '按型号还差', 'orange'],
] as const;

const metricHints: Record<string, string> = {
  demand_quantity: '所有未出库原单和售后的件数',
  incoming_quantity: '已发起采购、还没入库的件数',
  on_hand_quantity: '仓库当前可用件数',
  purchase_needed_quantity: '按颜色/型号分别扣除在库和已采购未到后的缺口，不能跨型号互抵',
};

export default function InventoryQuantities({data}: {data: InventoryPoolMetrics}) {
  return <Space wrap size={[4, 4]}>
    {metrics.map(([key, title, color]) => {
      const quantity = data[key] || 0;
      const entries = key === 'incoming_quantity' ? data.incoming_purchases || []
        : key === 'purchase_needed_quantity' ? data.purchase_orders || []
          : key === 'demand_quantity' ? data.demand_orders || [] : [];
      const contents = <div style={{maxHeight: 280, overflowY: 'auto', minWidth: 180}}>
        {entries.map((entry, index) => 'purchase_id' in entry ? <div key={`${entry.purchase_id}:${index}`}>
          <Button type="link" onClick={() => history.push(`/purchases?purchase_id=${entry.purchase_id}`)}>采购清单 #{entry.purchase_id}</Button>
          <Tag>{entry.quantity} 件</Tag>
        </div> : <div key={`${entry.order_id}:${entry.after_sale_id}:${index}`}>
          {entry.order_id ? <Button type="link" onClick={() => history.push(entry.after_sale_id ? `/after-sales?after_sale_id=${entry.after_sale_id}` : `/orders/detail/${entry.order_id}`)}>
            {entry.order_number}
          </Button> : <Typography.Text>{entry.order_number}</Typography.Text>}
          <Tag>{entry.quantity} 件</Tag>
        </div>)}
      </div>;
      return <Popover key={key} trigger="click" title={`${title} · ${quantity} 件`} content={contents} open={entries.length ? undefined : false}>
        <Button size="small" style={{height: 44, padding: '0 8px'}} aria-label={`${title} ${quantity} 件${entries.length ? '，点击查看明细' : ''}`} title={metricHints[key]}>
          <Typography.Text>{title}</Typography.Text> <Tag color={color} style={{marginInlineEnd: 0}}>{quantity}</Tag>
        </Button>
      </Popover>;
    })}
  </Space>;
}
