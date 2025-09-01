import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Button, Tag, Space, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import React, { useRef } from 'react';
import type { ProColumns, ActionType } from '@ant-design/pro-components';
import { getOrders, type OrderItem } from '@/services/orders';
import { history } from '@umijs/max';
import dayjs from 'dayjs';

const OrderList: React.FC = () => {
  const actionRef = useRef<ActionType>(null);

  const getStatusTag = (status: string, type: 'financial' | 'fulfillment' = 'financial') => {
    if (type === 'financial') {
      const statusMap = {
        pending: { color: 'orange', text: '待付款' },
        paid: { color: 'green', text: '已付款' },
        partially_paid: { color: 'blue', text: '部分付款' },
        refunded: { color: 'red', text: '已退款' },
        voided: { color: 'gray', text: '已作废' },
      };
      const config = statusMap[status as keyof typeof statusMap] || { color: 'default', text: status };
      return <Tag color={config.color}>{config.text}</Tag>;
    } else {
      const statusMap = {
        fulfilled: { color: 'green', text: '已发货' },
        partial: { color: 'blue', text: '部分发货' },
        unfulfilled: { color: 'orange', text: '未发货' },
        cancelled: { color: 'red', text: '已取消' },
      };
      const config = statusMap[status as keyof typeof statusMap] || { color: 'default', text: status };
      return <Tag color={config.color}>{config.text}</Tag>;
    }
  };



  const columns: ProColumns<OrderItem>[] = [
    {
      title: '订单号',
      dataIndex: 'order_number',
      key: 'order_number',
      copyable: true,
      width: 150,
    },
    {
      title: 'Shopify订单ID',
      dataIndex: 'shopify_order_id',
      key: 'shopify_order_id',
      width: 120,
      hideInSearch: true,
    },
    {
      title: '客户名称',
      dataIndex: 'customer_name',
      key: 'customer_name',
      width: 120,
    },

    {
      title: '订单金额',
      dataIndex: 'total_price',
      key: 'total_price',
      valueType: 'money',
      width: 120,
      render: (_, record) => `$${record.total_price?.toFixed(2) || '0.00'}`,
      hideInSearch: true,
    },
    {
      title: '实际到账',
      dataIndex: 'actual_received',
      key: 'actual_received',
      valueType: 'money',
      width: 120,
      render: (_, record) => `$${record.actual_received?.toFixed(2) || '0.00'}`,
      hideInSearch: true,
    },
    {
      title: '毛利润(CNY)',
      dataIndex: 'gross_profit_cny',
      key: 'gross_profit_cny',
      valueType: 'money',
      width: 120,
      render: (_, record) => `¥${record.gross_profit_cny?.toFixed(2) || '0.00'}`,
      hideInSearch: true,
    },
    {
      title: '商品成本费',
      dataIndex: 'allocated_product_cost',
      key: 'allocated_product_cost',
      width: 120,
      render: (_, record) => {
        const cost = record.allocated_product_cost || 0;
        return cost > 0 ? `¥${cost.toFixed(2)}` : <span style={{ color: '#999' }}>-</span>;
      },
      hideInSearch: true,
    },
    {
      title: '物流费用',
      dataIndex: 'allocated_shipping_cost',
      key: 'allocated_shipping_cost',
      width: 120,
      render: (_, record) => {
        const cost = record.allocated_shipping_cost || 0;
        return cost > 0 ? `¥${cost.toFixed(2)}` : <span style={{ color: '#999' }}>-</span>;
      },
      hideInSearch: true,
    },
    {
      title: '实际利润',
      key: 'net_profit',
      valueType: 'money',
      width: 120,
      render: (_, record) => {
        // 使用人民币计算实际利润
        const grossProfitCny = record.gross_profit_cny || 0;
        const allocatedProductCost = record.allocated_product_cost || 0;
        const allocatedShippingCost = record.allocated_shipping_cost || 0;
        const netProfit = grossProfitCny - allocatedProductCost - allocatedShippingCost;
        return `¥${netProfit.toFixed(2)}`;
      },
      hideInSearch: true,
    },
    {
      title: '支付方式',
      dataIndex: 'payment_method',
      key: 'payment_method',
      width: 100,
      valueEnum: {
        paypal: { text: 'PayPal' },
        stripe: { text: 'Stripe' },
      },
    },

    {
      title: '发货状态',
      dataIndex: 'fulfillment_status',
      key: 'fulfillment_status',
      width: 100,
      valueEnum: {
        fulfilled: { text: '已发货', status: 'Success' },
        unfulfilled: { text: '未发货', status: 'Default' },
        partial: { text: '部分发货', status: 'Processing' },
      },
    },
    {
      title: '订单状态',
      key: 'status',
      width: 100,
      render: (_, record) => getStatusTag(record.financial_status || '', 'financial'),
      hideInSearch: true,
    },
    {
      title: '订单日期',
      dataIndex: 'order_date',
      key: 'order_date',
      valueType: 'dateRange',
      width: 150,
      search: {
        transform: (value: any) => {
          if (!value || !value[0] || !value[1]) {
            return {
              start_date: undefined,
              end_date: undefined,
            };
          }
          
          const startDate = dayjs(value[0]);
          const endDate = dayjs(value[1]);
          
          // 如果起止日期是同一天，设置时间范围为00:00到23:59:59
          if (startDate.format('YYYY-MM-DD') === endDate.format('YYYY-MM-DD')) {
            return {
              start_date: startDate.startOf('day').format('YYYY-MM-DD HH:mm:ss'),
              end_date: endDate.endOf('day').format('YYYY-MM-DD HH:mm:ss'),
            };
          }
          
          return {
            start_date: startDate.format('YYYY-MM-DD HH:mm:ss'),
            end_date: endDate.format('YYYY-MM-DD HH:mm:ss'),
          };
        },
      },
      render: (_, record) => dayjs(record.order_date).format('YYYY-MM-DD HH:mm'),
      sorter: true,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      valueType: 'dateTime',
      width: 150,
      hideInTable: true,
    },

  ];

  // 请求订单数据
  const requestOrders = async (params: any) => {
    try {
      const response = await getOrders({
        page: params.current,
        per_page: params.pageSize,
        financial_status: params.financial_status,
        fulfillment_status: params.fulfillment_status,
        // payment_method: params.payment_method, // 暂时注释掉，后续可以添加到API参数中
        search: params.customer_name || params.order_number,
        start_date: params.start_date || params.order_date?.[0],
        end_date: params.end_date || params.order_date?.[1],
        sort_by: 'created_at',
        sort_order: 'desc',
      });

      if (response.success) {
        return {
          data: response.data.orders,
          success: true,
          total: response.data.pagination.total,
        };
      } else {
        message.error(response.message || '获取订单列表失败');
        return {
          data: [],
          success: false,
          total: 0,
        };
      }
    } catch (error) {
      console.error('获取订单列表失败:', error);
      message.error('获取订单列表失败');
      return {
        data: [],
        success: false,
        total: 0,
      };
    }
  };

  return (
    <PageContainer
      title="订单管理"
      subTitle="管理所有订单信息"
      extra={[
        <Button key="add" type="primary" icon={<PlusOutlined />}>
          新建订单
        </Button>,
      ]}
    >
      <ProTable<OrderItem>
        columns={columns}
        actionRef={actionRef}
        request={requestOrders}
        rowKey="id"
        search={{
          labelWidth: 'auto',
          defaultCollapsed: false,
          collapseRender: (collapsed: boolean) => {
             if (collapsed) {
               return [
                 <a key="collapsed">
                   展开 <span style={{ marginLeft: 8 }}>↓</span>
                 </a>,
               ];
             }
             return [
               <a key="expand">
                 收起 <span style={{ marginLeft: 8 }}>↑</span>
               </a>,
             ];
           },
        }}
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条/总共 ${total} 条`,
        }}
        dateFormatter="string"
        headerTitle="订单列表"
        scroll={{ x: 1500 }}
        toolBarRender={() => [
          <Button key="refresh" onClick={() => actionRef.current?.reload()}>
            刷新
          </Button>,
          <Button key="export" type="default">
            导出数据
          </Button>,
        ]}
      />
    </PageContainer>
  );
};

export default OrderList;