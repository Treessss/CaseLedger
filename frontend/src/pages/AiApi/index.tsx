import { CopyOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { Alert, Button, Card, Form, Input, Modal, Popover, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import React, { useCallback, useEffect, useState } from 'react';
import {
  createAiApiKey,
  listAiApiAudit,
  listAiApiKeys,
  revokeAiApiKey,
  rotateAiApiKey,
  updateAiApiKey,
  type AiApiAuditRow,
  type AiApiKeyItem,
  type CreatedAiApiKey,
} from '@/services/aiApi';
import { listShops, type ShopItem } from '@/services/shops/api';
import { getErrorMessage } from '@/utils/error';
import { formatBeijingTime } from '@/utils/timezone';

const BASE_URL = 'https://192.3.236.57:8443';

const scopeShort: Record<string, string> = {
  mail_read: '邮件读', mail_send: '邮件写', orders_read: '订单',
  logistics_read: '物流', procurement_read: '采购', inventory_read: '库存',
  after_sales_read: '售后', after_sales_write: '售后写', purchase_draft: '采购草稿',
};

const AiApi: React.FC = () => {
  const [form] = Form.useForm();
  const [keys, setKeys] = useState<AiApiKeyItem[]>([]);
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const [shops, setShops] = useState<ShopItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<CreatedAiApiKey | null>(null);
  const [audit, setAudit] = useState<AiApiAuditRow[]>([]);
  const [editForm] = Form.useForm();
  const [editing, setEditing] = useState<AiApiKeyItem | null>(null);
  const [editOpen, setEditOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [keyRes, shopRes, auditRes] = await Promise.all([
        listAiApiKeys(),
        listShops({ include_inactive: true }),
        listAiApiAudit({ page: 1, per_page: 20 }),
      ]);
      setKeys(keyRes.data?.keys || []);
      setScopes(keyRes.data?.scopes || {});
      setShops(shopRes.data || []);
      setAudit(auditRes.data?.items || []);
    } catch (error) {
      message.error(getErrorMessage(error, '加载 API Key 失败'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const submit = async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);
      const response = await createAiApiKey(values);
      setCreated(response.data);
      setOpen(false);
      form.resetFields();
      await load();
    } catch (error) {
      message.error(getErrorMessage(error, '创建失败'));
    } finally {
      setCreating(false);
    }
  };

  const openEdit = (record: AiApiKeyItem) => {
    setEditing(record);
    editForm.setFieldsValue({
      name: record.name,
      shop_ids: record.shop_ids,
      scopes: record.scopes,
      ip_allowlist: record.ip_allowlist || [],
      daily_send_limit: record.daily_send_limit ?? 100,
    });
    setEditOpen(true);
  };

  const submitEdit = async () => {
    if (!editing) return;
    try {
      const values = await editForm.validateFields();
      setCreating(true);
      await updateAiApiKey(editing.id, values);
      message.success('API Key 已更新');
      setEditOpen(false);
      await load();
    } catch (error) {
      message.error(getErrorMessage(error, '更新失败'));
    } finally {
      setCreating(false);
    }
  };

  const rotate = (record: AiApiKeyItem) => {
    Modal.confirm({
      title: `轮换「${record.name}」？`,
      content: '旧 Key 会立即失效，使用它的 AI 需要换成新 Key。',
      okText: '轮换',
      onOk: async () => {
        const response = await rotateAiApiKey(record.id);
        setCreated(response.data);
        await load();
      },
    });
  };

  const revoke = (record: AiApiKeyItem) => {
    Modal.confirm({
      title: `撤销「${record.name}」？`,
      content: '撤销后使用该 Key 的 AI 会立即无法调用接口。',
      okType: 'danger',
      okText: '撤销',
      onOk: async () => {
        await revokeAiApiKey(record.id);
        message.success('已撤销');
        await load();
      },
    });
  };

  const copyKey = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.api_key);
      message.success('已复制 API Key');
    } catch {
      message.warning('复制失败，请手动选择文本复制');
    }
  };

  const shopNames = (ids: number[]) => ids.map((id) => shops.find((shop) => shop.id === id)?.name || `#${id}`).join('、');

  return (
    <PageContainer
      title="AI 接口"
      subTitle="给 AI/自动化工具使用的 API Key（邮件、订单、物流、采购、库存、售后）"
      extra={[
        <Button key="reload" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>,
        <Button key="create" type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); form.setFieldsValue({ scopes: Object.keys(scopes) }); setOpen(true); }}>
          新建 API Key
        </Button>,
      ]}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="AI 调用方式：请求头带 X-API-Key: cai_xxx，接口前缀 /api/ai"
          description={`Base URL：${BASE_URL}；Key 只在创建时显示一次，请立即复制保存；每个 Key 限定了可访问店铺和接口范围，可随时撤销。`}
        />

        <Card
          size="small"
          title="接口能力"
          extra={<Button size="small" onClick={() => window.open('/api/ai/openapi.json', '_blank')}>OpenAPI</Button>}
        >
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <Space wrap size={[4, 4]}>
              <Tag color="blue">客服邮件（读/发/草稿/附件）</Tag>
              <Tag color="cyan">主动发信</Tag>
              <Tag color="geekblue">订单查询</Tag>
              <Tag color="purple">物流轨迹</Tag>
              <Tag color="gold">采购清单</Tag>
              <Tag color="green">库存</Tag>
              <Tag color="orange">售后（查询/发起）</Tag>
            </Space>
            <Typography.Text type="secondary">
              鉴权头 <Typography.Text code>X-API-Key: cai_xxx</Typography.Text>
              {' · '}公共 OpenAPI <Typography.Text code copyable>{`${BASE_URL}/api/ai/openapi.json`}</Typography.Text>
            </Typography.Text>
            <Typography.Text code copyable style={{ display: 'block', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {`curl -H "X-API-Key: cai_xxx" "${BASE_URL}/api/ai/mail/messages?shop_id=1&status=todo&page=1"`}
            </Typography.Text>
          </Space>
        </Card>

        <Table<AiApiKeyItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={keys}
          pagination={false}
          scroll={{ x: 1150 }}
          columns={[
            {
              title: '名称 / Key', dataIndex: 'name', width: 180,
              render: (_, record) => <div>
                <Typography.Text strong>{record.name}</Typography.Text>
                <div style={{ marginTop: 2 }}>
                  <Typography.Text code copyable={{ text: record.key_prefix }} style={{ fontSize: 12 }}>
                    {record.key_prefix}…
                  </Typography.Text>
                </div>
              </div>,
            },
            {
              title: '店铺', dataIndex: 'shop_ids', width: 120,
              render: (ids: number[]) => <Typography.Text ellipsis={{ tooltip: shopNames(ids) }} style={{ maxWidth: 110 }}>{shopNames(ids)}</Typography.Text>,
            },
            {
              title: '范围', dataIndex: 'scopes', width: 320,
              render: (values: string[]) => {
                const shown = values.slice(0, 4);
                return <Space size={[4, 4]} wrap>
                  {shown.map((key) => <Tooltip key={key} title={scopes[key] || key}>
                    <Tag color="blue" style={{ marginInlineEnd: 0 }}>{scopeShort[key] || key}</Tag>
                  </Tooltip>)}
                  {values.length > shown.length && <Popover
                    title="全部范围"
                    content={<Space direction="vertical" size={4}>
                      {values.map((key) => <Tag key={key} color="blue">{scopes[key] || key}</Tag>)}
                    </Space>}
                  >
                    <Tag style={{ cursor: 'pointer', marginInlineEnd: 0 }}>+{values.length - shown.length}</Tag>
                  </Popover>}
                </Space>;
              },
            },
            {
              title: '今日调用 / 发送', width: 120,
              render: (_, record) => <div>
                <Typography.Text>{record.today_calls ?? 0} / {record.today_sends ?? 0}</Typography.Text>
                <div><Typography.Text type="secondary" style={{ fontSize: 12 }}>上限 {record.daily_send_limit ?? 100}</Typography.Text></div>
              </div>,
            },
            {
              title: '最近调用', dataIndex: 'last_used_at', width: 150,
              render: (value?: string) => <Typography.Text type={value ? undefined : 'secondary'} style={{ fontSize: 12 }}>
                {value ? formatBeijingTime(value) : '从未'}
              </Typography.Text>,
            },
            {
              title: '有效期', dataIndex: 'expires_at', width: 110,
              render: (value?: string) => <Typography.Text style={{ fontSize: 12 }} type={value ? undefined : 'secondary'}>
                {value ? formatBeijingTime(value, 'YYYY-MM-DD') : '长期'}
              </Typography.Text>,
            },
            {
              title: '状态', dataIndex: 'is_active', width: 80,
              render: (value: boolean) => (value ? <Tag color="green">启用</Tag> : <Tag>已撤销</Tag>),
            },
            {
              title: '操作', width: 150, fixed: 'right',
              render: (_, record) => record.is_active ? (
                <Space size={0}>
                  <Button size="small" type="link" onClick={() => openEdit(record)}>编辑</Button>
                  <Button size="small" type="link" onClick={() => rotate(record)}>轮换</Button>
                  <Button size="small" danger type="link" onClick={() => revoke(record)}>撤销</Button>
                </Space>
              ) : '—',
            },
          ]}
          locale={{ emptyText: '还没有 API Key' }}
        />

        <Card size="small" title="最近调用（审计）" extra={<Typography.Text type="secondary">最多显示最近 20 条</Typography.Text>}>
          <Table<AiApiAuditRow>
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={audit}
            columns={[
              { title: '时间', dataIndex: 'created_at', width: 170,
                render: (value?: string) => (value ? formatBeijingTime(value) : '—') },
              { title: '方法', dataIndex: 'method', width: 70 },
              { title: '路径', dataIndex: 'path' },
              { title: '状态码', dataIndex: 'status_code', width: 80,
                render: (value: number) => <Tag color={value < 400 ? 'green' : 'red'}>{value}</Tag> },
              { title: '耗时(ms)', dataIndex: 'duration_ms', width: 90 },
              { title: 'IP', dataIndex: 'ip', width: 130 },
            ]}
            locale={{ emptyText: '还没有调用记录' }}
          />
        </Card>
      </Space>

      <Modal
        title="新建 API Key"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
        confirmLoading={creating}
        destroyOnClose
        okText="创建"
      >
        <Form form={form} layout="vertical" preserve={false} initialValues={{ scopes: [] }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="例如：客服 AI 助手" maxLength={100} />
          </Form.Item>
          <Form.Item name="shop_ids" label="可访问店铺" rules={[{ required: true, message: '请选择店铺' }]}>
            <Select mode="multiple" placeholder="选择店铺" options={shops.map((shop) => ({ value: shop.id, label: shop.name }))} />
          </Form.Item>
          <Form.Item name="scopes" label="接口范围" rules={[{ required: true, message: '请选择范围' }]}>
            <Select mode="multiple" placeholder="选择范围" options={Object.entries(scopes).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="ip_allowlist" label="IP 白名单" extra="可选；不填表示不限 IP。填服务器出口 IP 可防止 Key 被复制到别处使用">
            <Select mode="tags" tokenSeparators={[',', ' ']} placeholder="例如 1.2.3.4" />
          </Form.Item>
          <Form.Item name="daily_send_limit" label="每日发送上限" initialValue={100}
            extra="限制该 Key 每天最多发送多少封邮件（1–10000）">
            <Input type="number" min={1} max={10000} />
          </Form.Item>
          <Form.Item name="expires_in_days" label="有效期" extra="留空表示长期有效">
            <Select allowClear placeholder="长期" options={[
              { value: 30, label: '30 天' },
              { value: 90, label: '90 天' },
              { value: 180, label: '180 天' },
              { value: 365, label: '365 天' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`编辑 API Key：${editing?.name || ''}`}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void submitEdit()}
        confirmLoading={creating}
        okText="保存"
        destroyOnClose
      >
        <Form form={editForm} layout="vertical" preserve={false}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请填写名称' }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Form.Item name="shop_ids" label="可访问店铺" rules={[{ required: true, message: '请选择店铺' }]}>
            <Select mode="multiple" options={shops.map((shop) => ({ value: shop.id, label: shop.name }))} />
          </Form.Item>
          <Form.Item name="scopes" label="接口范围" rules={[{ required: true, message: '请选择范围' }]}>
            <Select mode="multiple" options={Object.entries(scopes).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="ip_allowlist" label="IP 白名单">
            <Select mode="tags" tokenSeparators={[',', ' ']} placeholder="留空表示不限 IP" />
          </Form.Item>
          <Form.Item name="daily_send_limit" label="每日发送上限">
            <Input type="number" min={1} max={10000} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="API Key 已创建（只显示这一次）"
        open={Boolean(created)}
        onCancel={() => setCreated(null)}
        footer={<Button onClick={() => setCreated(null)}>我已保存</Button>}
      >
        {created && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert type="warning" showIcon message="关闭后无法再次查看完整 Key，请现在复制保存" />
            <Typography.Text code copyable style={{ wordBreak: 'break-all' }}>{created.api_key}</Typography.Text>
            <Button icon={<CopyOutlined />} onClick={() => void copyKey()}>复制 Key</Button>
            <Typography.Text type="secondary">示例：</Typography.Text>
            <Typography.Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {`curl -H "X-API-Key: ${created.api_key}" "${BASE_URL}/api/ai/mail/messages?shop_id=${created.shop_ids[0]}&status=todo"`}
            </Typography.Text>
          </Space>
        )}
      </Modal>
    </PageContainer>
  );
};

export default AiApi;
