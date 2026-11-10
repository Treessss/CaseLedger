import { Space, Tag, Typography } from 'antd';
import React from 'react';
import type { AfterSale } from '@/services/afterSales';

const procurementNames = {
  not_started: '未发起采购', draft: '待确认采购', ordered: '采购中',
  partially_received: '分批入库中', received: '已完成入库', history: '历史采购待核对',
};

export default function AfterSaleStates({ record }: { record: AfterSale }) {
  const procurement = record.procurement_status;
  const stock = record.stock_status || (record.status === 'shipped' ? 'shipped' : record.status === 'ready' ? 'ready' : 'missing');
  const missing = record.stock_missing_quantity ?? record.items.reduce((sum, item) => sum + item.needed_quantity, 0);
  const stockName = stock === 'shipped' ? '已扫码出库' : stock === 'ready' ? '现货可满足本单'
    : stock === 'invalid' ? '无有效补发商品' : `本单缺 ${missing} 件`;
  return <Space direction="vertical" size={4}>
    <div><Typography.Text type="secondary">采购：</Typography.Text>
      <Tag color={procurement === 'received' ? 'blue' : procurement === 'ordered' || procurement === 'partially_received' ? 'orange' : 'default'}>
        {procurement ? procurementNames[procurement] : '待刷新核对'}</Tag></div>
    <div><Typography.Text type="secondary">库存：</Typography.Text>
      <Tag color={stock === 'ready' ? 'green' : stock === 'shipped' ? 'blue' : 'orange'}>{stockName}</Tag></div>
  </Space>;
}
