import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Button, Tag, Space, Modal, Form, Input, Select, InputNumber, message, Card, Row, Col, Statistic, DatePicker, Tooltip } from 'antd';
import { PlusOutlined, ExclamationCircleOutlined, ReloadOutlined, HistoryOutlined, DollarOutlined, SyncOutlined } from '@ant-design/icons';
import React, { useState, useRef, useEffect, useCallback } from 'react';
import type { ProColumns, ActionType } from '@ant-design/pro-components';
import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);
dayjs.extend(timezone);
import { getCurrencyOptions, convertCurrency, formatCurrency, SUPPORTED_CURRENCIES } from '@/services/exchange';
import { formatBeijingTime, formatShortBeijingTime, convertDateRangeToUTC, getCurrentMonthUTCRange } from '@/utils/timezone';
import { request } from '@umijs/max';

const { RangePicker } = DatePicker;
const { Option } = Select;
const { TextArea } = Input;

interface AccountItem {
  id: string;
  account_name: string;
  platform: string;
  account_id?: string;
  description?: string;
  balance: number;
  currency: string;
  original_amount?: number;  // 原始金额
  original_currency?: string;  // 原始货币
  exchange_rate?: number;  // 汇率
  status: 'active' | 'inactive' | 'suspended';
  created_at: string;
  updated_at: string;
}

interface RechargeRecord {
  id: string;
  amount: number;
  recharge_method: string;
  transaction_id?: string;
  description?: string;
  status: 'pending' | 'completed' | 'confirmed' | 'failed' | 'cancelled';
  recharge_date: string;
}

interface ConsumptionRecord {
  id: string;
  amount: number;
  consumption_type: string;
  related_id?: string;
  description?: string;
  consumption_date: string;
}

interface AccountStats {
  total_balance: number;
  total_accounts: number;
  month_recharge: number;
  month_consumption: number;
}

