import { PageContainer, ProTable } from '@ant-design/pro-components';
import { request } from '@umijs/max';
import { 
  Card, 
  Form, 
  Input, 
  Select, 
  Switch, 
  Button, 
  message, 
  Divider, 
  Row, 
  Col, 
  Modal,
  Space,
  Tag,
  Popconfirm,
  InputNumber
} from 'antd';
import { 
  SaveOutlined, 
  ReloadOutlined, 
  PlusOutlined, 
  EditOutlined, 
  DeleteOutlined,
  ApiOutlined,
  SyncOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined
} from '@ant-design/icons';
import React, { useState, useRef, useEffect } from 'react';
import type { ActionType, ProColumns } from '@ant-design/pro-components';

const { Option } = Select;
const { TextArea } = Input;

interface SettingsForm {
  companyName: string;
  companyAddress: string;
  contactEmail: string;
  contactPhone: string;
  currency: string;
  language: string;
  timezone: string;
  emailNotifications: boolean;
  smsNotifications: boolean;
  autoBackup: boolean;
  backupFrequency: string;
  theme: string;
}

interface ShopifyConfig {
  shopUrl: string;
  apiKey: string;
  apiSecret: string;
  accessToken: string;
  isConnected: boolean;
}

interface FeeConfig {
  id: string;
  feeType: string;
  feeName: string;
  calculationMethod: 'percentage' | 'fixed' | 'percentage_plus_fixed';
  percentageRate?: number;
  fixedAmount?: number;
  currency?: string;
  isActive: boolean;
}

