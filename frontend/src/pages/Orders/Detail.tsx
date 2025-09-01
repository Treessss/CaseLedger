import { PageContainer, ProDescriptions } from '@ant-design/pro-components';
import { Card, Button, Tag, Divider, Row, Col, Timeline, Table, message, Spin } from 'antd';
import { ArrowLeftOutlined, EditOutlined, PrinterOutlined } from '@ant-design/icons';
import React, { useState, useEffect } from 'react';
import { history, useParams } from '@umijs/max';
import type { ColumnsType } from 'antd/es/table';

interface OrderDetailItem {
  key: string;
  productName: string;
  quantity: number;
  unitPrice: number;
  totalPrice: number;
}

interface OrderData {
  id: number;
  order_number: string;
  customer_name?: string;
  customer_phone?: string;
  customer_email?: string;
  shipping_address?: any;
  total_price: number;
  actual_received?: number;
  product_cost?: number;
  shipping_cost?: number;
  financial_status: string;
  fulfillment_status: string;
  created_at: string;
  updated_at: string;
  note?: string;
  line_items?: any[];
  payments?: any[];
}

const OrderDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [orderData, setOrderData] = useState<OrderData | null>(null);
  const [loading, setLoading] = useState(true);

  // 获取订单详情
  const fetchOrderDetail = async () => {
    if (!id) {
      message.error('订单ID不存在');
      return;
    }

    try {
      setLoading(true);
      const response = await fetch(`/api/orders/${id}`);
      const data = await response.json();
      
      if (data.success) {
        setOrderData(data.data);
      } else {
        message.error(data.message || '获取订单详情失败');
      }
    } catch (error) {
      console.error('获取订单详情失败:', error);
      message.error('获取订单详情失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOrderDetail();
  }, [id]);

  // 订单商品明细
  const orderItems: OrderDetailItem[] = orderData?.line_items?.map((item: any, index: number) => ({
    key: index.toString(),
    productName: item.title || item.name || '未知商品',
    quantity: item.quantity || 0,
    unitPrice: parseFloat(item.price || '0'),
    totalPrice: parseFloat(item.price || '0') * (item.quantity || 0),
  })) || [];

  // 订单状态历史
  const statusHistory = orderData?.payments?.map((payment: any, index: number) => ({
    time: payment.created_at || payment.payment_date,
    status: `支付${payment.status === 'completed' ? '成功' : '处理中'}`,
    description: `${payment.payment_method || '未知方式'} - ${payment.amount} ${payment.currency || 'USD'}`,
    color: payment.status === 'completed' ? 'green' : 'blue',
  })) || [
    {
      time: orderData?.created_at || '',
      status: '订单创建',
      description: '订单已创建',
      color: 'orange',
    },
  ];

  const itemColumns: ColumnsType<OrderDetailItem> = [
    {
      title: '商品名称',
      dataIndex: 'productName',
      key: 'productName',
    },
    {
      title: '数量',
      dataIndex: 'quantity',
      key: 'quantity',
      align: 'center',
    },
    {
      title: '单价 (¥)',
      dataIndex: 'unitPrice',
      key: 'unitPrice',
      render: (value: number) => value.toFixed(2),
      align: 'right',
    },
    {
      title: '小计 (¥)',
      dataIndex: 'totalPrice',
      key: 'totalPrice',
      render: (value: number) => value.toFixed(2),
      align: 'right',
    },
  ];

  const getStatusTag = (status: string, type: 'financial' | 'fulfillment' = 'financial') => {
    if (type === 'financial') {
      const statusMap = {
        pending: { color: 'orange', text: '待付款' },
        paid: { color: 'green', text: '已付款' },
        partially_paid: { color: 'blue', text: '部分付款' },
        refunded: { color: 'red', text: '已退款' },
        voided: { color: 'gray', text: '已作废' },
        completed: { color: 'green', text: '已完成' },
        processing: { color: 'blue', text: '处理中' },
        cancelled: { color: 'red', text: '已取消' },
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

  const handleBack = () => {
    history.back();
  };

  const handleEdit = () => {
    console.log('编辑订单:', orderData?.id);
    // 这里添加编辑逻辑
  };

  const handlePrint = () => {
    console.log('打印订单:', orderData?.id);
    window.print();
  };

  if (loading) {
    return (
      <PageContainer title="订单详情" subTitle="查看订单的详细信息">
        <div style={{ textAlign: 'center', padding: '50px' }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!orderData) {
    return (
      <PageContainer title="订单详情" subTitle="查看订单的详细信息">
        <div style={{ textAlign: 'center', padding: '50px' }}>
          订单不存在
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title={`订单详情 - ${orderData.order_number}`}
      subTitle="查看订单的详细信息"
      extra={[
        <Button key="back" icon={<ArrowLeftOutlined />} onClick={handleBack}>
          返回
        </Button>,
        <Button key="edit" type="primary" icon={<EditOutlined />} onClick={handleEdit}>
          编辑
        </Button>,
        <Button key="print" icon={<PrinterOutlined />} onClick={handlePrint}>
          打印
        </Button>,
      ]}
    >
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card title="基本信息">
            <ProDescriptions
              column={2}
              dataSource={orderData as Record<string, any>}
              columns={[
                {
                  title: '订单号',
                  dataIndex: 'order_number',
                  copyable: true,
                },
                {
                  title: '财务状态',
                  dataIndex: 'financial_status',
                  render: () => getStatusTag(orderData?.financial_status || '', 'financial'),
                },
                {
                  title: '发货状态',
                  dataIndex: 'fulfillment_status',
                  render: () => getStatusTag(orderData?.fulfillment_status || '', 'fulfillment'),
                },
                {
                  title: '客户姓名',
                  dataIndex: 'customer_name',
                },
                {
                  title: '联系电话',
                  dataIndex: 'customer_phone',
                },
                {
                  title: '邮箱地址',
                  dataIndex: 'customer_email',
                },
                {
                  title: '收货地址',
                  dataIndex: 'shipping_address',
                  render: (address: any) => address ? `${address.address1 || ''} ${address.city || ''} ${address.province || ''}` : '',
                  span: 2,
                },
                {
                  title: '订单金额',
                  dataIndex: 'total_price',
                  render: (_, record) => `$${record.total_price?.toFixed(2) || '0.00'}`,
                },
                {
                  title: '实际到账',
                  dataIndex: 'actual_received',
                  render: (_, record) => `$${record.actual_received?.toFixed(2) || '0.00'}`,
                },
                {
                  title: '成本费用',
                  dataIndex: 'product_cost',
                  render: (_, record) => {
                    const cost = record.product_cost || 0;
                    return cost > 0 ? `$${cost.toFixed(2)}` : '未添加';
                  },
                },
                {
                  title: '物流费用',
                  dataIndex: 'shipping_cost',
                  render: (_, record) => {
                    const cost = record.shipping_cost || 0;
                    return cost > 0 ? `$${cost.toFixed(2)}` : '未添加';
                  },
                },
                {
                  title: '实际利润',
                  key: 'net_profit',
                  render: (_, record) => {
                    const actualReceived = record.actual_received || 0;
                    const productCost = record.product_cost || 0;
                    const shippingCost = record.shipping_cost || 0;
                    const netProfit = actualReceived - productCost - shippingCost;
                    return `$${netProfit.toFixed(2)}`;
                  },
                },
                {
                  title: '创建时间',
                  dataIndex: 'created_at',
                },
                {
                  title: '更新时间',
                  dataIndex: 'updated_at',
                },
                {
                  title: '备注',
                  dataIndex: 'note',
                  span: 2,
                },
              ]}
            />
          </Card>

          {/* 商品明细 */}
          <Card title="商品明细">
            <Table
              columns={itemColumns}
              dataSource={orderItems}
              pagination={false}
              summary={() => (
                <Table.Summary>
                  <Table.Summary.Row>
                    <Table.Summary.Cell index={0} colSpan={3}>
                      <strong>总计</strong>
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={1} align="right">
                      <strong>¥{orderData?.total_price?.toFixed(2) || '0.00'}</strong>
                    </Table.Summary.Cell>
                  </Table.Summary.Row>
                </Table.Summary>
              )}
            />
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          {/* 订单状态历史 */}
          <Card title="状态历史">
            <Timeline
              items={statusHistory.map((item) => ({
                color: item.color,
                children: (
                  <div>
                    <div style={{ fontWeight: 'bold', marginBottom: 4 }}>
                      {item.status}
                    </div>
                    <div style={{ color: '#666', fontSize: 12, marginBottom: 4 }}>
                      {item.time}
                    </div>
                    <div style={{ fontSize: 14 }}>
                      {item.description}
                    </div>
                  </div>
                ),
              }))}
            />
          </Card>
        </Col>
      </Row>
    </PageContainer>
  );
};

export default OrderDetail;