const Accounts: React.FC = () => {
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [addModalVisible, setAddModalVisible] = useState(false);
  const [rechargeModalVisible, setRechargeModalVisible] = useState(false);
  const [historyModalVisible, setHistoryModalVisible] = useState(false);
  const [currentAccount, setCurrentAccount] = useState<AccountItem | null>(null);
  const [accountStats, setAccountStats] = useState<AccountStats>({
    total_balance: 0,
    total_accounts: 0,
    month_recharge: 0,
    month_consumption: 0
  });
  const [rechargeRecords, setRechargeRecords] = useState<RechargeRecord[]>([]);
  const [consumptionRecords, setConsumptionRecords] = useState<ConsumptionRecord[]>([]);
  const [dateRange, setDateRange] = useState<any>(() => {
    // 默认设置为当前月份的北京时间范围
    const now = dayjs().tz('Asia/Shanghai');
    const startOfMonth = now.startOf('month');
    const endOfMonth = now.endOf('month');
    return [startOfMonth, endOfMonth];
  });
  const [loading, setLoading] = useState(false);
  
  // 汇率相关状态
  const [selectedCurrency, setSelectedCurrency] = useState('CNY');
  const [originalAmount, setOriginalAmount] = useState<number>(0);
  const [convertedAmount, setConvertedAmount] = useState<number>(0);
  const [exchangeRate, setExchangeRate] = useState<number>(1);
  const [convertLoading, setConvertLoading] = useState(false);
  
  // 充值相关汇率状态
  const [rechargeCurrency, setRechargeCurrency] = useState('CNY');
  const [rechargeOriginalAmount, setRechargeOriginalAmount] = useState<number>(0);
  const [rechargeConvertedAmount, setRechargeConvertedAmount] = useState<number>(0);
  const [rechargeExchangeRate, setRechargeExchangeRate] = useState<number>(1);
  const [rechargeConvertLoading, setRechargeConvertLoading] = useState(false);
  
  // 防抖定时器
  const [debounceTimer, setDebounceTimer] = useState<NodeJS.Timeout | null>(null);
  const [rechargeDebounceTimer, setRechargeDebounceTimer] = useState<NodeJS.Timeout | null>(null);
  
  const actionRef = useRef<ActionType>(null);
  const [addForm] = Form.useForm();
  const [rechargeForm] = Form.useForm();

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

  // 防抖版本的汇率转换函数
  const debouncedCurrencyConvert = useCallback((amount: number, fromCurrency: string) => {
    // 清除之前的定时器
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    
    // 设置新的定时器，500ms后执行汇率转换
    const timer = setTimeout(() => {
      handleCurrencyConvert(amount, fromCurrency);
    }, 500);
    
    setDebounceTimer(timer);
  }, [debounceTimer]);

  // 充值汇率转换函数
  const handleRechargeCurrencyConvert = async (amount: number, fromCurrency: string) => {
    if (fromCurrency === 'CNY' || amount === 0) {
      setRechargeConvertedAmount(amount);
      setRechargeExchangeRate(1);
      return;
    }

    try {
      setRechargeConvertLoading(true);
      const result = await convertCurrency({
        amount,
        from_currency: fromCurrency,
        to_currency: 'CNY'
      });

      if (result.success && result.data) {
        setRechargeConvertedAmount(result.data.converted_amount);
        setRechargeExchangeRate(result.data.exchange_rate);
      } else {
        message.error(result.message || '汇率转换失败');
        setRechargeConvertedAmount(amount);
        setRechargeExchangeRate(1);
      }
    } catch (error) {
      console.error('汇率转换失败:', error);
      message.error('汇率转换失败');
      setRechargeConvertedAmount(amount);
      setRechargeExchangeRate(1);
    } finally {
      setRechargeConvertLoading(false);
    }
  };

  // 防抖版本的充值汇率转换函数
  const debouncedRechargeCurrencyConvert = useCallback((amount: number, fromCurrency: string) => {
    // 清除之前的定时器
    if (rechargeDebounceTimer) {
      clearTimeout(rechargeDebounceTimer);
    }
    
    // 设置新的定时器，500ms后执行汇率转换
    const timer = setTimeout(() => {
      handleRechargeCurrencyConvert(amount, fromCurrency);
    }, 500);
    
    setRechargeDebounceTimer(timer);
  }, [rechargeDebounceTimer]);

  // 加载账户统计数据
  const loadAccountStats = async () => {
    try {
      const params: any = {};
      
      // 如果有日期范围，添加到参数中
      if (dateRange && dateRange[0] && dateRange[1]) {
        // 将北京时间的日期范围转换为UTC时间
        const utcRange = convertDateRangeToUTC(
          dateRange[0].format('YYYY-MM-DD'),
          dateRange[1].format('YYYY-MM-DD')
        );
        params.start_date = utcRange.start;
        params.end_date = utcRange.end;
      }
      
      const response = await request('/api/accounts/summary', {
        method: 'GET',
        params,
      });
      
      if (response.success) {
        setAccountStats(response.data);
      }
    } catch (error) {
      console.error('加载账户统计失败:', error);
    }
  };

  // 获取账户列表
  const getAccounts = async (params: any) => {
    try {
      if (dateRange && dateRange[0] && dateRange[1]) {
        // 将北京时间的日期范围转换为UTC时间
        const utcRange = convertDateRangeToUTC(
          dateRange[0].format('YYYY-MM-DD'),
          dateRange[1].format('YYYY-MM-DD')
        );
        params.start_date = utcRange.start;
        params.end_date = utcRange.end;
      }
      
      const response = await request('/api/accounts', {
        method: 'GET',
        params,
      });
      
      if (response.success) {
        return {
          data: response.data || [],
          success: true,
          total: response.pagination?.total || 0,
        };
      }
      
      return {
        data: [],
        success: false,
        total: 0,
      };
    } catch (error) {
      console.error('获取账户列表失败:', error);
      return {
        data: [],
        success: false,
        total: 0,
      };
    }
  };

  // 添加账户
  const handleAddAccount = async (values: any) => {
    try {
      const accountData = {
        ...values,
        currency: selectedCurrency,
        original_amount: originalAmount,
        original_currency: selectedCurrency,
        balance: selectedCurrency === 'CNY' ? originalAmount : convertedAmount,
        exchange_rate: exchangeRate
      };
      
      const response = await request('/api/accounts', {
        method: 'POST',
        data: accountData,
      });
      
      if (response.success) {
        message.success('账户添加成功');
        setAddModalVisible(false);
        addForm.resetFields();
        setSelectedCurrency('CNY');
        setOriginalAmount(0);
        setConvertedAmount(0);
        setExchangeRate(1);
        actionRef.current?.reload();
        loadAccountStats();
      } else {
        message.error(response.message || '账户添加失败');
      }
    } catch (error) {
      console.error('添加账户失败:', error);
      message.error('添加账户失败');
    }
  };

  // 充值
  const handleRecharge = async (values: any) => {
    if (!currentAccount) return;
    
    try {
      const rechargeData = {
        ...values,
        amount: rechargeCurrency === 'CNY' ? rechargeOriginalAmount : rechargeConvertedAmount,
        original_amount: rechargeOriginalAmount,
        original_currency: rechargeCurrency,
        exchange_rate: rechargeExchangeRate
      };
      
      const response = await request('/api/accounts/recharge', {
        method: 'POST',
        data: {
          ...rechargeData,
          account_id: currentAccount.id
        },
      });
      
      if (response.success) {
        message.success('充值成功');
        setRechargeModalVisible(false);
        rechargeForm.resetFields();
        setRechargeCurrency('CNY');
        setRechargeOriginalAmount(0);
        setRechargeConvertedAmount(0);
        setRechargeExchangeRate(1);
        actionRef.current?.reload();
        loadAccountStats();
      } else {
        message.error(response.message || '充值失败');
      }
    } catch (error) {
      console.error('充值失败:', error);
      message.error('充值失败');
    }
  };

  // 切换账户状态
  const toggleAccountStatus = async (account: AccountItem) => {
    try {
      const newStatus = account.status === 'active' ? 'inactive' : 'active';
      
      const response = await request(`/api/accounts/${account.id}`, {
        method: 'PUT',
        data: { status: newStatus },
      });
      
      if (response.success) {
        message.success(`账户已${newStatus === 'active' ? '激活' : '停用'}`);
        actionRef.current?.reload();
        loadAccountStats();
      } else {
        message.error(response.message || '状态更新失败');
      }
    } catch (error) {
      console.error('更新账户状态失败:', error);
      message.error('状态更新失败');
    }
  };

  // 查看账户历史
  const viewAccountHistory = async (account: AccountItem) => {
    setCurrentAccount(account);
    setHistoryModalVisible(true);
    setLoading(true);
    
    try {
      // 获取充值记录
      const rechargeResponse = await request(`/api/accounts/${account.id}/recharges`, {
        method: 'GET',
      });
      
      if (rechargeResponse.success) {
        setRechargeRecords(rechargeResponse.recharges || []);
      }
      
      // 获取消费记录
      const consumptionResponse = await request(`/api/accounts/${account.id}/consumptions`, {
        method: 'GET',
      });
      
      if (consumptionResponse.success) {
        setConsumptionRecords(consumptionResponse.consumptions || []);
      }
    } catch (error) {
      console.error('获取账户历史失败:', error);
      message.error('获取账户历史失败');
    } finally {
      setLoading(false);
    }
  };

  // 删除账户
  const handleDelete = async (account: AccountItem) => {
    Modal.confirm({
      title: '确认删除',
      icon: <ExclamationCircleOutlined />,
      content: `确定要删除账户 "${account.account_name}" 吗？此操作不可恢复。`,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const response = await fetch(`/api/accounts/${account.id}?token=123`, {
            method: 'DELETE',
            headers: {
              'Content-Type': 'application/json',
            },
          });
          
          const data = await response.json();
          
          if (response.ok && data.success) {
            message.success('账户删除成功');
            actionRef.current?.reload();
            loadAccountStats();
          } else if (response.status === 400 && data.has_records) {
            // 显示强制删除确认对话框
            Modal.confirm({
              title: '强制删除确认',
              icon: <ExclamationCircleOutlined />,
              content: (
                <div>
                  <p>{data.message}</p>
                  <p>该账户包含：</p>
                  <ul>
                    <li>充值记录：{data.recharge_count} 条</li>
                    <li>消耗记录：{data.consumption_count} 条</li>
                  </ul>
                  <p style={{ color: 'red', fontWeight: 'bold' }}>
                    强制删除将同时删除所有相关记录，此操作不可恢复！
                  </p>
                </div>
              ),
              okText: '强制删除',
              okType: 'danger',
              cancelText: '取消',
              onOk: async () => {
                try {
                  const forceResponse = await fetch(`/api/accounts/${account.id}?token=123&force=true`, {
                    method: 'DELETE',
                    headers: {
                      'Content-Type': 'application/json',
                    },
                  });
                  
                  const forceData = await forceResponse.json();
                  
                  if (forceResponse.ok && forceData.success) {
                    message.success('账户删除成功');
                    actionRef.current?.reload();
                    loadAccountStats();
                  } else {
                    message.error(forceData.message || '删除失败');
                  }
                } catch (forceError) {
                  console.error('强制删除账户失败:', forceError);
                  message.error('删除失败');
                }
              },
            });
          } else {
            message.error(data.message || '删除失败');
          }
        } catch (error: any) {
          console.error('删除账户失败:', error);
          message.error('删除失败');
        }
      },
    });
  };

  useEffect(() => {
    loadAccountStats();
  }, [dateRange]);

  // 清理定时器
  useEffect(() => {
    return () => {
      if (debounceTimer) {
        clearTimeout(debounceTimer);
      }
      if (rechargeDebounceTimer) {
        clearTimeout(rechargeDebounceTimer);
      }
    };
  }, [debounceTimer, rechargeDebounceTimer]);

  const columns: ProColumns<AccountItem>[] = [
    {
      title: '账户名称',
      dataIndex: 'account_name',
      key: 'account_name',
      width: 150,
      ellipsis: true,
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 120,
      ellipsis: true,
    },
    {
      title: '账户ID',
      dataIndex: 'account_id',
      key: 'account_id',
      width: 150,
      ellipsis: true,
      hideInSearch: true,
    },
    {
      title: '余额',
      dataIndex: 'balance',
      key: 'balance',
      width: 120,
      hideInSearch: true,
      render: (_, record) => {
         const displayBalance = formatCurrency(record.balance, 'CNY');
         const originalDisplay = record.original_currency && record.original_currency !== 'CNY' 
           ? `(${formatCurrency(record.original_amount || 0, record.original_currency || 'USD')})` 
           : '';
        
        return (
          <Tooltip title={originalDisplay ? `原始金额: ${formatCurrency(record.original_amount || 0, record.original_currency || 'USD')}` : undefined}>
            <div>
              <div>{displayBalance}</div>
              {originalDisplay && (
                <div style={{ fontSize: '12px', color: '#666' }}>
                  {originalDisplay}
                </div>
              )}
            </div>
          </Tooltip>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      valueType: 'select',
      valueEnum: {
        active: { text: '正常', status: 'Success' },
        inactive: { text: '停用', status: 'Default' },
        suspended: { text: '暂停', status: 'Warning' },
      },
      render: (_, record) => {
        const statusConfig = {
          active: { color: 'green', text: '正常' },
          inactive: { color: 'default', text: '停用' },
          suspended: { color: 'orange', text: '暂停' },
        };
        const config = statusConfig[record.status] || statusConfig.inactive;
        return <Tag color={config.color}>{config.text}</Tag>;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      valueType: 'dateRange',
      hideInSearch: true,
      render: (_, record) => formatBeijingTime(record.created_at),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      width: 280,
      fixed: 'right',
      render: (_, record) => (
        <Space size="small" wrap>
          <Button
            key="recharge"
            type="link"
            size="small"
            icon={<DollarOutlined />}
            onClick={() => {
              setCurrentAccount(record);
              setRechargeModalVisible(true);
            }}
          >
            充值
          </Button>
          <Button
            key="history"
            type="link"
            size="small"
            icon={<HistoryOutlined />}
            onClick={() => viewAccountHistory(record)}
          >
            历史
          </Button>
          <Button
            key="toggle"
            type="link"
            size="small"
            onClick={() => toggleAccountStatus(record)}
          >
            {record.status === 'active' ? '停用' : '激活'}
          </Button>
          <Button
            key="delete"
            type="link"
            size="small"
            danger
            onClick={() => handleDelete(record)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <PageContainer>
      {/* 日期范围选择器 */}
      <Row style={{ marginBottom: 16 }}>
        <Col span={24}>
          <Card>
            <Space align="center">
              <span>统计时间范围：</span>
              <RangePicker
                value={dateRange}
                onChange={(dates) => setDateRange(dates)}
                placeholder={['开始日期', '结束日期']}
                allowClear
                style={{ width: 300 }}
              />
              <Button 
                type="link" 
                onClick={() => {
                  const now = dayjs().tz('Asia/Shanghai');
                  const startOfMonth = now.startOf('month');
                  const endOfMonth = now.endOf('month');
                  setDateRange([startOfMonth, endOfMonth]);
                }}
                size="small"
              >
                重置为当前月
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>
      
      {/* 统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="总余额"
              value={accountStats.total_balance}
              precision={2}
              prefix="¥"
              valueStyle={{ color: '#3f8600' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="账户总数"
              value={accountStats.total_accounts}
              valueStyle={{ color: '#1890ff' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={dateRange ? "选定期间充值" : "本月充值"}
              value={accountStats.month_recharge}
              precision={2}
              prefix="¥"
              valueStyle={{ color: '#722ed1' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={dateRange ? "选定期间消费" : "本月消费"}
              value={accountStats.month_consumption}
              precision={2}
              prefix="¥"
              valueStyle={{ color: '#cf1322' }}
            />
          </Card>
        </Col>
      </Row>

      <ProTable<AccountItem>
        headerTitle="账户管理"
        actionRef={actionRef}
        rowKey="id"
        search={{
          labelWidth: 'auto',
        }}
        scroll={{ x: 1200 }}
        toolBarRender={() => [
          <RangePicker
            key="dateRange"
            value={dateRange}
            onChange={setDateRange}
            placeholder={['开始日期', '结束日期']}
          />,
          <Button
            key="refresh"
            icon={<ReloadOutlined />}
            onClick={() => {
              setDateRange(null);
              actionRef.current?.reload();
            }}
          >
            重置
          </Button>,
          <Button
            type="primary"
            key="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setAddModalVisible(true);
            }}
          >
            新建账户
          </Button>,
        ]}
        request={getAccounts}
        columns={columns}
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
        }}
      />

      {/* 添加账户模态框 */}
      <Modal
        title="添加账户"
        open={addModalVisible}
        onCancel={() => {
          setAddModalVisible(false);
          addForm.resetFields();
          setSelectedCurrency('CNY');
          setOriginalAmount(0);
          setConvertedAmount(0);
          setExchangeRate(1);
        }}
        footer={null}
        width={600}
      >
        <Form
          form={addForm}
          layout="vertical"
          onFinish={handleAddAccount}
        >
          <Form.Item
            name="account_name"
            label="账户名称"
            rules={[{ required: true, message: '请输入账户名称' }]}
          >
            <Input placeholder="请输入账户名称" />
          </Form.Item>
          
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true, message: '请输入平台' }]}
          >
            <Input placeholder="请输入平台名称" />
          </Form.Item>
          
          <Form.Item
            name="account_id"
            label="账户ID"
          >
            <Input placeholder="请输入账户ID（可选）" />
          </Form.Item>
          
          <Form.Item
            name="description"
            label="描述"
          >
            <TextArea rows={3} placeholder="请输入账户描述（可选）" />
          </Form.Item>
          
          {/* 货币和金额输入 */}
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                label="货币类型"
                required
              >
                <Select
                  value={selectedCurrency}
                  onChange={(value) => {
                    setSelectedCurrency(value);
                    if (originalAmount > 0) {
                      debouncedCurrencyConvert(originalAmount, value);
                    }
                  }}
                  style={{ width: '100%' }}
                  placeholder="选择货币"
                >
                  {getCurrencyOptions().map(option => (
                    <Option key={option.value} value={option.value}>
                      {option.label}
                    </Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
            <Col span={16}>
              <Form.Item
                label="初始金额"
                required
              >
                <InputNumber
                  style={{ width: '100%' }}
                  min={0}
                  precision={2}
                  placeholder="0.00"
                  value={originalAmount}
                  onChange={(value) => {
                    const amount = value || 0;
                    setOriginalAmount(amount);
                    debouncedCurrencyConvert(amount, selectedCurrency);
                  }}
                  addonBefore={SUPPORTED_CURRENCIES[selectedCurrency as keyof typeof SUPPORTED_CURRENCIES]?.symbol || selectedCurrency}
                  suffix={
                    convertLoading ? (
                      <SyncOutlined spin />
                    ) : null
                  }
                />
              </Form.Item>
            </Col>
          </Row>
          
          {/* 汇率转换显示 */}
          {selectedCurrency !== 'CNY' && originalAmount > 0 && (
            <Form.Item label="转换为人民币">
              <div style={{ 
                padding: '8px 12px', 
                backgroundColor: '#f5f5f5', 
                borderRadius: '6px',
                border: '1px solid #d9d9d9'
              }}>
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <div>
                    <strong>原始金额：</strong>
                    {formatCurrency(originalAmount, selectedCurrency)}
                  </div>
                  <div>
                    <strong>人民币金额：</strong>
                    <span style={{ color: '#1890ff', fontSize: '16px', fontWeight: 'bold' }}>
                      {formatCurrency(convertedAmount, 'CNY')}
                    </span>
                  </div>
                  <div style={{ fontSize: '12px', color: '#666' }}>
                    汇率：1 {selectedCurrency} = {exchangeRate.toFixed(4)} CNY
                  </div>
                </Space>
              </div>
            </Form.Item>
          )}
          
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                确定
              </Button>
              <Button onClick={() => {
                setAddModalVisible(false);
                addForm.resetFields();
                setSelectedCurrency('CNY');
                setOriginalAmount(0);
                setConvertedAmount(0);
                setExchangeRate(1);
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 充值模态框 */}
      <Modal
        title={`充值 - ${currentAccount?.account_name}`}
        open={rechargeModalVisible}
        onCancel={() => {
          setRechargeModalVisible(false);
          rechargeForm.resetFields();
          setRechargeCurrency('CNY');
          setRechargeOriginalAmount(0);
          setRechargeConvertedAmount(0);
          setRechargeExchangeRate(1);
        }}
        footer={null}
        width={500}
      >
        <Form
          form={rechargeForm}
          layout="vertical"
          onFinish={handleRecharge}
        >
          <Form.Item label="充值货币类型">
            <Select
              value={rechargeCurrency}
              onChange={(value) => {
                setRechargeCurrency(value);
                if (rechargeOriginalAmount > 0) {
                  debouncedRechargeCurrencyConvert(rechargeOriginalAmount, value);
                }
              }}
              style={{ width: '100%' }}
            >
              {getCurrencyOptions().map(option => (
                <Option key={option.value} value={option.value}>
                  {option.label}
                </Option>
              ))}
            </Select>
          </Form.Item>
          
          <Form.Item
            name="amount"
            label="充值金额"
            rules={[{ required: true, message: '请输入充值金额' }]}
          >
            <InputNumber
              style={{ width: '100%' }}
              min={0.01}
              precision={2}
              placeholder="0.00"
              addonBefore={(SUPPORTED_CURRENCIES as any)[rechargeCurrency]?.symbol || '¥'}
              suffix={rechargeConvertLoading ? <SyncOutlined spin /> : undefined}
              onChange={(value) => {
                const amount = value || 0;
                setRechargeOriginalAmount(amount);
                debouncedRechargeCurrencyConvert(amount, rechargeCurrency);
              }}
            />
          </Form.Item>
          
          {rechargeCurrency !== 'CNY' && rechargeOriginalAmount > 0 && (
            <div style={{ marginBottom: 16, padding: 12, backgroundColor: '#f6f6f6', borderRadius: 6 }}>
              <div style={{ fontSize: 14, color: '#666', marginBottom: 4 }}>转换为人民币:</div>
              <div style={{ fontSize: 16, fontWeight: 'bold', color: '#1890ff' }}>
                ¥ {formatCurrency(rechargeConvertedAmount, 'CNY')}
              </div>
              <div style={{ fontSize: 12, color: '#999' }}>
                汇率: 1 {rechargeCurrency} = {rechargeExchangeRate.toFixed(4)} CNY
              </div>
            </div>
          )}
          
          <Form.Item
            name="recharge_method"
            label="充值方式"
            rules={[{ required: true, message: '请选择充值方式' }]}
          >
            <Select placeholder="请选择充值方式">
              <Option value="bank_transfer">银行转账</Option>
              <Option value="alipay">支付宝</Option>
              <Option value="wechat">微信支付</Option>
              <Option value="credit_card">信用卡</Option>
              <Option value="other">其他</Option>
            </Select>
          </Form.Item>
          
          <Form.Item
            name="transaction_id"
            label="交易ID"
          >
            <Input placeholder="请输入交易ID（可选）" />
          </Form.Item>
          
          <Form.Item
            name="description"
            label="备注"
          >
            <TextArea rows={3} placeholder="请输入备注（可选）" />
          </Form.Item>
          
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                确定充值
              </Button>
              <Button onClick={() => {
                setRechargeModalVisible(false);
                rechargeForm.resetFields();
                setRechargeCurrency('CNY');
                setRechargeOriginalAmount(0);
                setRechargeConvertedAmount(0);
                setRechargeExchangeRate(1);
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 历史记录模态框 */}
      <Modal
        title={`账户历史 - ${currentAccount?.account_name}`}
        open={historyModalVisible}
        onCancel={() => setHistoryModalVisible(false)}
        footer={null}
        width={800}
      >
        <div style={{ maxHeight: '60vh', overflowY: 'auto' }}>
          <h4>充值记录</h4>
          {rechargeRecords.length > 0 ? (
            <div style={{ marginBottom: 24 }}>
              {rechargeRecords.map((record) => (
                <Card key={record.id} size="small" style={{ marginBottom: 8 }}>
                  <Row>
                    <Col span={6}>
                      <strong>金额:</strong> ¥{record.amount.toFixed(2)}
                    </Col>
                    <Col span={6}>
                      <strong>方式:</strong> {record.recharge_method}
                    </Col>
                    <Col span={6}>
                      <strong>状态:</strong> 
                      <Tag color={record.status === 'completed' || record.status === 'confirmed' ? 'green' : record.status === 'pending' ? 'orange' : 'red'}>
                        {record.status === 'completed' || record.status === 'confirmed' ? '已完成' : record.status === 'pending' ? '待确认' : record.status === 'failed' ? '失败' : '已取消'}
                      </Tag>
                    </Col>
                    <Col span={6}>
                      <strong>时间:</strong> {formatShortBeijingTime(record.recharge_date)}
                    </Col>
                  </Row>
                  {record.description && (
                    <div style={{ marginTop: 8, color: '#666' }}>
                      备注: {record.description}
                    </div>
                  )}
                </Card>
              ))}
            </div>
          ) : (
            <div style={{ textAlign: 'center', color: '#999', marginBottom: 24 }}>暂无充值记录</div>
          )}
          
          <h4>消费记录</h4>
          {consumptionRecords.length > 0 ? (
            <div>
              {consumptionRecords.map((record) => (
                <Card key={record.id} size="small" style={{ marginBottom: 8 }}>
                  <Row>
                    <Col span={6}>
                      <strong>金额:</strong> ¥{record.amount.toFixed(2)}
                    </Col>
                    <Col span={6}>
                      <strong>类型:</strong> {record.consumption_type}
                    </Col>
                    <Col span={6}>
                      <strong>关联ID:</strong> {record.related_id || '-'}
                    </Col>
                    <Col span={6}>
                      <strong>时间:</strong> {formatShortBeijingTime(record.consumption_date)}
                    </Col>
                  </Row>
                  {record.description && (
                    <div style={{ marginTop: 8, color: '#666' }}>
                      备注: {record.description}
                    </div>
                  )}
                </Card>
              ))}
            </div>
          ) : (
            <div style={{ textAlign: 'center', color: '#999' }}>暂无消费记录</div>
          )}
        </div>
      </Modal>
    </PageContainer>
  );
};

export default Accounts;