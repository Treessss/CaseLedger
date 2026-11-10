import {
  DeleteOutlined,
  EditOutlined,
  LinkOutlined,
  PlusOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Divider,
  Form,
  Input,
  Modal,
  message,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import React, { useRef, useState } from 'react';
import {
  createShop,
  deleteShop,
  listShops,
  type ShopItem,
  type ShopPayload,
  syncShopOrders,
  testShopConnection,
  updateShop,
} from '@/services/shops/api';
import {
  getErrorData,
  getErrorMessage,
  isValidationError,
} from '@/utils/error';
import { formatBeijingTime } from '@/utils/timezone';
import { persistedPagination } from '@/utils/pagination';

const { TextArea } = Input;

const CURRENCIES = ['USD', 'CNY', 'EUR', 'GBP', 'SGD', 'JPY', 'HKD', 'AUD'];

const Shops: React.FC = () => {
  const actionRef = useRef<ActionType>(null);
  const [form] = Form.useForm<ShopPayload>();
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<ShopItem | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ currency: 'USD', is_active: true });
    setModalVisible(true);
  };

  const openEdit = (record: ShopItem) => {
    setEditing(record);
    form.setFieldsValue({
      name: record.name,
      shop_url: record.shop_url,
      currency: record.currency,
      notes: record.notes,
      is_active: record.is_active,
      oauth_client_id: record.oauth_client_id,
      // 密钥回显为打码值，留空表示不修改
      oauth_client_secret: record.oauth_client_secret,
      fourpx_app_name: record.fourpx_app_name || '',
      fourpx_app_key: '',
      fourpx_app_secret: '',
      fourpx_resource_code: record.fourpx_resource_code || '',
      fourpx_environment: record.fourpx_environment || 'test',
    });
    setModalVisible(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      if (editing) {
        await updateShop(editing.id, values);
        message.success('店铺更新成功');
      } else {
        await createShop(values);
        message.success('店铺创建成功');
      }
      setModalVisible(false);
      actionRef.current?.reload();
    } catch (error) {
      if (isValidationError(error)) {
        return; // 表单校验失败
      }
      message.error(getErrorMessage(error, '保存失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = (record: ShopItem) => {
    const remove = async (force = false) => {
      try {
        await deleteShop(record.id, force);
        message.success('店铺已删除');
        actionRef.current?.reload();
      } catch (error) {
        const detail = getErrorData<{
          message: string;
          has_data?: boolean;
          order_count?: number;
          expense_count?: number;
        }>(error);
        if (detail?.has_data) {
          Modal.confirm({
            title: '该店铺已有业务数据',
            content: (
              <div>
                <p>{detail.message}</p>
                <p>
                  订单：{detail.order_count} 条，费用：{detail.expense_count} 条
                </p>
                <p style={{ color: 'red' }}>
                  强制删除会让这些数据失去店铺归属，建议改为「停用店铺」。
                </p>
              </div>
            ),
            okText: '强制删除',
            okType: 'danger',
            cancelText: '取消',
            onOk: () => remove(true),
          });
          return;
        }
        message.error(detail?.message || '删除失败');
      }
    };
    Modal.confirm({
      title: '确认删除店铺',
      content: `确定要删除「${record.name}」吗？`,
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => remove(false),
    });
  };

  const handleTest = async (record: ShopItem) => {
    const hide = message.loading('正在测试连接...', 0);
    try {
      const res = await testShopConnection(record.id);
      hide();
      if (res.success) {
        message.success('连接测试成功');
      } else {
        message.error(res.message || '连接测试失败');
      }
    } catch (error) {
      hide();
      message.error(getErrorMessage(error, '连接测试失败'));
    }
  };

  const handleSync = async (record: ShopItem) => {
    const hide = message.loading('正在同步订单...', 0);
    try {
      const res = await syncShopOrders(record.id);
      hide();
      message.success(res.message || '同步完成');
      actionRef.current?.reload();
    } catch (error) {
      hide();
      message.error(getErrorMessage(error, '同步失败'));
    }
  };

  const columns: ProColumns<ShopItem>[] = [
    {
      title: '店铺名称',
      dataIndex: 'name',
      width: 190,
      render: (_, record) => (
        <Space size={4} style={{ whiteSpace: 'nowrap' }}>
          <Typography.Text
            ellipsis={{ tooltip: record.name }}
            style={{ maxWidth: record.is_active ? 165 : 110 }}
          >
            {record.name}
          </Typography.Text>
          {!record.is_active && <Tag color="default">已停用</Tag>}
        </Space>
      ),
    },
    {
      title: '店铺域名',
      dataIndex: 'shop_url',
      width: 220,
      render: (_, record) => (
        <Typography.Text
          copyable={{ text: record.shop_url }}
          ellipsis={{ tooltip: record.shop_url }}
          style={{ maxWidth: 195 }}
        >
          {record.shop_url}
        </Typography.Text>
      ),
    },
    // 先放「能不能用」的状态列，再放数量统计：
    // 表比屏幕宽时，第一屏就能看到绑定与同步状态，不用横向滚动
    {
      title: '绑定状态',
      dataIndex: 'has_credentials',
      width: 100,
      search: false,
      render: (_, record) =>
        record.has_credentials ? (
          <Tooltip title="域名 + Client ID + Client Secret 已齐全，可以正常读取订单与客户信息">
            <Tag color="green">已绑定</Tag>
          </Tooltip>
        ) : (
          <Tooltip title="缺少域名 / Client ID / Client Secret，无法连接 Shopify">
            <Tag color="orange">未绑定</Tag>
          </Tooltip>
        ),
    },
    {
      title: '4PX',
      dataIndex: 'has_fourpx_credentials',
      width: 115,
      search: false,
      render: (_, record) => record.has_fourpx_credentials
        ? <Tag color="blue">凭据已保存</Tag> : <Tag>未配置</Tag>,
    },
    {
      title: '最后同步',
      dataIndex: 'last_sync',
      width: 160,
      search: false,
      // 后端按 UTC 存储，统一显示北京时间
      render: (_, record) => formatBeijingTime(record.last_sync || ''),
    },
    {
      title: '同步状态',
      width: 120,
      search: false,
      render: (_, record) => record.last_sync_error ? (
        <Tooltip title={`${formatBeijingTime(record.last_sync_attempt_at || '')}：${record.last_sync_error}`}>
          <Tag color="red">最近同步失败</Tag>
        </Tooltip>
      ) : record.last_sync ? <Tag color="green">最近同步成功</Tag> : <Tag>尚未同步</Tag>,
    },
    {
      title: '订单数',
      dataIndex: 'order_count',
      width: 85,
      search: false,
    },
    {
      title: '费用数',
      dataIndex: 'expense_count',
      width: 85,
      search: false,
    },
    {
      title: '币种',
      dataIndex: 'currency',
      width: 75,
    },
    {
      title: '待补真实手续费',
      dataIndex: 'pending_fee_count',
      width: 125,
      search: false,
      render: (_, record) => <Tag color={record.pending_fee_count ? 'orange' : 'green'}>{record.pending_fee_count || 0} 笔</Tag>,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 235,
      // 始终固定在右侧：这张表列多，不固定的话「操作」会滚出可视区，
      // 用户在 1440 宽度的屏幕上根本看不到编辑/同步入口
      fixed: 'right',
      render: (_, record) => [
        <a key="edit" onClick={() => openEdit(record)} className="table-action">
          <EditOutlined /> 编辑
        </a>,
        <Tooltip key="test" title="测试 Shopify 连接">
          <a onClick={() => handleTest(record)}>
            <LinkOutlined /> 测试
          </a>
        </Tooltip>,
        <Tooltip key="sync" title="同步该店铺的订单">
          <a onClick={() => handleSync(record)}>
            <SyncOutlined /> 同步
          </a>
        </Tooltip>,
        <a
          key="delete"
          onClick={() => handleDelete(record)}
          className="table-action"
          style={{ color: '#ff4d4f' }}
        >
          <DeleteOutlined /> 删除
        </a>,
      ],
    },
  ];

  return (
    <PageContainer
      header={{
        title: '店铺管理',
        subTitle: '维护店铺与 Shopify 凭据',
      }}
    >
      <ProTable<ShopItem>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        search={{ labelWidth: 'auto' }}
        pagination={persistedPagination('shops', 10)}
        scroll={{ x: 1510 }}
        request={async () => {
          const res = await listShops({ include_inactive: true });
          return { data: res.data || [], success: res.success };
        }}
        toolBarRender={() => [
          <Button
            key="add"
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreate}
          >
            新增店铺
          </Button>,
        ]}
      />

      <Modal
        title={editing ? `编辑店铺：${editing.name}` : '新增店铺'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={handleSubmit}
        confirmLoading={submitting}
        width={640}
        destroyOnClose
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item
            name="name"
            label="店铺名称"
            rules={[{ required: true, message: '请输入店铺名称' }]}
          >
            <Input placeholder="例如：美国主店" />
          </Form.Item>
          <Form.Item
            name="shop_url"
            label="店铺域名"
            rules={[{ required: true, message: '请输入店铺域名' }]}
            extra="填写 xxx.myshopify.com 或店铺前缀"
          >
            <Input placeholder="efb409-00.myshopify.com" />
          </Form.Item>
          <Form.Item name="currency" label="结算币种">
            <Select options={CURRENCIES.map((c) => ({ value: c, label: c }))} />
          </Form.Item>
          <Divider orientation="left" plain style={{ marginTop: 8 }}>
            应用凭据（唯一绑定方式）
          </Divider>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="域名 + Client ID + Secret 绑定"
          />
          <Form.Item
            name="oauth_client_id"
            label="Client ID"
            rules={[{ required: true, message: '请输入 Client ID' }]}
            extra="应用配置页可查看"
          >
            <Input placeholder="例如 d5a315b0a833e37f798d96666380e765" />
          </Form.Item>
          <Form.Item
            name="oauth_client_secret"
            label="Client Secret"
            rules={
              editing ? [] : [{ required: true, message: '请输入 Client Secret' }]
            }
            extra="留空表示不修改"
          >
            <Input.Password
              placeholder={editing ? '留空表示不修改' : 'shpss_...'}
            />
          </Form.Item>
          {editing && <>
            <Divider orientation="left" plain>4PX 连接（暂不自动预报）</Divider>
            <Alert type="warning" showIcon style={{ marginBottom: 16 }}
              message="仅保存凭据，不自动预报或出库"
              description="密钥必须成对填写" />
            <Form.Item name="fourpx_app_name" label="4PX App 名称">
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item name="fourpx_resource_code" label="资源编码">
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item name="fourpx_environment" label="4PX 环境">
              <Select options={[{ value: 'test', label: '测试环境' }, { value: 'production', label: '正式环境' }]} />
            </Form.Item>
            <Form.Item name="fourpx_app_key" label="4PX AppKey">
              <Input.Password autoComplete="new-password" placeholder="留空表示不修改" />
            </Form.Item>
            <Form.Item name="fourpx_app_secret" label="4PX AppSecret">
              <Input.Password autoComplete="new-password" placeholder="留空表示不修改" />
            </Form.Item>
          </>}
          <Form.Item name="notes" label="备注">
            <TextArea rows={2} placeholder="可选" />
          </Form.Item>
          <Form.Item
            name="is_active"
            label="启用该店铺"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </PageContainer>
  );
};

export default Shops;
