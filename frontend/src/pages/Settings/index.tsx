import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  message,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Tag,
} from 'antd';
import React, { useEffect, useRef, useState } from 'react';
import {
  createFeeConfig,
  deleteFeeConfig,
  listFeeConfigs,
  updateFeeConfig,
} from '@/services/settings';
import { persistedPagination } from '@/utils/pagination';

const { Option } = Select;

interface FeeConfig {
  id: string;
  feeType: string;
  feeName: string;
  calculationMethod: 'percentage' | 'fixed' | 'percentage_plus_fixed';
  percentageRate?: number | null;
  fixedAmount?: number | null;
  currency?: string;
  isActive: boolean | undefined;
}

const Settings: React.FC = () => {
  const [feeForm] = Form.useForm();
  const [_feeLoading, setFeeLoading] = useState(false);
  const [feeModalVisible, setFeeModalVisible] = useState(false);
  const [editingFee, setEditingFee] = useState<FeeConfig | null>(null);
  const [feeConfigs, setFeeConfigs] = useState<FeeConfig[]>([]);
  const actionRef = useRef<ActionType>(null);
  // 费用配置相关函数
  const loadFeeConfigs = async () => {
    setFeeLoading(true);
    try {
      const data = await listFeeConfigs();

      if (data.success) {
        const configs = data.data.map((config) => ({
          id: config.id.toString(),
          feeType: config.fee_type,
          feeName: config.fee_name,
          calculationMethod:
            config.calculation_method as FeeConfig['calculationMethod'],
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
      currency: record.currency || 'USD',
    });
    setFeeModalVisible(true);
  };

  const handleDeleteFee = async (id: string) => {
    try {
      const data = await deleteFeeConfig(id);

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
        percentage_rate:
          values.calculationMethod === 'percentage' ||
          values.calculationMethod === 'percentage_plus_fixed'
            ? values.percentageRate
            : null,
        fixed_amount:
          values.calculationMethod === 'fixed' ||
          values.calculationMethod === 'percentage_plus_fixed'
            ? values.fixedAmount
            : null,
        currency: values.currency,
      };

      let data: Record<string, any>;
      if (editingFee) {
        // 更新费用配置
        data = await updateFeeConfig(editingFee.id, requestData);
      } else {
        // 添加新费用配置
        data = await createFeeConfig(requestData);
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
          percentage_plus_fixed: 'orange',
        };
        const textMap: Record<string, string> = {
          percentage: '百分比',
          fixed: '固定金额',
          percentage_plus_fixed: '百分比+固定金额',
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
        const currencySymbol =
          record.currency === 'CNY'
            ? '¥'
            : record.currency === 'USD'
              ? '$'
              : record.currency || '$';
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
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  useEffect(() => {
    loadFeeConfigs();
  }, []);

  return (
    <PageContainer
      title="费用配置"
      subTitle="维护全局费用类型与费率"
    >
      <Row gutter={[24, 24]}>
        {/* 费用配置管理 */}
        <Col span={24}>
          <Card
            title="费率与费用类型"
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
                ...persistedPagination('fee-configs', 10),
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
        <Form form={feeForm} layout="vertical" onFinish={handleSaveFee}>
          <Row gutter={16}>
            <Col xs={24} sm={8}>
              <Form.Item
                label="费用类型"
                name="feeType"
                rules={[{ required: true, message: '请输入费用类型' }]}
              >
                <Input placeholder="如：平台费、物流费" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
              <Form.Item
                label="费用名称"
                name="feeName"
                rules={[{ required: true, message: '请输入费用名称' }]}
              >
                <Input placeholder="如：Shopify交易费" />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
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
                const currencySymbol =
                  currency === 'CNY'
                    ? '¥'
                    : currency === 'USD'
                      ? '$'
                      : currency;
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
                  <Row gutter={16}>
                    <Col xs={24} sm={12}>
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
                    <Col xs={24} sm={12}>
                      <Form.Item
                        label={`固定金额（${(() => {
                          const currency = getFieldValue('currency') || 'USD';
                          return currency === 'CNY'
                            ? '¥'
                            : currency === 'USD'
                              ? '$'
                              : currency;
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
              <Button onClick={() => setFeeModalVisible(false)}>取消</Button>
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
