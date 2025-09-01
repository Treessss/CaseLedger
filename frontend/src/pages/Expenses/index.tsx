import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Button, Tag, Space, Modal, Form, Input, Select, DatePicker, InputNumber, message } from 'antd';
import { PlusOutlined, EyeOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import React, { useState, useRef, useEffect } from 'react';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { getExpenses, createExpense, updateExpense, deleteExpense, batchDeleteExpenses, getExpenseCategories, type ExpenseItem } from '@/services/expenses';
import { getOrders, type OrderItem } from '@/services/orders';
import { getAccounts, type AccountItem } from '@/services/accounts/api';
import { convertCurrency } from '@/services/exchange/api';
import { SUPPORTED_CURRENCIES } from '@/services/exchange/api';
import dayjs from 'dayjs';



const Expenses: React.FC = () => {
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [addModalVisible, setAddModalVisible] = useState(false);
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [viewModalVisible, setViewModalVisible] = useState(false);
  const [currentRecord, setCurrentRecord] = useState<ExpenseItem | null>(null);
  const [categories, setCategories] = useState<Record<string, string>>({
    'facebook_ads': 'Facebook广告费',
    'product_cost': '商品成本费', 
    'shipping_cost': '物流费用',
    'other': '其他费用'
  });
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [selectedOrders, setSelectedOrders] = useState<number[]>([]);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const actionRef = useRef<ActionType>(null);
  
  // 汇率相关状态
  const [selectedCurrency, setSelectedCurrency] = useState('CNY');
  const [originalAmount, setOriginalAmount] = useState<number>(0);
  const [convertedAmount, setConvertedAmount] = useState<number>(0);
  const [exchangeRate, setExchangeRate] = useState<number>(1);
  const [convertLoading, setConvertLoading] = useState(false);

  useEffect(() => {
    fetchCategories();
    fetchOrders();
    fetchAccounts();
  }, []);

  // 获取订单列表
  const fetchOrders = async () => {
    try {
      const response = await getOrders({ per_page: 1000 });
      if (response.success) {
        setOrders(response.data.orders);
      }
    } catch (error) {
      console.error('获取订单列表失败:', error);
    }
  };

  // 获取账户列表
  const fetchAccounts = async () => {
    try {
      const response = await getAccounts({ pageSize: 1000 });
      if (response.success && response.data && Array.isArray(response.data)) {
        setAccounts(response.data);
      } else {
        setAccounts([]);
      }
    } catch (error) {
      console.error('获取账户列表失败:', error);
      setAccounts([]);
    }
  };

  // 获取费用分类
  const fetchCategories = async () => {
    try {
      const response = await getExpenseCategories();
      if (response.success && Array.isArray(response.data)) {
        const categoryMap: Record<string, string> = {};
        response.data.forEach((item: { key: string; label: string }) => {
          categoryMap[item.key] = item.label;
        });
        setCategories(categoryMap);
      }
    } catch (error) {
      console.error('获取费用分类失败:', error);
    }
  };

  // 汇率转换函数
  const handleCurrencyConvert = async (amount: number, fromCurrency: string) => {
    if (fromCurrency === 'CNY' || amount === 0) {
      setConvertedAmount(amount);
      setExchangeRate(1);
      return;
    }

    try {
      setConvertLoading(true);
      const result = await convertCurrency({
        amount,
        from_currency: fromCurrency,
        to_currency: 'CNY'
      });

      if (result.success && result.data) {
        setConvertedAmount(result.data.converted_amount);
        setExchangeRate(result.data.exchange_rate);
      } else {
        message.error(result.message || '汇率转换失败');
        setConvertedAmount(amount);
        setExchangeRate(1);
      }
    } catch (error) {
      console.error('汇率转换失败:', error);
      message.error('汇率转换失败');
      setConvertedAmount(amount);
      setExchangeRate(1);
    } finally {
      setConvertLoading(false);
    }
  };



  const columns: ProColumns<ExpenseItem>[] = [
    {
      title: '费用类别',
      dataIndex: 'category',
      key: 'category',
      width: 120,
      valueEnum: Object.fromEntries(
        Object.entries(categories).map(([key, label]) => [key, { text: label }])
      ),
      filters: true,
      onFilter: true,
    },
    {
      title: '费用描述',
      dataIndex: 'description',
      key: 'description',
      width: 200,
      ellipsis: true,
    },
    {
      title: '金额',
      dataIndex: 'amount',
      key: 'amount',
      width: 120,
      valueType: 'money',
      render: (_, record) => `¥${record.amount.toFixed(2)}`,
      sorter: (a, b) => a.amount - b.amount,
    },
    {
      title: '日期',
      dataIndex: 'date',
      key: 'date',
      width: 120,
      valueType: 'dateRange',
      search: {
        transform: (value) => {
          return {
            start_date: value[0],
            end_date: value[1],
          };
        },
      },
      render: (_, record) => record.date,
      sorter: (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime(),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (_, record) => {
        const statusMap: Record<string, { color: string; text: string }> = {
          confirmed: { color: 'green', text: '已确认' },
          completed: { color: 'green', text: '已确认' },
          pending: { color: 'orange', text: '待确认' },
          cancelled: { color: 'red', text: '已取消' },
        };
        const status = statusMap[record.status] || { color: 'default', text: record.status };
        return <Tag color={status.color}>{status.text}</Tag>;
      },
      filters: [
        { text: '已确认', value: 'confirmed' },
        { text: '待确认', value: 'pending' },
        { text: '已取消', value: 'cancelled' },
      ],
      onFilter: (value, record) => record.status === value,
    },
    {
      title: '提交人',
      dataIndex: 'submitter',
      key: 'submitter',
      width: 100,
    },
    {
      title: '关联订单',
      dataIndex: 'orders_info',
      key: 'orders_info',
      width: 200,
      render: (_, record) => {
        // 优先显示多个订单信息，如果没有则显示单个订单信息
        const ordersInfo = record.orders_info || (record.order_info ? [record.order_info] : []);
        
        if (ordersInfo && ordersInfo.length > 0) {
          return (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
              {ordersInfo.map((order: any, index: number) => (
                <Tag 
                  key={index} 
                  color="blue" 
                  title={`客户: ${order.customer_name}, 总价: ¥${order.total_price}`}
                  style={{ marginBottom: '2px' }}
                >
                  {order.order_number}
                </Tag>
              ))}
            </div>
          );
        }
        return <span style={{ color: '#999' }}>无关联订单</span>;
      },
    },
    {
      title: '扣费账户',
      dataIndex: 'account_name',
      key: 'account_name',
      width: 120,
      render: (_, record) => {
        return record.account_name ? (
          <Tag color="green">{record.account_name}</Tag>
        ) : (
          <span style={{ color: '#999' }}>未指定账户</span>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      fixed: 'right',
      render: (_, record) => (
        <Space size="small">
          <Button 
            type="link" 
            size="small"
            icon={<EyeOutlined />}
            title="查看"
            onClick={() => {
              setCurrentRecord(record);
              setViewModalVisible(true);
            }}
          />
          <Button 
            type="link" 
            size="small"
            icon={<EditOutlined />}
            title="编辑"
            onClick={() => {
              setCurrentRecord(record);
              editForm.setFieldsValue({
                ...record,
                date: record.date ? new Date(record.date) : null,
              });
              setEditModalVisible(true);
            }}
          />
          <Button 
            type="link" 
            size="small" 
            danger
            icon={<DeleteOutlined />}
            title="删除"
            onClick={() => handleDelete(record)}
          />
        </Space>
      ),
    },
  ];

  const handleAdd = async (values: any) => {
    try {
      const expenseData = {
        ...values,
        amount: convertedAmount, // 使用转换后的人民币金额
        original_amount: originalAmount, // 保存原始金额
        original_currency: selectedCurrency, // 保存原始币种
        exchange_rate: exchangeRate, // 保存汇率
        date: values.date.format('YYYY-MM-DD'),
        order_ids: values.order_ids || [], // 关联的订单ID列表
        account_id: values.account_id || null, // 扣费账户ID
      };
      
      const response = await createExpense(expenseData);
      if (response.success) {
        message.success('费用添加成功');
        setAddModalVisible(false);
        form.resetFields();
        // 重置汇率相关状态
        setSelectedCurrency('CNY');
        setOriginalAmount(0);
        setConvertedAmount(0);
        setExchangeRate(1);
        actionRef.current?.reload();
      } else {
        message.error(response.message || '添加失败');
      }
    } catch (error) {
      message.error('添加失败');
      console.error('Failed to add expense:', error);
    }
  };

  const handleEdit = async (values: any) => {
    try {
      if (!currentRecord?.id) return;
      const response = await updateExpense(currentRecord.id, {
        ...values,
        date: values.date.format('YYYY-MM-DD'),
      });
      if (response.success) {
        message.success('费用更新成功');
        setEditModalVisible(false);
        editForm.resetFields();
        actionRef.current?.reload();
      } else {
        message.error(response.message || '更新失败');
      }
    } catch (error) {
      message.error('更新失败');
      console.error('Failed to update expense:', error);
    }
  };

  const handleDelete = (record: ExpenseItem) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这条费用记录吗？',
      onOk: async () => {
        try {
          const response = await deleteExpense(record.id);
          if (response.success) {
            message.success('删除成功');
            actionRef.current?.reload();
          } else {
            message.error(response.message || '删除失败');
          }
        } catch (error) {
          message.error('删除失败');
          console.error('Failed to delete expense:', error);
        }
      },
    });
  };



  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) {
      Modal.warning({
        title: '提示',
        content: '请先选择要删除的费用记录',
      });
      return;
    }
    
    Modal.confirm({
      title: '批量删除',
      content: `确定要删除选中的 ${selectedRowKeys.length} 条费用记录吗？此操作不可恢复。`,
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          const response = await batchDeleteExpenses(selectedRowKeys as number[]);
          if (response.success) {
            message.success(response.message || '批量删除成功');
            setSelectedRowKeys([]);
            actionRef.current?.reload();
          } else {
            message.error(response.message || '批量删除失败');
          }
        } catch (error) {
          message.error('批量删除失败');
          console.error('Failed to batch delete expenses:', error);
        }
      },
    });
  };

  const rowSelection = {
    selectedRowKeys,
    onChange: (newSelectedRowKeys: React.Key[]) => {
      setSelectedRowKeys(newSelectedRowKeys);
    },
  };

  return (
    <PageContainer
      title="费用管理"
      subTitle="管理和审批费用报销"
      extra={[
        <Button 
          key="add" 
          type="primary" 
          icon={<PlusOutlined />}
          onClick={() => setAddModalVisible(true)}
        >
          新增费用
        </Button>,
      ]}
    >
      <ProTable<ExpenseItem>
        columns={columns}
        actionRef={actionRef}
        request={async (params, sort, filter) => {
          try {
            const response = await getExpenses({
              page: params.current,
              per_page: params.pageSize,
              category: params.category,
              status: params.status,
              search: params.keyword,
              start_date: params.start_date || params.date?.[0],
              end_date: params.end_date || params.date?.[1],
              sort_by: Object.keys(sort || {})[0],
              sort_order: Object.values(sort || {})[0] === 'ascend' ? 'asc' : 'desc',
            });
            
            if (response.success) {
              return {
                data: response.data.expenses,
                success: true,
                total: response.data.pagination.total,
              };
            } else {
              message.error(response.message || '获取数据失败');
              return {
                data: [],
                success: false,
                total: 0,
              };
            }
          } catch (error) {
            message.error('获取数据失败');
            console.error('Failed to fetch expenses:', error);
            return {
              data: [],
              success: false,
              total: 0,
            };
          }
        }}
        rowKey="id"
        rowSelection={rowSelection}
        search={{
          labelWidth: 'auto',
          defaultCollapsed: false,
        }}
        params={{}}
        pagination={{
          pageSize: 10,
          showSizeChanger: true,
        }}
        dateFormatter="string"
        headerTitle="费用列表"
        scroll={{ x: 1050 }}
        toolBarRender={() => [
          <Button 
            key="delete" 
            type="default"
            danger
            onClick={handleBatchDelete}
            disabled={selectedRowKeys.length === 0}
          >
            批量删除
          </Button>,
          <Button key="export" type="default">
            导出数据
          </Button>,
        ]}
      />

      {/* 添加费用模态框 */}
      <Modal
        title="添加费用"
        open={addModalVisible}
        onCancel={() => {
          setAddModalVisible(false);
          form.resetFields();
          // 重置汇率相关状态
          setSelectedCurrency('CNY');
          setOriginalAmount(0);
          setConvertedAmount(0);
          setExchangeRate(1);
        }}
        footer={null}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleAdd}
        >
          <Form.Item
            name="category"
            label="费用分类"
            rules={[{ required: true, message: '请选择费用分类' }]}
          >
            <Select placeholder="请选择费用分类">
              {Object.entries(categories).map(([key, label]) => (
                <Select.Option key={key} value={key}>
                  {label}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="description"
            label="费用描述"
            rules={[{ required: true, message: '请输入费用描述' }]}
          >
            <Input.TextArea rows={3} placeholder="请输入费用描述" />
          </Form.Item>
          <Form.Item
            label="费用金额"
            required
          >
            <Input.Group compact>
              <Select
                value={selectedCurrency}
                onChange={(value) => {
                  setSelectedCurrency(value);
                  if (originalAmount > 0) {
                    handleCurrencyConvert(originalAmount, value);
                  }
                }}
                style={{ width: '30%' }}
              >
                {Object.keys(SUPPORTED_CURRENCIES).map((currency) => (
                  <Select.Option key={currency} value={currency}>
                    {currency}
                  </Select.Option>
                ))}
              </Select>
              <InputNumber
                value={originalAmount}
                onChange={(value) => {
                  const amount = value || 0;
                  setOriginalAmount(amount);
                  handleCurrencyConvert(amount, selectedCurrency);
                }}
                style={{ width: '70%' }}
                placeholder="请输入费用金额"
                min={0}
                precision={2}
              />
            </Input.Group>
            {selectedCurrency !== 'CNY' && (
              <div style={{ marginTop: 8, fontSize: '12px', color: '#666' }}>
                {convertLoading ? '转换中...' : (
                  <>
                    汇率: 1 {selectedCurrency} = {exchangeRate.toFixed(4)} CNY
                    <br />
                    转换后金额: ¥{convertedAmount.toFixed(2)}
                  </>
                )}
              </div>
            )}
          </Form.Item>
          <Form.Item
            name="date"
            label="费用日期"
            rules={[{ required: true, message: '请选择费用日期' }]}
          >
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="submitter"
            label="提交人"
            rules={[{ required: true, message: '请输入提交人' }]}
          >
            <Input placeholder="请输入提交人" />
          </Form.Item>
          <Form.Item
            name="order_ids"
            label="关联订单"
            tooltip="选择要分摊此费用的订单，费用将按订单金额比例分摊"
          >
            <Select
              mode="multiple"
              placeholder="请选择关联订单（可选）"
              showSearch
              filterOption={(input, option) =>
                (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
              }
              options={orders.map(order => ({
                value: order.id,
                label: `${order.order_number} - ${order.customer_name || order.customer_email} - ¥${order.total_price}`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="account_id"
            label="扣费账户"
            tooltip="选择要从哪个账户扣除此费用"
          >
            <Select
              placeholder="请选择扣费账户（可选）"
              showSearch
              filterOption={(input, option) =>
                (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
              }
              options={(accounts || []).filter(account => account.status === 'active').map(account => ({
                value: account.id,
                label: `${account.account_name} (${account.platform}) - 余额: ¥${account.balance}`,
              }))}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                确定
              </Button>
              <Button onClick={() => {
                setAddModalVisible(false);
                form.resetFields();
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑费用模态框 */}
      <Modal
        title="编辑费用"
        open={editModalVisible}
        onCancel={() => {
          setEditModalVisible(false);
          editForm.resetFields();
        }}
        footer={null}
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={handleEdit}
        >
          <Form.Item
            name="category"
            label="费用分类"
            rules={[{ required: true, message: '请选择费用分类' }]}
          >
            <Select placeholder="请选择费用分类">
              {Object.entries(categories).map(([key, label]) => (
                <Select.Option key={key} value={key}>
                  {label}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="description"
            label="费用描述"
            rules={[{ required: true, message: '请输入费用描述' }]}
          >
            <Input.TextArea rows={3} placeholder="请输入费用描述" />
          </Form.Item>
          <Form.Item
            name="amount"
            label="费用金额"
            rules={[{ required: true, message: '请输入费用金额' }]}
          >
            <InputNumber
              style={{ width: '100%' }}
              placeholder="请输入费用金额"
              min={0}
              precision={2}
              addonAfter="元"
            />
          </Form.Item>
          <Form.Item
            name="date"
            label="费用日期"
            rules={[{ required: true, message: '请选择费用日期' }]}
          >
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="submitter"
            label="提交人"
            rules={[{ required: true, message: '请输入提交人' }]}
          >
            <Input placeholder="请输入提交人" />
          </Form.Item>
          <Form.Item
            name="status"
            label="状态"
            rules={[{ required: true, message: '请选择状态' }]}
          >
            <Select placeholder="请选择状态">
              <Select.Option value="pending">待审核</Select.Option>
              <Select.Option value="approved">已批准</Select.Option>
              <Select.Option value="rejected">已拒绝</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                确定
              </Button>
              <Button onClick={() => {
                setEditModalVisible(false);
                editForm.resetFields();
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 查看费用模态框 */}
      <Modal
        title="费用详情"
        open={viewModalVisible}
        onCancel={() => setViewModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setViewModalVisible(false)}>
            关闭
          </Button>
        ]}
      >
        {currentRecord && (
          <div>
            <p><strong>费用分类：</strong>{categories[currentRecord.category] || currentRecord.category}</p>
            <p><strong>费用描述：</strong>{currentRecord.description}</p>
            <p><strong>费用金额：</strong>¥{currentRecord.amount.toFixed(2)}</p>
            <p><strong>费用日期：</strong>{currentRecord.date}</p>
            <p><strong>提交人：</strong>{currentRecord.submitter}</p>
            <p><strong>状态：</strong>
              <Tag color={
                currentRecord.status === 'approved' ? 'green' :
                currentRecord.status === 'rejected' ? 'red' : 'orange'
              }>
                {currentRecord.status === 'approved' ? '已批准' :
                 currentRecord.status === 'rejected' ? '已拒绝' : '待审核'}
              </Tag>
            </p>
            {currentRecord.created_at && (
              <p><strong>创建时间：</strong>{currentRecord.created_at}</p>
            )}
            {currentRecord.updated_at && (
              <p><strong>更新时间：</strong>{currentRecord.updated_at}</p>
            )}
          </div>
        )}
      </Modal>
    </PageContainer>
  );
};

export default Expenses;