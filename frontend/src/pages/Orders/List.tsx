import type {
  ActionType,
  ProColumns,
  ProFormInstance,
} from '@ant-design/pro-components';
import { CopyOutlined, ShoppingCartOutlined } from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Button, Card, message, Space, Tag, Tooltip, Typography } from 'antd';
import dayjs from 'dayjs';
import React, { useRef, useState } from 'react';
import { getOrders, type OrderItem, type OrderListParams } from '@/services/orders';
import { getExchangeRates } from '@/services/exchange';
import { exportCsv } from '@/utils/exportCsv';
import { persistedPagination } from '@/utils/pagination';
import {
  formatBeijingTime,
  getCurrentBeijingTime,
} from '@/utils/timezone';

/** 金额格式化：千分位 + 两位小数（列表里数字右对齐更好扫读） */
const money = (value: number | undefined | null, symbol = '¥') =>
  `${symbol}${(Number(value) || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

const OrderList: React.FC = () => {
  const actionRef = useRef<ActionType>(null);
  // 不传初始值：ProTable 的 formRef 要求 current 是 `T | undefined`，
  // 写成 useRef<T>(null) 会变成 `T | null`，类型不匹配。
  const formRef = useRef<ProFormInstance | undefined>(undefined);
  const quickSubmitRef = useRef(false);
  const lastQueryRef = useRef<OrderListParams>({});
  const rateRefreshStarted = useRef(false);
  const [exporting, setExporting] = useState(false);
  const [quickFilter, setQuickFilter] = useState('all');

  const applyQuickFilter = (
    key: string,
    values: {
      warehouse_status?: string;
      order_date?: [dayjs.Dayjs, dayjs.Dayjs];
    },
  ) => {
    quickSubmitRef.current = true;
    setQuickFilter(key);
    formRef.current?.resetFields();
    formRef.current?.setFieldsValue(values);
    formRef.current?.submit();
    window.setTimeout(() => {
      quickSubmitRef.current = false;
    }, 0);
  };

  const today = dayjs(getCurrentBeijingTime('YYYY-MM-DD'));
  const quickFilters = [
    { key: 'all', label: '全部', values: {} },
    {
      key: 'pending',
      label: '待出库',
      values: { warehouse_status: 'pending' },
    },
    {
      key: 'shipped',
      label: '已出库',
      values: { warehouse_status: 'shipped' },
    },
    {
      key: 'today',
      label: '今日订单',
      values: {
        order_date: [today.startOf('day'), today.endOf('day')] as [
          dayjs.Dayjs,
          dayjs.Dayjs,
        ],
      },
    },
    {
      key: 'week',
      label: '近 7 天',
      values: {
        order_date: [
          today.subtract(6, 'day').startOf('day'),
          today.endOf('day'),
        ] as [dayjs.Dayjs, dayjs.Dayjs],
      },
    },
  ];

  const getStatusTag = (
    status: string,
    type: 'financial' | 'fulfillment' = 'financial',
  ) => {
    if (type === 'financial') {
      const statusMap = {
        pending: { color: 'orange', text: '待付款' },
        paid: { color: 'green', text: '已付款' },
        partially_paid: { color: 'blue', text: '部分付款' },
        partially_refunded: { color: 'volcano', text: '部分退款' },
        refunded: { color: 'red', text: '已退款' },
        voided: { color: 'gray', text: '已作废' },
      };
      const config = statusMap[status as keyof typeof statusMap] || {
        color: 'default',
        text: status,
      };
      return <Tag color={config.color}>{config.text}</Tag>;
    } else {
      const statusMap = {
        fulfilled: { color: 'green', text: '已发货' },
        partial: { color: 'blue', text: '部分发货' },
        unfulfilled: { color: 'orange', text: '未发货' },
        cancelled: { color: 'red', text: '已取消' },
      };
      const config = statusMap[status as keyof typeof statusMap] || {
        color: 'default',
        text: status,
      };
      return <Tag color={config.color}>{config.text}</Tag>;
    }
  };

  const columns: ProColumns<OrderItem>[] = [
    {
      title: '订单号',
      dataIndex: 'order_number',
      key: 'order_number',
      width: 150,
      fixed: 'left',
      render: (_, record) => (
        <Space size={4}>
          <a
            style={{ color: 'var(--cl-primary)', textDecoration: 'underline' }}
            onClick={(event) => {
              event.stopPropagation();
              history.push(`/orders/detail/${record.id}`);
            }}
          >
            {record.order_number}
          </a>
          <Typography.Text copyable={{ text: record.order_number }} />
        </Space>
      ),
    },
    {
      // 顶部选「全部店铺」时会混排多家店的订单，靠这列区分归属
      title: '店铺',
      dataIndex: 'shop_name',
      key: 'shop_name',
      width: 120,
      hideInSearch: true,
      render: (_, record) =>
        record.shop_name ? (
          <Tag color="blue">{record.shop_name}</Tag>
        ) : (
          <span style={{ color: '#999' }}>-</span>
        ),
    },
    // {
    //   title: '客户名称',
    //   dataIndex: 'customer_name',
    //   key: 'customer_name',
    //   width: 120,
    // },

    {
      title: '订单金额',
      dataIndex: 'total_price',
      key: 'total_price',
      valueType: 'money',
      width: 120,
      align: 'right',
      render: (_, record) => money(record.total_price, '$'),
      hideInSearch: true,
    },
    {
      title: '实际到账',
      dataIndex: 'settlement_amount_cny',
      key: 'settlement_amount_cny',
      valueType: 'money',
      width: 160,
      align: 'right',
      render: (_, record) => {
        if (record.settlement_amount != null && record.settlement_currency) {
          return (
            <Tooltip
              title={`${record.actual_received_source === 'refund_pending' ? '退款处理中，预计净额' : '网关实际到账'} ${Number(record.settlement_amount).toFixed(2)} ${record.settlement_currency}；按汇率 ${Number(record.settlement_exchange_rate || 0).toFixed(4)} 换算`}
            >
              <Space
                direction="vertical"
                size={0}
                style={{ alignItems: 'flex-end' }}
              >
                <span>{money(record.settlement_amount_cny)}</span>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {Number(record.settlement_amount).toFixed(2)}{' '}
                  {record.settlement_currency}
                  <Tag
                    color={record.actual_received_source === 'refund_pending' ? 'orange' : 'green'}
                    style={{ marginLeft: 6 }}
                  >
                    {record.actual_received_source === 'refund_pending'
                      ? '退款待结算'
                      : '真实'}
                  </Tag>
                </Typography.Text>
              </Space>
            </Tooltip>
          );
        }
        // 没有 Shopify Payments 结算流水时看口径：
        // PayPal 等渠道虽然没有结算流水，但净额来自网关回执，仍然是真实值。
        if (record.actual_received_source === 'gateway') {
          return (
            <Tooltip title="来自网关回执的真实净额">
              <Space
                direction="vertical"
                size={0}
                style={{ alignItems: 'flex-end' }}
              >
                <span>{money(record.actual_received, '$')}</span>
                <Tag color="green">真实</Tag>
              </Space>
            </Tooltip>
          );
        }
        return (
          <Tooltip title="网关手续费未到，暂为估算">
            <span style={{ color: '#999' }}>
              {money(record.actual_received, '$')}（估算）
            </span>
          </Tooltip>
        );
      },
      hideInSearch: true,
    },
    {
      title: '支付手续费',
      dataIndex: 'payment_fee',
      key: 'payment_fee',
      width: 130,
      align: 'right',
      hideInSearch: true,
      render: (_, record) => {
        const fee = record.payment_fee || 0;
        if (!fee) return <span style={{ color: '#bbb' }}>-</span>;
        const verified = record.fee_source === 'gateway';
        return (
          <Space size={4}>
            {money(fee, '$')}
            <Tooltip
              title={
                verified
                  ? '来自网关回执的实际扣费'
                  : '按费率表估算'
              }
            >
              <Tag
                color={verified ? 'green' : undefined}
                style={{ marginInlineEnd: 0 }}
              >
                {verified ? '真实' : '估算'}
              </Tag>
            </Tooltip>
          </Space>
        );
      },
    },
    {
      title: '毛利润(CNY)',
      dataIndex: 'gross_profit_cny',
      key: 'gross_profit_cny',
      valueType: 'money',
      width: 120,
      align: 'right',
      render: (_, record) => money(record.gross_profit_cny),
      hideInSearch: true,
    },
    {
      title: '商品成本费',
      dataIndex: 'allocated_product_cost',
      key: 'allocated_product_cost',
      width: 120,
      align: 'right',
      render: (_, record) => {
        const cost = record.allocated_product_cost || 0;
        return cost > 0 ? (
          money(cost)
        ) : (
          <span style={{ color: '#bbb' }}>-</span>
        );
      },
      hideInSearch: true,
    },
    {
      title: '物流费用',
      dataIndex: 'shipping_cost_cny',
      key: 'shipping_cost_cny',
      width: 120,
      align: 'right',
      render: (_, record) => {
        const cost = record.shipping_cost_cny ?? 0;
        return cost > 0 ? (
          <Space size={4}>
            {money(cost)}
            {record.shipping_cost_source === '4px' && <Tag color="blue">4PX</Tag>}
          </Space>
        ) : (
          <span style={{ color: '#bbb' }}>-</span>
        );
      },
      hideInSearch: true,
    },
    {
      title: '实际利润',
      key: 'net_profit',
      valueType: 'money',
      align: 'right',
      width: 120,
      render: (_, record) => {
        // 使用人民币计算实际利润
        const grossProfitCny = record.gross_profit_cny || 0;
        const allocatedProductCost = record.allocated_product_cost || 0;
        const allocatedShippingCost = record.allocated_shipping_cost || 0;
        const netProfit =
          grossProfitCny - allocatedProductCost - allocatedShippingCost;
        return (
          <span style={{ color: netProfit < 0 ? '#cf1322' : undefined }}>
            {money(netProfit)}
          </span>
        );
      },
      hideInSearch: true,
    },
    {
      title: '支付方式',
      dataIndex: 'payment_method',
      key: 'payment_method',
      width: 150,
      valueEnum: {
        paypal: { text: 'PayPal' },
        stripe: { text: 'Stripe' },
        shopify_payment: { text: 'Shopify Payments' },
        manual: { text: '手动' },
      },
      render: (_, record) =>
        ({
          paypal: 'PayPal',
          stripe: 'Stripe',
          shopify_payment: 'Shopify Payments',
          manual: '手动',
        })[record.payment_method || ''] || record.payment_method || '-',
    },

    {
      title: 'Shopify 发货状态',
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
      title: '仓库状态',
      dataIndex: 'warehouse_status',
      key: 'warehouse_status',
      width: 125,
      valueEnum: {
        pending: { text: '待扫码出库', status: 'Warning' },
        shipped: { text: '已出库', status: 'Success' },
        legacy_unverified: { text: '历史待核实', status: 'Default' },
        legacy_partial_unverified: { text: '历史部分发货待核实', status: 'Default' },
      },
    },
    {
      title: '采购备货',
      key: 'procurement_status',
      width: 130,
      hideInSearch: true,
      render: (_, record) => record.procurement_status === 'ready_to_ship'
        ? <Tag color="green">可发货</Tag>
        : record.procurement_status === 'stocked'
          ? <Tag>已备齐</Tag>
        : record.procurement_status === 'purchasing'
          ? <Tag color="blue">已发起采购</Tag>
          : record.procurement_status === 'shortage'
            ? <Tag color="orange">已采购 · 待补采</Tag>
            : record.procurement_status === 'partially_stocked'
              ? <Tag color="blue">部分备货</Tag>
            : <Tag>未采购</Tag>,
    },
    {
      title: '订单状态',
      key: 'status',
      width: 170,
      render: (_, record) =>
        record.actual_received_source === 'refund_pending' ? (
          <Space size={2}>
            {getStatusTag(record.financial_status || '', 'financial')}
            <Tag color="orange">退款处理中</Tag>
          </Space>
        ) : (
          getStatusTag(record.financial_status || '', 'financial')
        ),
      hideInSearch: true,
    },
    {
      title: '物流标签单号',
      key: 'tracking_numbers',
      width: 260,
      ellipsis: true,
      hideInSearch: true,
      render: (_, record) => {
        const label = record.shipping_label_number;
        const trackingNumbers = record.shopify_tracking_numbers || [];
        const number = label || trackingNumbers[0];
        if (!number) return <Typography.Text type="secondary">-</Typography.Text>;
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0, maxWidth: '100%' }}>
            <Typography.Text
              ellipsis={{ tooltip: trackingNumbers.length ? trackingNumbers.join('、') : number }}
              style={{ flex: '1 1 auto', minWidth: 0 }}
            >
              {number}
            </Typography.Text>
            <Typography.Text
              copyable={{ text: trackingNumbers.length ? trackingNumbers.join('\n') : number, icon: <CopyOutlined /> }}
              style={{ flex: '0 0 auto' }}
            />
            {trackingNumbers.length > 1 && <Tag style={{ flex: '0 0 auto', marginInlineEnd: 0 }}>+{trackingNumbers.length - 1}</Tag>}
          </div>
        );
      },
    },
    {
      title: '订单日期',
      dataIndex: 'order_date',
      key: 'order_date',
      valueType: 'dateRange',
      width: 160,
      search: {
        transform: (value: any) => {
          if (!value || !value[0] || !value[1]) {
            return {
              start_date: undefined,
              end_date: undefined,
            };
          }

          return {
            start_date: dayjs(value[0]).format('YYYY-MM-DD'),
            end_date: dayjs(value[1]).format('YYYY-MM-DD'),
          };
        },
      },
      // 后端按 UTC 存储，展示时统一转成北京时间
      render: (_, record) =>
        formatBeijingTime(record.order_date, 'YYYY-MM-DD HH:mm'),
      sorter: true,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      valueType: 'dateTime',
      width: 150,
      hideInTable: true,
      hideInSearch: true,
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      fixed: 'right',
      hideInSearch: true,
      render: (_, record) => (
        <Button type="link" size="small" onClick={() => history.push(`/orders/detail/${record.id}`)}>
          查看详情
        </Button>
      ),
    },
  ];

  // 请求订单数据
  const requestOrders = async (params: any) => {
    try {
      const query: OrderListParams = {
        page: params.current,
        per_page: params.pageSize,
        financial_status: params.financial_status,
        warehouse_status: params.warehouse_status,
        fulfillment_status: params.fulfillment_status,
        payment_method: params.payment_method,
        search: params.customer_name || params.order_number,
        start_date: params.start_date,
        end_date: params.end_date,
        sort_by: 'created_at',
        sort_order: 'desc',
      };
      lastQueryRef.current = query;
      const response = await getOrders(query);

      if (response.success) {
        const staleCurrencies = response.data.exchange_rate_refresh_currencies || [];
        if (staleCurrencies.length && !rateRefreshStarted.current) {
          rateRefreshStarted.current = true;
          void Promise.all(staleCurrencies.map((from_currency) =>
            getExchangeRates({ from_currency, to_currency: 'CNY' }),
          )).then((results) => {
            if (results.some((result) => result.success)) actionRef.current?.reload();
          });
        }
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

  const handleExport = async () => {
    setExporting(true);
    try {
      const rows: OrderItem[] = [];
      for (let page = 1; ; page += 1) {
        const response = await getOrders({ ...lastQueryRef.current, page, per_page: 100 });
        if (!response.success) throw new Error(response.message || '导出失败');
        rows.push(...response.data.orders);
        if (rows.length >= response.data.pagination.total || !response.data.orders.length) break;
      }
      exportCsv(`订单-${dayjs().format('YYYYMMDD-HHmm')}.csv`,
        ['店铺', '订单号', '订单日期(北京时间)', '客户', '订单金额', '订单币种', '实际到账', '结算币种', '人民币金额', '手续费', '手续费来源', '付款状态', '发货状态'],
        rows.map((order) => [order.shop_name, order.order_number, formatBeijingTime(order.order_date, 'YYYY-MM-DD HH:mm'), order.customer_name,
          order.total_price, order.currency, order.settlement_amount ?? order.actual_received, order.settlement_currency || order.currency,
          order.settlement_amount_cny, order.payment_fee, order.fee_source, order.financial_status, order.fulfillment_status]));
      message.success(`已导出 ${rows.length} 笔订单`);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '导出失败');
    } finally {
      setExporting(false);
    }
  };

  return (
    <PageContainer
      title="订单管理"
      subTitle="管理所有订单信息"
    >
      <Card size="small" className="order-quick-filters">
        <Space size={8} className="order-quick-filters__content">
          <Typography.Text
            type="secondary"
            className="order-quick-filters__label"
          >
            快捷筛选
          </Typography.Text>
          {quickFilters.map((filter) => (
            <Button
              key={filter.key}
              type={quickFilter === filter.key ? 'primary' : 'default'}
              onClick={() => applyQuickFilter(filter.key, filter.values)}
            >
              {filter.label}
            </Button>
          ))}
        </Space>
      </Card>
      <ProTable<OrderItem>
        columns={columns}
        actionRef={actionRef}
        formRef={formRef}
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
        onSubmit={() => {
          if (!quickSubmitRef.current) setQuickFilter('custom');
        }}
        onReset={() => setQuickFilter('all')}
        pagination={{
          ...persistedPagination('orders', 20, 50),
          pageSizeOptions: [10, 20, 50],
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total, range) =>
            `第 ${range[0]}-${range[1]} 条/总共 ${total} 条`,
        }}
        dateFormatter="string"
        headerTitle="订单列表"
        scroll={{ x: 2000 }}
        tableLayout="fixed"
        // 工具栏已经有「刷新」按钮，隐藏 ProTable 自带的刷新图标，避免两个刷新入口
        options={{ reload: false }}
        toolBarRender={() => [
          <Button key="purchase" icon={<ShoppingCartOutlined />} onClick={() => history.push('/purchases')}>
            生成采购清单
          </Button>,
          <Button key="refresh" onClick={() => actionRef.current?.reload()}>
            刷新
          </Button>,
          <Button key="export" type="default" loading={exporting} onClick={handleExport}>
            导出数据
          </Button>,
        ]}
      />
    </PageContainer>
  );
};

export default OrderList;
