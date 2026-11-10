import { PageContainer } from '@ant-design/pro-components';
import { Alert, Button, Card, Drawer, Form, Image, Input, message, Segmented, Space, Table, Tag, Typography } from 'antd';
import React, { useEffect, useRef, useState } from 'react';
import { ShopRequiredNotice } from '@/components';
import { listManagedProducts, saveProductNames, type ManagedProduct } from '@/services/products/api';
import { getErrorMessage } from '@/utils/error';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';

const Products: React.FC = () => {
  const selected = getSelectedShopId();
  const shopId = selected === ALL_SHOPS ? null : Number(selected);
  const [rows, setRows] = useState<ManagedProduct[]>([]);
  const [search, setSearch] = useState('');
  const [mapped, setMapped] = useState('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<ManagedProduct | null>(null);
  const [revision, setRevision] = useState(0);
  const [form] = Form.useForm<{ chinese_name: string; colors: { chinese_name: string }[] }>();
  const sequence = useRef(0);

  useEffect(() => {
    setPage(1);
    setEditing(null);
  }, [shopId]);

  useEffect(() => {
    const seq = ++sequence.current;
    if (!shopId) { setRows([]); setTotal(0); setLoading(false); return; }
    setLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const response = await listManagedProducts({ shop_id: shopId, page, per_page: pageSize, search, mapped });
        if (sequence.current !== seq) return;
        setRows(response.data.items);
        setTotal(response.data.pagination.total);
      } catch (error) {
        if (sequence.current === seq) message.error(getErrorMessage(error, '商品加载失败'));
      } finally {
        if (sequence.current === seq) setLoading(false);
      }
    }, 250);
    return () => { window.clearTimeout(timer); sequence.current += 1; };
  }, [shopId, page, pageSize, search, mapped, revision]);

  const edit = (row: ManagedProduct) => {
    setEditing(row);
    form.resetFields();
    form.setFieldsValue({ chinese_name: row.chinese_name, colors: row.colors.map((color) => ({ chinese_name: color.chinese_name })) });
  };
  const save = async () => {
    if (!editing || saving || editing.shop_id !== shopId) return;
    try {
      const values = await form.validateFields();
      setSaving(true);
      await saveProductNames({ shop_id: editing.shop_id, shopify_product_id: editing.shopify_product_id, original_title: editing.original_title,
        chinese_name: values.chinese_name || '', colors: editing.colors.map((color, index) => ({
          original_color: color.original_color, chinese_name: values.colors?.[index]?.chinese_name || '',
        })) });
      message.success('中文映射已保存，后续查询和导出会使用新名称');
      setEditing(null);
      setRevision((value) => value + 1);
    } catch (error) {
      if (!(error && typeof error === 'object' && 'errorFields' in error)) message.error(getErrorMessage(error, '保存失败'));
    } finally { setSaving(false); }
  };

  return <PageContainer title="商品管理" subTitle="自定义商品与颜色中文名">
    {!shopId ? <ShopRequiredNotice reason="中文名称按店铺独立管理，请先选择具体店铺。" /> : <>
      <Alert showIcon type="info" message="填写后优先显示中文，留空恢复英文" style={{ marginBottom: 16 }} />
      <Card>
        <Space wrap style={{ marginBottom: 16 }}>
          <Input allowClear placeholder="搜索英文、中文、颜色或型号" value={search}
            onChange={(event) => { setSearch(event.target.value); setPage(1); }} style={{ width: 300, maxWidth: '100%' }} />
          <Segmented options={[{ label: '全部商品', value: 'all' }, { label: '已填写中文', value: 'yes' }]}
            value={mapped} onChange={(value) => { setMapped(String(value)); setPage(1); }} />
          <Button onClick={() => setRevision((value) => value + 1)}>刷新</Button>
        </Space>
        <Table<ManagedProduct> rowKey={(row) => row.shopify_product_id || row.original_title} loading={loading} dataSource={rows} scroll={{ x: 850 }}
          pagination={{ current: page, pageSize, total, showSizeChanger: true, showTotal: (value) => `共 ${value} 款商品`,
            onChange: (next, size) => { setPage(size === pageSize ? next : 1); setPageSize(size); } }}
          columns={[
            { title: '商品', dataIndex: 'original_title', width: 380, render: (_, row) => <Space align="start">
              {row.image_url ? <Image src={row.image_url} width={56} height={56} style={{ objectFit: 'contain' }} /> : <span style={{ width: 56, height: 56, flexShrink: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5', fontSize: 12, color: '#999' }}>暂无图片</span>}
              <div><Typography.Text strong>{row.display_title}</Typography.Text>{row.chinese_name && <div><Typography.Text type="secondary">{row.original_title}</Typography.Text></div>}</div>
            </Space> },
            { title: '颜色映射', width: 300, render: (_, row) => <Space wrap>{row.colors.map((color) =>
              <Tag key={color.original_color} color={color.chinese_name ? 'blue' : undefined}>{color.original_color}{color.chinese_name ? ` → ${color.chinese_name}` : ''}</Tag>)}</Space> },
            { title: '变体数', dataIndex: 'variant_count', width: 80 },
            { title: '操作', width: 110, render: (_, row) => <Button type="link" onClick={() => edit(row)}>编辑中文</Button> },
          ]} />
      </Card>
    </>}
    <Drawer title="编辑商品中文映射" open={Boolean(editing)} width="min(680px, 100vw)" onClose={() => { if (!saving) setEditing(null); }}
      extra={<Button type="primary" loading={saving} onClick={() => void save()}>保存</Button>}>
      {editing && <Form form={form} layout="vertical">
        <Typography.Paragraph type="secondary">原商品：{editing.original_title}</Typography.Paragraph>
        <Form.Item name="chinese_name" label="商品中文名称" rules={[{ max: 255 }]}><Input maxLength={255} allowClear placeholder="留空保持英文名称" /></Form.Item>
        <Typography.Title level={5}>颜色中文名称</Typography.Title>
        {editing.colors.map((color, index) => <div key={color.original_color} style={{ display: 'flex', gap: 12, marginBottom: 12, alignItems: 'start' }}>
          {color.image_url && <Image src={color.image_url} width={56} height={56} style={{ objectFit: 'contain' }} />}
          <Form.Item name={['colors', index, 'chinese_name']} label={color.original_color} rules={[{ max: 255 }]} style={{ flex: 1, minWidth: 0, marginBottom: 0 }}>
            <Input maxLength={255} allowClear placeholder="留空保持英文颜色" />
          </Form.Item>
        </div>)}
        {!editing.colors.length && <Typography.Text type="secondary">该商品暂未识别到独立颜色选项。</Typography.Text>}
      </Form>}
    </Drawer>
  </PageContainer>;
};

export default Products;
