import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Button, Space, Modal, Form, Input, Select, message, Tag, Popconfirm, Tooltip } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, EyeOutlined, EyeInvisibleOutlined, SearchOutlined, CopyOutlined } from '@ant-design/icons';
import React, { useState, useRef, useEffect } from 'react';
import type { ProColumns, ActionType } from '@ant-design/pro-components';
import { request } from '@umijs/max';
import { formatBeijingTime } from '@/utils/timezone';

const { TextArea } = Input;
const { Option } = Select;

interface PlatformAccountItem {
  id: string;
  platform: string;
  username: string;
  notes?: string;
  created_at: string;
  updated_at: string;
}

interface CreateFormData {
  platform: string;
  username: string;
  password: string;
  notes?: string;
}

interface UpdateFormData {
  platform?: string;
  username?: string;
  password?: string;
  notes?: string;
}

const PlatformAccounts: React.FC = () => {
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [updateModalVisible, setUpdateModalVisible] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState<{ [key: string]: boolean }>({});
  const [passwords, setPasswords] = useState<{ [key: string]: string }>({});
  const [currentRecord, setCurrentRecord] = useState<PlatformAccountItem | null>(null);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [createForm] = Form.useForm();
  const [updateForm] = Form.useForm();
  const actionRef = useRef<ActionType>(null);

  // 获取平台列表
  const fetchPlatforms = async () => {
    try {
      const response = await request('/api/platform-accounts/platforms');
      if (response.success) {
        setPlatforms(response.data || []);
      }
    } catch (error) {
      console.error('获取平台列表失败:', error);
    }
  };

  useEffect(() => {
    fetchPlatforms();
  }, []);

  // 获取平台账户列表
  const fetchPlatformAccounts = async (params: any) => {
    try {
      const response = await request('/api/platform-accounts', {
        params: {
          page: params.current,
          per_page: params.pageSize,
          platform: params.platform,
          search: params.search,
        },
      });

      if (response.success) {
        return {
          data: response.data,
          success: true,
          total: response.pagination.total,
        };
      }
      return {
        data: [],
        success: false,
        total: 0,
      };
    } catch (error) {
      message.error('获取平台账户列表失败');
      return {
        data: [],
        success: false,
        total: 0,
      };
    }
  };

  // 创建平台账户
  const handleCreate = async (values: CreateFormData) => {
    try {
      const response = await request('/api/platform-accounts', {
        method: 'POST',
        data: values,
      });

      if (response.success) {
        message.success('平台账户创建成功');
        setCreateModalVisible(false);
        createForm.resetFields();
        actionRef.current?.reload();
        fetchPlatforms(); // 刷新平台列表
      } else {
        message.error(response.message || '创建失败');
      }
    } catch (error) {
      message.error('创建平台账户失败');
    }
  };

  // 更新平台账户
  const handleUpdate = async (values: UpdateFormData) => {
    if (!currentRecord) return;

    try {
      const response = await request(`/api/platform-accounts/${currentRecord.id}`, {
        method: 'PUT',
        data: values,
      });

      if (response.success) {
        message.success('平台账户更新成功');
        setUpdateModalVisible(false);
        updateForm.resetFields();
        setCurrentRecord(null);
        actionRef.current?.reload();
        fetchPlatforms(); // 刷新平台列表
      } else {
        message.error(response.message || '更新失败');
      }
    } catch (error) {
      message.error('更新平台账户失败');
    }
  };

  // 删除平台账户
  const handleDelete = async (id: string) => {
    try {
      const response = await request(`/api/platform-accounts/${id}`, {
        method: 'DELETE',
      });

      if (response.success) {
        message.success('平台账户删除成功');
        actionRef.current?.reload();
        fetchPlatforms(); // 刷新平台列表
      } else {
        message.error(response.message || '删除失败');
      }
    } catch (error) {
      message.error('删除平台账户失败');
    }
  };

  // 获取密码
  const fetchPassword = async (id: string) => {
    try {
      const response = await request(`/api/platform-accounts/${id}/password`);
      if (response.success && response.data.password) {
        return response.data.password;
      }
      return null;
    } catch (error) {
      message.error('获取密码失败');
      return null;
    }
  };

  // 复制到剪贴板
  const copyToClipboard = async (text: string, type: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success(`${type}已复制到剪贴板`);
    } catch (error) {
      message.error('复制失败');
    }
  };

  // 切换密码显示状态
  const togglePasswordVisibility = async (record: PlatformAccountItem) => {
    const isVisible = passwordVisible[record.id];
    
    if (!isVisible) {
      const password = await fetchPassword(record.id);
      if (password) {
        setPasswords(prev => ({ ...prev, [record.id]: password }));
        setPasswordVisible(prev => ({ ...prev, [record.id]: true }));
        // 5秒后自动隐藏密码
        setTimeout(() => {
          setPasswordVisible(prev => ({ ...prev, [record.id]: false }));
        }, 5000);
      }
    } else {
      setPasswordVisible(prev => ({ ...prev, [record.id]: false }));
    }
  };

  // 编辑按钮点击处理
  const handleEditClick = (record: PlatformAccountItem) => {
    setCurrentRecord(record);
    updateForm.setFieldsValue({
      platform: record.platform,
      username: record.username,
      notes: record.notes,
    });
    setUpdateModalVisible(true);
  };

  const columns: ProColumns<PlatformAccountItem>[] = [
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 120,
      render: (text) => <Tag color="blue">{text}</Tag>,
      renderFormItem: () => (
        <Select placeholder="选择平台" allowClear>
          {platforms.map(platform => (
            <Option key={platform} value={platform}>{platform}</Option>
          ))}
        </Select>
      ),
    },
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      width: 200,
      render: (_, record) => (
        <Space>
          <span>{record.username}</span>
          <Tooltip title="复制用户名">
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined />}
              onClick={() => copyToClipboard(record.username || '', '用户名')}
            />
          </Tooltip>
        </Space>
      ),
    },
    {
      title: '密码',
      key: 'password',
      width: 220,
      search: false,
      render: (_, record) => (
        <Space>
          <span style={{ fontFamily: 'monospace', minWidth: '100px' }}>
            {passwordVisible[record.id] ? passwords[record.id] || '••••••••' : '••••••••'}
          </span>
          <Tooltip title={passwordVisible[record.id] ? '隐藏密码' : '显示密码'}>
            <Button
              type="text"
              size="small"
              icon={passwordVisible[record.id] ? <EyeInvisibleOutlined /> : <EyeOutlined />}
              onClick={() => togglePasswordVisibility(record)}
            />
          </Tooltip>
          {passwordVisible[record.id] && passwords[record.id] && (
            <Tooltip title="复制密码">
              <Button
                type="text"
                size="small"
                icon={<CopyOutlined />}
                onClick={() => copyToClipboard(passwords[record.id], '密码')}
              />
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '备注',
      dataIndex: 'notes',
      key: 'notes',
      width: 200,
      search: false,
      ellipsis: true,
      render: (text) => text || '-',
    },

    {
      title: '操作',
      key: 'action',
      width: 120,
      search: false,
      render: (_, record) => (
        <Space>
          <Tooltip title="编辑">
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={() => handleEditClick(record)}
            />
          </Tooltip>
          <Popconfirm
            title="确定要删除这个平台账户吗？"
            onConfirm={() => handleDelete(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Tooltip title="删除">
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <PageContainer>
      <ProTable<PlatformAccountItem>
        headerTitle="平台账户管理"
        actionRef={actionRef}
        rowKey="id"
        search={{
          labelWidth: 'auto',
          optionRender: (searchConfig, formProps, dom) => [
            ...dom,
            <Button
              key="search"
              type="primary"
              icon={<SearchOutlined />}
              onClick={() => {
                formProps?.form?.submit();
              }}
            >
              搜索
            </Button>,
          ],
        }}
        toolBarRender={() => [
          <Button
            key="create"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateModalVisible(true)}
          >
            新建账户
          </Button>,
        ]}
        request={fetchPlatformAccounts}
        columns={columns}
        pagination={{
          defaultPageSize: 20,
          showSizeChanger: true,
          showQuickJumper: true,
        }}
      />

      {/* 创建账户弹窗 */}
      <Modal
        title="新建平台账户"
        open={createModalVisible}
        onCancel={() => {
          setCreateModalVisible(false);
          createForm.resetFields();
        }}
        footer={null}
        width={600}
      >
        <Form
          form={createForm}
          layout="vertical"
          onFinish={handleCreate}
        >
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true, message: '请输入平台名称' }]}
          >
            <Input placeholder="请输入平台名称，如：GitHub、微信、支付宝等" />
          </Form.Item>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="请输入用户名或邮箱" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="请输入密码" />
          </Form.Item>
          <Form.Item
            name="notes"
            label="备注"
          >
            <TextArea
              placeholder="请输入备注信息（可选）"
              rows={3}
              maxLength={500}
              showCount
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                创建
              </Button>
              <Button onClick={() => {
                setCreateModalVisible(false);
                createForm.resetFields();
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑账户弹窗 */}
      <Modal
        title="编辑平台账户"
        open={updateModalVisible}
        onCancel={() => {
          setUpdateModalVisible(false);
          updateForm.resetFields();
          setCurrentRecord(null);
        }}
        footer={null}
        width={600}
      >
        <Form
          form={updateForm}
          layout="vertical"
          onFinish={handleUpdate}
        >
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true, message: '请输入平台名称' }]}
          >
            <Input placeholder="请输入平台名称" />
          </Form.Item>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="请输入用户名或邮箱" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            extra="留空则不修改密码"
          >
            <Input.Password placeholder="请输入新密码（留空不修改）" />
          </Form.Item>
          <Form.Item
            name="notes"
            label="备注"
          >
            <TextArea
              placeholder="请输入备注信息（可选）"
              rows={3}
              maxLength={500}
              showCount
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                更新
              </Button>
              <Button onClick={() => {
                setUpdateModalVisible(false);
                updateForm.resetFields();
                setCurrentRecord(null);
              }}>
                取消
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
};

export default PlatformAccounts;