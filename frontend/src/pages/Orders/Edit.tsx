import { PageContainer, ProForm, ProFormText, ProFormDigit, ProFormSelect, ProFormTextArea } from '@ant-design/pro-components';
import { Card, Button, message, Spin } from 'antd';
import { ArrowLeftOutlined, SaveOutlined } from '@ant-design/icons';
import React, { useState, useEffect } from 'react';
import { history, useParams } from '@umijs/max';
import { getOrderDetail, updateOrder } from '@/services/orders';

interface OrderData {
  id: number;
  order_number: string;
  customer_name?: string;
  customer_phone?: string;
  customer_email?: string;
  total_price: number;
  actual_received?: number;
  product_cost?: number;
  shipping_cost?: number;
  financial_status?: string;
  fulfillment_status?: string;
  note?: string;
}

const OrderEdit: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const [orderData, setOrderData] = useState<OrderData | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  // 获取订单详情
  const fetchOrderDetail = async () => {
    if (!id) return;
    
    try {
      setLoading(true);
      const response = await getOrderDetail(parseInt(id));
      if (response.success) {
        setOrderData(response.data);
      } else {
        message.error('获取订单详情失败');
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

  const handleBack = () => {
    history.back();
  };

  const handleSubmit = async (values: any) => {
    if (!orderData) return;
    
    try {
      setSubmitting(true);
      const response = await updateOrder(orderData.id, values);
      if (response.success) {
        message.success('订单更新成功');
        history.back();
      } else {
        message.error('订单更新失败');
      }
    } catch (error) {
      console.error('订单更新失败:', error);
      message.error('订单更新失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <PageContainer>
        <div style={{ textAlign: 'center', padding: '50px' }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!orderData) {
    return (
      <PageContainer>
        <div style={{ textAlign: 'center', padding: '50px' }}>
          <p>订单不存在</p>
          <Button onClick={handleBack}>返回</Button>
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title={`编辑订单 - ${orderData.order_number}`}
      extra={[
        <Button key="back" icon={<ArrowLeftOutlined />} onClick={handleBack}>
          返回
        </Button>,
      ]}
    >
      <Card>
        <ProForm
          initialValues={{
            customer_name: orderData.customer_name,
            customer_phone: orderData.customer_phone,
            customer_email: orderData.customer_email,
            total_price: orderData.total_price,
            actual_received: orderData.actual_received,
            product_cost: orderData.product_cost || 0,
            shipping_cost: orderData.shipping_cost || 0,
            financial_status: orderData.financial_status,
            fulfillment_status: orderData.fulfillment_status,
            note: orderData.note,
          }}
          onFinish={handleSubmit}
          submitter={{
            searchConfig: {
              submitText: '保存',
            },
            render: (props, doms) => {
              return [
                <Button
                  key="submit"
                  type="primary"
                  icon={<SaveOutlined />}
                  loading={submitting}
                  onClick={() => props.form?.submit?.()}
                >
                  保存
                </Button>,
              ];
            },
          }}
        >
          <ProForm.Group>
            <ProFormText
              name="customer_name"
              label="客户姓名"
              width="md"
              placeholder="请输入客户姓名"
            />
            <ProFormText
              name="customer_phone"
              label="联系电话"
              width="md"
              placeholder="请输入联系电话"
            />
          </ProForm.Group>
          
          <ProForm.Group>
            <ProFormText
              name="customer_email"
              label="邮箱地址"
              width="md"
              placeholder="请输入邮箱地址"
            />
            <ProFormDigit
              name="total_price"
              label="订单金额"
              width="md"
              min={0}
              precision={2}
              fieldProps={{
                addonBefore: '$',
              }}
            />
          </ProForm.Group>
          
          <ProForm.Group>
            <ProFormDigit
              name="actual_received"
              label="实际到账"
              width="md"
              min={0}
              precision={2}
              fieldProps={{
                addonBefore: '$',
              }}
            />
            <ProFormDigit
              name="product_cost"
              label="成本费用"
              width="md"
              min={0}
              precision={2}
              fieldProps={{
                addonBefore: '$',
              }}
              tooltip="商品采购成本"
            />
          </ProForm.Group>
          
          <ProForm.Group>
            <ProFormDigit
              name="shipping_cost"
              label="物流费用"
              width="md"
              min={0}
              precision={2}
              fieldProps={{
                addonBefore: '$',
              }}
              tooltip="物流运输费用"
            />
            <ProFormSelect
              name="financial_status"
              label="财务状态"
              width="md"
              options={[
                { label: '已付款', value: 'paid' },
                { label: '待付款', value: 'pending' },
                { label: '已退款', value: 'refunded' },
                { label: '已取消', value: 'cancelled' },
              ]}
            />
          </ProForm.Group>
          
          <ProForm.Group>
            <ProFormSelect
              name="fulfillment_status"
              label="发货状态"
              width="md"
              options={[
                { label: '已发货', value: 'fulfilled' },
                { label: '未发货', value: 'unfulfilled' },
                { label: '部分发货', value: 'partial' },
              ]}
            />
          </ProForm.Group>
          
          <ProFormTextArea
            name="note"
            label="备注"
            placeholder="请输入备注信息"
            fieldProps={{
              rows: 4,
            }}
          />
        </ProForm>
      </Card>
    </PageContainer>
  );
};

export default OrderEdit;