const Settings: React.FC = () => {
  const [form] = Form.useForm();
  const [shopifyForm] = Form.useForm();
  const [feeForm] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [shopifyLoading, setShopifyLoading] = useState(false);
  const [feeLoading, setFeeLoading] = useState(false);
  const [testingConnection, setTestingConnection] = useState(false);
  const [syncingOrders, setSyncingOrders] = useState(false);
  const [feeModalVisible, setFeeModalVisible] = useState(false);
  const [editingFee, setEditingFee] = useState<FeeConfig | null>(null);
  const [feeConfigs, setFeeConfigs] = useState<FeeConfig[]>([]);
  const actionRef = useRef<ActionType>(null);
  
  const [shopifyConfig, setShopifyConfig] = useState<ShopifyConfig>({
    shopUrl: '',
    apiKey: '',
    apiSecret: '',
    accessToken: '',
    isConnected: false,
  });

  // 初始设置值
  const initialValues: SettingsForm = {
    companyName: 'CaseLedger 公司',
    companyAddress: '北京市朝阳区某某街道123号',
    contactEmail: 'admin@caseledger.com',
    contactPhone: '+86 138-0000-0000',
    currency: 'CNY',
    language: 'zh-CN',
    timezone: 'Asia/Shanghai',
    emailNotifications: true,
    smsNotifications: false,
    autoBackup: true,
    backupFrequency: 'daily',
    theme: 'light',
  };

  const handleSave = async (values: SettingsForm) => {
    setLoading(true);
    try {
      // 模拟保存设置
      await new Promise(resolve => setTimeout(resolve, 1000));
      console.log('保存设置:', values);
      message.success('设置保存成功！');
    } catch (error) {
      message.error('保存设置失败，请重试。');
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    form.setFieldsValue(initialValues);
    message.info('设置已重置为默认值');
  };

  // 加载Shopify配置
  const loadShopifyConfig = async () => {
    try {
      const data = await request('/api/settings/shopify');
      if (data.success) {
        const config = {
          shopUrl: data.data.shop_url || '',
          apiKey: data.data.api_key || '',
          apiSecret: data.data.api_secret || '',
          accessToken: data.data.access_token || '',
          isConnected: data.data.is_active || false
        };
        setShopifyConfig(config);
        shopifyForm.setFieldsValue(config);
      }
    } catch (error) {
      console.error('加载Shopify配置失败:', error);
      message.error('加载Shopify配置失败');
    }
  };

  // 保存Shopify配置
  const handleSaveShopifyConfig = async (values: ShopifyConfig) => {
    setShopifyLoading(true);
    try {
      const data = await request('/api/settings/shopify', {
        method: 'POST',
        data: {
          shop_url: values.shopUrl,
          api_key: values.apiKey,
          api_secret: values.apiSecret,
          access_token: values.accessToken
        }
      });
      if (data.success) {
        setShopifyConfig({ ...values, isConnected: false });
        message.success('Shopify配置保存成功！');
        // 重新加载配置以获取最新状态
        loadShopifyConfig();
      } else {
        message.error(data.message || '保存失败，请重试');
      }
    } catch (error) {
      message.error('保存失败，请重试');
    } finally {
      setShopifyLoading(false);
    }
  };

  // 测试Shopify连接
  const handleTestConnection = async () => {
    const values = shopifyForm.getFieldsValue();
    if (!values.shopUrl || !values.apiKey || !values.apiSecret || !values.accessToken) {
      message.warning('请先填写所有配置信息');
      return;
    }
    
    setTestingConnection(true);
    try {
      const data = await request('/api/settings/shopify/test', {
        method: 'POST'
      });
      if (data.success) {
        setShopifyConfig(prev => ({ ...prev, isConnected: true }));
        message.success('连接测试成功！');
      } else {
        setShopifyConfig(prev => ({ ...prev, isConnected: false }));
        message.error(data.message || '连接测试失败，请检查配置信息');
      }
    } catch (error) {
      setShopifyConfig(prev => ({ ...prev, isConnected: false }));
      message.error('连接测试失败，请检查配置信息');
    } finally {
      setTestingConnection(false);
    }
  };

  // 同步Shopify订单
  const handleSyncOrders = async () => {
    if (!shopifyConfig.isConnected) {
      message.warning('请先测试连接成功后再同步订单');
      return;
    }
    
    setSyncingOrders(true);
    try {
      const data = await request('/api/settings/shopify/sync', {
        method: 'POST',
        data: {
          days_back: 30,
          limit: 250
        }
      });
      if (data.success) {
        message.success(`订单同步成功！同步了 ${data.synced_count || 0} 个订单`);
      } else {
        message.error(data.message || '订单同步失败，请重试');
      }
    } catch (error) {
      message.error('订单同步失败，请重试');
    } finally {
      setSyncingOrders(false);
    }
  };

  // 费用配置相关函数
  const loadFeeConfigs = async () => {
    setFeeLoading(true);
    try {
      const data = await request('/api/settings/fees');
      
      if (data.success) {
        const configs = data.data.map((config: any) => ({
          id: config.id.toString(),
          feeType: config.fee_type,
          feeName: config.fee_name,
          calculationMethod: config.calculation_method,
          percentageRate: config.percentage_rate,
          fixedAmount: config.fixed_amount,
          currency: config.currency,
          isActive: config.is_active,
        }));
        setFeeConfigs(configs);
      } else {
        message.error('加载费用配置失败');
      }
    } catch (error) {
      console.error('加载费用配置失败:', error);
      message.error('加载费用配置失败');
    } finally {
      setFeeLoading(false);
    }
  };

  const handleAddFee = () => {
    setEditingFee(null);
    feeForm.resetFields();
    setFeeModalVisible(true);
  };

  const handleEditFee = (record: FeeConfig) => {
    setEditingFee(record);
    feeForm.setFieldsValue({
      ...record,
      currency: record.currency || 'USD'
    });
    setFeeModalVisible(true);
  };

  const handleDeleteFee = async (id: string) => {
    try {
      const data = await request(`/api/settings/fees/${id}`, {
        method: 'DELETE',
      });
      
      if (data.success) {
        message.success('费用配置删除成功');
        loadFeeConfigs(); // 重新加载费用配置列表
      } else {
        message.error(data.message || '删除失败');
      }
    } catch (error) {
      console.error('删除费用配置失败:', error);
      message.error('删除失败，请重试');
    }
  };

  const handleSaveFee = async (values: any) => {
    try {
      const requestData = {
        fee_type: values.feeType,
        fee_name: values.feeName,
        description: values.description || '',
        calculation_method: values.calculationMethod,
        percentage_rate: (values.calculationMethod === 'percentage' || values.calculationMethod === 'percentage_plus_fixed') ? values.percentageRate : null,
        fixed_amount: (values.calculationMethod === 'fixed' || values.calculationMethod === 'percentage_plus_fixed') ? values.fixedAmount : null,
        currency: values.currency,
      };
      
      let data;
      if (editingFee) {
        // 更新费用配置
        data = await request(`/api/settings/fee-configs/${editingFee.id}`, {
          method: 'PUT',
          data: requestData,
        });
      } else {
        // 添加新费用配置
        data = await request('/api/settings/fee-configs', {
          method: 'POST',
          data: requestData,
        });
      }
      
      if (data.success) {
         message.success(editingFee ? '费用配置更新成功' : '费用配置添加成功');
         setFeeModalVisible(false);
         setEditingFee(null);
         feeForm.resetFields();
         loadFeeConfigs(); // 重新加载费用配置列表
       } else {
         message.error(data.message || '保存失败');
       }
    } catch (error) {
      console.error('保存费用配置失败:', error);
      message.error('保存失败，请重试');
    }
  };

  // 费用配置表格列定义
  const feeColumns: ProColumns<FeeConfig>[] = [
    {
      title: '费用类型',
      dataIndex: 'feeType',
      width: 120,
    },
    {
      title: '费用名称',
      dataIndex: 'feeName',
      width: 150,
    },
    {
      title: '计算方式',
      dataIndex: 'calculationMethod',
      width: 100,
      render: (_, record) => {
        const colorMap: Record<string, string> = {
          percentage: 'blue',
          fixed: 'green',
          percentage_plus_fixed: 'orange'
        };
        const textMap: Record<string, string> = {
          percentage: '百分比',
          fixed: '固定金额',
          percentage_plus_fixed: '百分比+固定金额'
        };
        const method = record.calculationMethod;
        return (
          <Tag color={colorMap[method] || 'default'}>
            {textMap[method] || method}
          </Tag>
        );
      },
    },
    {
      title: '费率/金额',
      width: 120,
      render: (_, record) => {
        const currencySymbol = record.currency === 'CNY' ? '¥' : record.currency === 'USD' ? '$' : record.currency || '$';
        if (record.calculationMethod === 'percentage') {
          return `${record.percentageRate}%`;
        } else if (record.calculationMethod === 'fixed') {
          return `${currencySymbol}${record.fixedAmount}`;
        } else if (record.calculationMethod === 'percentage_plus_fixed') {
          return `${record.percentageRate}% + ${currencySymbol}${record.fixedAmount}`;
        }
        return '-';
      },
    },
    {
      title: '货币',
      dataIndex: 'currency',
      width: 80,
      render: (currency) => currency || 'USD',
    },
    {
      title: '状态',
      dataIndex: 'isActive',
      width: 80,
      render: (isActive) => (
        <Tag color={isActive ? 'success' : 'default'}>
          {isActive ? '启用' : '禁用'}
        </Tag>
      ),
    },
    {
      title: '操作',
      width: 120,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEditFee(record)}
          >
            编辑
          </Button>
          <Popconfirm
            title="确定要删除这个费用配置吗？"
            onConfirm={() => handleDeleteFee(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
            >
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  useEffect(() => {
    loadShopifyConfig();
    loadFeeConfigs();
  }, []);

  return (
    <PageContainer
      title="系统设置"
      subTitle="配置系统参数和偏好设置"
    >
      <Row gutter={[24, 24]}>
        {/* Shopify集成配置 */}
        <Col span={24}>
          <Card 
            title={
              <Space>
                <ApiOutlined />
                Shopify集成配置
                {shopifyConfig.isConnected ? (
                  <Tag color="success" icon={<CheckCircleOutlined />}>
                    已连接
                  </Tag>
                ) : (
                  <Tag color="error" icon={<CloseCircleOutlined />}>
                    未连接
                  </Tag>
                )}
              </Space>
            }
            bordered={false}
          >
            <Form
              form={shopifyForm}
              layout="vertical"
              onFinish={handleSaveShopifyConfig}
              initialValues={shopifyConfig}
            >
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item
                    label="店铺URL"
                    name="shopUrl"
                    rules={[{ required: true, message: '请输入店铺URL' }]}
                  >
                    <Input placeholder="https://your-shop.myshopify.com" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item
                    label="API Key"
                    name="apiKey"
                    rules={[{ required: true, message: '请输入API Key' }]}
                  >
                    <Input placeholder="输入API Key" />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item
                    label="API Secret"
                    name="apiSecret"
                    rules={[{ required: true, message: '请输入API Secret' }]}
                  >
                    <Input.Password placeholder="输入API Secret" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item
                    label="Access Token"
                    name="accessToken"
                    rules={[{ required: true, message: '请输入Access Token' }]}
                  >
                    <Input.Password placeholder="输入Access Token" />
                  </Form.Item>
                </Col>
              </Row>
              <Space>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={shopifyLoading}
                  icon={<SaveOutlined />}
                >
                  保存配置
                </Button>
                <Button
                  onClick={handleTestConnection}
                  loading={testingConnection}
                  icon={<ApiOutlined />}
                >
                  测试连接
                </Button>
                <Button
                  onClick={handleSyncOrders}
                  loading={syncingOrders}
                  disabled={!shopifyConfig.isConnected}
                  icon={<SyncOutlined />}
                >
                  同步订单
                </Button>
              </Space>
            </Form>
          </Card>
        </Col>

        {/* 费用配置管理 */}
        <Col span={24}>
          <Card 
            title="费用配置管理"
            bordered={false}
            extra={
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={handleAddFee}
              >
                添加费用配置
              </Button>
            }
          >
            <ProTable<FeeConfig>
              actionRef={actionRef}
              columns={feeColumns}
              dataSource={feeConfigs}
              rowKey="id"
              search={false}
              pagination={{
                pageSize: 10,
                showSizeChanger: true,
              }}
              toolBarRender={false}
            />
          </Card>
        </Col>


      </Row>

      {/* 费用配置模态框 */}
      <Modal
        title={editingFee ? '编辑费用配置' : '添加费用配置'}
        open={feeModalVisible}
        onCancel={() => setFeeModalVisible(false)}
        footer={null}
        width={600}
      >
        <Form
          form={feeForm}
          layout="vertical"
          onFinish={handleSaveFee}
        >
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item
                label="费用类型"
                name="feeType"
                rules={[{ required: true, message: '请输入费用类型' }]}
              >
                <Input placeholder="如：平台费、物流费" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="费用名称"
                name="feeName"
                rules={[{ required: true, message: '请输入费用名称' }]}
              >
                <Input placeholder="如：Shopify交易费" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item
                label="货币单位"
                name="currency"
                rules={[{ required: true, message: '请选择货币单位' }]}
                initialValue="USD"
              >
                <Select placeholder="请选择货币">
                  <Option value="USD">美元 (USD)</Option>
                  <Option value="EUR">欧元 (EUR)</Option>
                  <Option value="GBP">英镑 (GBP)</Option>
                  <Option value="CAD">加元 (CAD)</Option>
                  <Option value="AUD">澳元 (AUD)</Option>
                  <Option value="JPY">日元 (JPY)</Option>
                  <Option value="CNY">人民币 (CNY)</Option>
                </Select>
              </Form.Item>
            </Col>
          </Row>
          <Form.Item
            label="计算方式"
            name="calculationMethod"
            rules={[{ required: true, message: '请选择计算方式' }]}
          >
            <Select placeholder="请选择计算方式">
              <Option value="percentage">百分比</Option>
              <Option value="fixed">固定金额</Option>
              <Option value="percentage_plus_fixed">百分比 + 固定金额</Option>
            </Select>
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prevValues, currentValues) =>
              prevValues.calculationMethod !== currentValues.calculationMethod
            }
          >
            {({ getFieldValue }) => {
              const calculationMethod = getFieldValue('calculationMethod');
              if (calculationMethod === 'percentage') {
                return (
                  <Form.Item
                    label="费率（%）"
                    name="percentageRate"
                    rules={[{ required: true, message: '请输入费率' }]}
                  >
                    <InputNumber
                      min={0}
                      max={100}
                      step={0.1}
                      placeholder="如：2.9"
                      style={{ width: '100%' }}
                    />
                  </Form.Item>
                );
              }
              if (calculationMethod === 'fixed') {
                const currency = getFieldValue('currency') || 'USD';
                const currencySymbol = currency === 'CNY' ? '¥' : currency === 'USD' ? '$' : currency;
                return (
                  <Form.Item
                    label={`固定金额（${currencySymbol}）`}
                    name="fixedAmount"
                    rules={[{ required: true, message: '请输入固定金额' }]}
                  >
                    <InputNumber
                      min={0}
                      step={0.01}
                      placeholder="如：15.00"
                      style={{ width: '100%' }}
                    />
                  </Form.Item>
                );
              }
              if (calculationMethod === 'percentage_plus_fixed') {
                return (
                  <>
                    <Row gutter={16}>
                      <Col span={12}>
                        <Form.Item
                          label="费率（%）"
                          name="percentageRate"
                          rules={[{ required: true, message: '请输入费率' }]}
                        >
                          <InputNumber
                            min={0}
                            max={100}
                            step={0.1}
                            placeholder="如：2.9"
                            style={{ width: '100%' }}
                          />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item
                          label={`固定金额（${(() => {
                            const currency = getFieldValue('currency') || 'USD';
                            return currency === 'CNY' ? '¥' : currency === 'USD' ? '$' : currency;
                          })()}）`}
                          name="fixedAmount"
                          rules={[{ required: true, message: '请输入固定金额' }]}
                        >
                          <InputNumber
                            min={0}
                            step={0.01}
                            placeholder="如：0.30"
                            style={{ width: '100%' }}
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                  </>
                );
              }
              return null;
            }}
          </Form.Item>
          <Form.Item
            label="状态"
            name="isActive"
            valuePropName="checked"
            initialValue={true}
          >
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => setFeeModalVisible(false)}>
                取消
              </Button>
              <Button type="primary" htmlType="submit">
                {editingFee ? '更新' : '添加'}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
};

export default Settings;