import {
  DeleteOutlined,
  DownOutlined,
  EditOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  ShopOutlined,
} from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import {
  Alert,
  Button,
  Checkbox,
  Divider,
  Drawer,
  Dropdown,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { TagList } from '@/components';
import { getErrorMessage, isValidationError } from '@/utils/error';
import { listShops, type ShopItem } from '@/services/shops/api';
import {
  assignUserPermissions,
  assignUserShops,
  createUser,
  deleteUser,
  listPermissions,
  listUsers,
  updateUser,
  type ManagedUser,
  type PermissionItem,
  type UserPayload,
} from '@/services/users/api';
import { formatBeijingTime } from '@/utils/timezone';
import { persistedPagination } from '@/utils/pagination';

const SubAccounts: React.FC = () => {
  const actionRef = useRef<ActionType>(null);
  const [form] = Form.useForm<UserPayload>();
  const [assignForm] = Form.useForm<{ shop_ids: number[] }>();
  const [permForm] = Form.useForm<{ permissions: string[] }>();
  const [permissions, setPermissions] = useState<PermissionItem[]>([]);
  const [permVisible, setPermVisible] = useState(false);
  const [permissioning, setPermissioning] = useState<ManagedUser | null>(null);
  const [permDetailUser, setPermDetailUser] = useState<ManagedUser | null>(null);
  const [shops, setShops] = useState<ShopItem[]>([]);
  const [modalVisible, setModalVisible] = useState(false);
  const [assignVisible, setAssignVisible] = useState(false);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [assigning, setAssigning] = useState<ManagedUser | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadShops = async () => {
    try {
      const res = await listShops({ include_inactive: true });
      setShops(res.data || []);
    } catch {
      // 店铺列表加载失败时不影响账号列表展示
    }
  };

  const loadPermissions = async () => {
    try {
      const res = await listPermissions();
      setPermissions(res.data?.permissions || []);
    } catch {
      // 权限清单加载失败不影响账号列表展示
    }
  };

  useEffect(() => {
    loadShops();
    loadPermissions();
  }, []);

  const shopOptions = shops.map((shop) => ({
    value: shop.id,
    label: `${shop.name}${shop.is_active ? '' : '（已停用）'}`,
  }));

  const permissionLabel = (key: string) =>
    permissions.find((item) => item.key === key)?.label || key;

  const grantablePermissions = useMemo(
    () => permissions.filter((item) => !item.admin_only),
    [permissions],
  );
  const adminOnlyPermissions = useMemo(
    () => permissions.filter((item) => item.admin_only),
    [permissions],
  );
  const permissionGroups = useMemo(() => {
    const groups: Array<{ name: string; items: PermissionItem[] }> = [];
    permissions.filter((item) => !item.admin_only).forEach((item) => {
      const name = item.group || '其他';
      const group = groups.find((entry) => entry.name === name);
      if (group) {
        group.items.push(item);
      } else {
        groups.push({ name, items: [item] });
      }
    });
    return groups;
  }, [permissions]);

  const watchedPermissions = Form.useWatch('permissions', permForm) || [];
  const missingDependencies = grantablePermissions.filter(
    (item) => watchedPermissions.includes(item.key)
      && (item.requires || []).some((key) => !watchedPermissions.includes(key)),
  );

  const permissionCheckboxGroup = (
    <Checkbox.Group style={{ display: 'block', width: '100%' }}>
      {permissionGroups.map((group) => (
        <div key={group.name} style={{ marginBottom: 12 }}>
          <Typography.Text strong>{group.name}</Typography.Text>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
            {group.items.map((item) => (
              <Checkbox key={item.key} value={item.key} style={{ alignItems: 'flex-start' }}>
                <Space direction="vertical" size={0}>
                  <Space size={6} wrap>
                    <span>{item.label}</span>
                    {(item.requires || []).length > 0 && (
                      <Tag color="blue">需同时勾选 {item.requires!.map(permissionLabel).join('、')}</Tag>
                    )}
                  </Space>
                  {item.description && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {item.description}
                    </Typography.Text>
                  )}
                </Space>
              </Checkbox>
            ))}
          </div>
        </div>
      ))}
    </Checkbox.Group>
  );

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ is_active: true, shop_ids: [] });
    setModalVisible(true);
  };

  const openEdit = (record: ManagedUser) => {
    setEditing(record);
    form.setFieldsValue({
      display_name: record.display_name,
      email: record.email,
      is_active: record.is_active,
      shop_ids: record.shop_ids || [],
      password: undefined,
    });
    setModalVisible(true);
  };

  const openAssign = (record: ManagedUser) => {
    setAssigning(record);
    assignForm.setFieldsValue({ shop_ids: record.shop_ids || [] });
    setAssignVisible(true);
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      if (editing) {
        await updateUser(editing.id, values);
        message.success('子账户更新成功');
      } else {
        await createUser(values);
        message.success('子账户创建成功');
      }
      setModalVisible(false);
      actionRef.current?.reload();
    } catch (error) {
      if (isValidationError(error)) {
        return;
      }
      message.error(getErrorMessage(error, '保存失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const openPermissions = (record: ManagedUser) => {
    setPermissioning(record);
    permForm.setFieldsValue({ permissions: record.permissions || [] });
    setPermVisible(true);
  };

  const handlePermissions = async () => {
    if (!permissioning) return;
    try {
      const values = await permForm.validateFields();
      setSubmitting(true);
      await assignUserPermissions(permissioning.id, values.permissions || []);
      message.success('功能权限已更新，该账号下次请求立即生效');
      setPermVisible(false);
      actionRef.current?.reload();
    } catch (error) {
      if (isValidationError(error)) return;
      message.error(getErrorMessage(error, '设置失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleAssign = async () => {
    if (!assigning) return;
    try {
      const values = await assignForm.validateFields();
      setSubmitting(true);
      await assignUserShops(assigning.id, values.shop_ids || []);
      message.success('店铺分配成功，该账号下次请求立即生效');
      setAssignVisible(false);
      actionRef.current?.reload();
    } catch (error) {
      if (isValidationError(error)) {
        return;
      }
      message.error(getErrorMessage(error, '分配失败'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = (record: ManagedUser) => {
    Modal.confirm({
      title: '确认删除子账户',
      content: `确定要删除「${record.display_name || record.username}」吗？删除后该账号将无法登录。`,
      okText: '确认删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteUser(record.id);
          message.success('子账户已删除');
          actionRef.current?.reload();
        } catch (error) {
          message.error(getErrorMessage(error, '删除失败'));
        }
      },
    });
  };

  const columns: ProColumns<ManagedUser>[] = [
    {
      title: '用户名',
      dataIndex: 'username',
      render: (_, record) => (
        <Space>
          <span>{record.username}</span>
          {record.role === 'admin' && <Tag color="gold">管理员</Tag>}
        </Space>
      ),
    },
    {
      title: '显示名',
      dataIndex: 'display_name',
      search: false,
    },
    {
      title: '已分配店铺',
      dataIndex: 'shops',
      search: false,
      width: 200,
      render: (_, record) => {
        if (record.role === 'admin') {
          return <Tag color="gold">全部店铺</Tag>;
        }
        if (!record.shops || record.shops.length === 0) {
          return <Tag color="orange">未分配</Tag>;
        }
        return <TagList items={record.shops.map((shop) => shop.name)} color="blue" max={2} />;
      },
    },
    {
      title: '功能权限',
      dataIndex: 'permissions',
      search: false,
      width: 300,
      render: (_, record) => {
        if (record.role === 'admin') {
          return <Tag color="gold">全部权限</Tag>;
        }
        const keys = record.permissions || [];
        if (keys.length === 0) {
          return <Tag color="red">未分配</Tag>;
        }
        return (
          <Space direction="vertical" size={2}>
            <Space size={[4, 4]} wrap>
              {keys.slice(0, 3).map((key) => {
                const item = permissions.find((entry) => entry.key === key);
                return (
                  <Tooltip key={key} title={item?.description || ''}>
                    <Tag color="geekblue">{item?.label || key}</Tag>
                  </Tooltip>
                );
              })}
              {keys.length > 3 && <Tag>+{keys.length - 3}</Tag>}
            </Space>
            <Button size="small" type="link" style={{ padding: 0 }}
              onClick={() => setPermDetailUser(record)}>
              查看全部（{keys.length} 项）
            </Button>
          </Space>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      width: 90,
      valueEnum: {
        true: { text: '启用', status: 'Success' },
        false: { text: '停用', status: 'Default' },
      },
    },
    {
      title: '最后登录',
      dataIndex: 'last_login_at',
      search: false,
      width: 180,
      render: (_, record) =>
        record.last_login_at ? formatBeijingTime(record.last_login_at) : '-',
    },
    {
      title: '操作',
      valueType: 'option',
      width: 168,
      fixed: 'right',
      render: (_, record) => [
        <a key="edit" onClick={() => openEdit(record)} className="table-action">
          <EditOutlined /> 编辑
        </a>,
        <Dropdown
          key="more"
          menu={{
            items: [
              {
                key: 'assign',
                icon: <ShopOutlined />,
                label: '分配店铺',
                onClick: () => openAssign(record),
              },
              {
                key: 'permissions',
                icon: <SafetyCertificateOutlined />,
                label: '分配权限',
                onClick: () => openPermissions(record),
              },
            ],
          }}
        >
          <a className="table-action" onClick={(e) => e.preventDefault()}>
            更多 <DownOutlined />
          </a>
        </Dropdown>,
        record.role !== 'admin' ? (
          <a
            key="delete"
            onClick={() => handleDelete(record)}
            className="table-action"
            style={{ color: '#ff4d4f' }}
          >
            <DeleteOutlined /> 删除
          </a>
        ) : null,
      ],
    },
  ];

  return (
    <PageContainer
      header={{
        title: '员工账号',
        subTitle: '给同事开登录账号，分配可见店铺与功能模块（可同时分配多个店铺）',
      }}
    >
      <ProTable<ManagedUser>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        search={{ labelWidth: 'auto' }}
        pagination={persistedPagination('sub-accounts', 10)}
        request={async (params) => {
          const res = await listUsers({ keyword: params.username as string });
          return { data: res.data || [], success: res.success };
        }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增子账户
          </Button>,
        ]}
      />

      <Modal
        title={editing ? `编辑子账户：${editing.username}` : '新增子账户'}
        open={modalVisible}
        onCancel={() => setModalVisible(false)}
        onOk={handleSubmit}
        confirmLoading={submitting}
        width={560}
        destroyOnClose
      >
        <Form form={form} layout="vertical" preserve={false}>
          {!editing && (
            <Form.Item
              name="username"
              label="登录用户名"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input placeholder="例如：zhangsan" autoComplete="off" />
            </Form.Item>
          )}
          <Form.Item
            name="password"
            label={editing ? '重置密码（留空表示不修改）' : '登录密码'}
            rules={
              editing ? [] : [{ required: true, message: '请输入密码' }]
            }
            extra="密码至少 8 位"
          >
            <Input.Password placeholder="至少 8 位" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input placeholder="例如：张三" />
          </Form.Item>
          <Form.Item name="email" label="邮箱">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item
            name="shop_ids"
            label="分配店铺（可多选）"
            extra="不分配店铺则该账号看不到任何店铺数据"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="选择该子账户可以管理的店铺"
              options={shopOptions}
            />
          </Form.Item>
          <Form.Item name="is_active" label="启用该账号" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item
            name="permissions"
            label="功能权限（按模块勾选，悬停可看覆盖范围）"
            extra="不勾选任何模块时，该账号登录后看不到任何菜单，接口也会拒绝访问"
          >
            {permissionCheckboxGroup}
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={
          assigning
            ? `分配店铺：${assigning.display_name || assigning.username}`
            : '分配店铺'
        }
        open={assignVisible}
        onCancel={() => setAssignVisible(false)}
        onOk={handleAssign}
        confirmLoading={submitting}
        destroyOnClose
      >
        <Form form={assignForm} layout="vertical" preserve={false}>
          <Form.Item
            name="shop_ids"
            label="可管理的店铺（支持多选）"
            extra="保存后立即生效，该账号无需重新登录"
          >
            <Select
              mode="multiple"
              allowClear
              placeholder="选择一个或多个店铺"
              options={shopOptions}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={
          permissioning
            ? `分配功能权限：${permissioning.display_name || permissioning.username}`
            : '分配功能权限'
        }
        open={permVisible}
        onCancel={() => setPermVisible(false)}
        onOk={handlePermissions}
        confirmLoading={submitting}
        destroyOnClose
        width={560}
      >
        <Form form={permForm} layout="vertical" preserve={false}>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={`已选 ${watchedPermissions.length} 项；保存后立即生效，未勾选的菜单不可见、接口返回 403`}
          />
          <Space wrap size={8} style={{ marginBottom: 12 }}>
            <Button size="small" onClick={() => permForm.setFieldsValue({
              permissions: grantablePermissions.map((item) => item.key),
            })}>全选可分配</Button>
            <Button size="small" onClick={() => permForm.setFieldsValue({ permissions: [] })}>清空</Button>
          </Space>
          <Form.Item
            name="permissions"
            label="该账号可以访问的模块"
            extra="每项下方注明覆盖的功能；保存后立即生效（无需重新登录）"
          >
            {permissionCheckboxGroup}
          </Form.Item>
          {missingDependencies.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message="还有依赖权限没勾选"
              description={missingDependencies.map((item) => (
                <div key={item.key}>
                  {item.label}：还需要勾选 {(item.requires || []).map(permissionLabel).join('、')}
                </div>
              ))}
            />
          )}
          {adminOnlyPermissions.length > 0 && (
            <Tooltip title="这些属于管理员专属能力，不能授予子账户">
              <div style={{ color: '#999', fontSize: 12 }}>
                管理员专属（不可分配）：
                {adminOnlyPermissions.map((item) => item.label).join('、')}
              </div>
            </Tooltip>
          )}
        </Form>
      </Modal>

      <Drawer
        title={permDetailUser
          ? `已分配权限：${permDetailUser.display_name || permDetailUser.username}`
          : '已分配权限'}
        width={520}
        open={Boolean(permDetailUser)}
        onClose={() => setPermDetailUser(null)}
      >
        {permDetailUser && (() => {
          const assigned = new Set(permDetailUser.permissions || []);
          const assignedCount = grantablePermissions.filter((item) => assigned.has(item.key)).length;
          return (
            <Space direction="vertical" size={14} style={{ width: '100%' }}>
              <Alert
                type="info"
                showIcon
                message={`可分配权限已授予 ${assignedCount} / ${grantablePermissions.length} 项`}
                description={assignedCount === 0 ? '这个账号目前没有任何功能权限，登录后看不到菜单' : undefined}
              />
              {permissionGroups.map((group) => (
                <div key={group.name}>
                  <Typography.Text strong>{group.name}</Typography.Text>
                  <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {group.items.map((item) => (
                      <div key={item.key}>
                        <Space size={6} wrap>
                          <Tag color={assigned.has(item.key) ? 'green' : 'default'}>
                            {assigned.has(item.key) ? '已分配' : '未分配'}
                          </Tag>
                          <span>{item.label}</span>
                          {(item.requires || []).length > 0 && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              需同时有 {item.requires!.map(permissionLabel).join('、')}
                            </Typography.Text>
                          )}
                        </Space>
                        {item.description && (
                          <Typography.Paragraph type="secondary" style={{ fontSize: 12, margin: '2px 0 0' }}>
                            {item.description}
                          </Typography.Paragraph>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {adminOnlyPermissions.length > 0 && (
                <>
                  <Divider style={{ margin: '4px 0' }} />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    管理员专属（子账户不可分配）：{adminOnlyPermissions.map((item) => item.label).join('、')}
                  </Typography.Text>
                </>
              )}
            </Space>
          );
        })()}
      </Drawer>
    </PageContainer>
  );
};

export default SubAccounts;
