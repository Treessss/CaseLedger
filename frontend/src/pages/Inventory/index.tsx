import { HistoryOutlined, PlusOutlined } from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { Alert, Button, Collapse, Drawer, Dropdown, Form, Grid, Image, Input, InputNumber, message, Modal, Space, Statistic, Table, Tabs, Tag, Typography } from 'antd';
import type { TableColumnsType } from 'antd';
import React, { useEffect, useRef, useState } from 'react';
import {
  createInventoryAdjustment,
  batchReceiveInventory,
  listReceivingProducts,
  listInventory,
  listInventoryAdjustments,
  type InventoryAdjustment,
  type InventoryItem,
  type InventoryProduct,
} from '@/services/inventory';
import { getErrorMessage } from '@/utils/error';
import { persistedPagination } from '@/utils/pagination';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';
import { formatBeijingTime } from '@/utils/timezone';
import { ShopRequiredNotice } from '@/components';
import Movements from './Movements';
import InventoryQuantities from '@/components/InventoryQuantities';
import InventoryDemandOrders from '@/components/InventoryDemandOrders';

const Inventory: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'stock' | 'movements'>('stock');
  const screens = Grid.useBreakpoint();
  const selectedShop = getSelectedShopId();
  const shopId = selectedShop === ALL_SHOPS ? null : Number(selectedShop);
  const actionRef = useRef<ActionType>(null);
  const [summary, setSummary] = useState({ product_count: 0, variant_count: 0, on_hand_quantity: 0, reserved_quantity: 0, available_quantity: 0,
    incoming_quantity: 0, purchase_needed_quantity: 0, ready_quantity: 0, waiting_quantity: 0, packing_quantity: 0, idle_quantity: 0, demand_quantity: 0,
    net_shortage_quantity: 0, structural_gap_quantity: 0 });
  const [selected, setSelected] = useState<InventoryItem | null>(null);
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [receiving, setReceiving] = useState<Record<string, {item: InventoryItem; quantity: number}>>({});
  const [receivingReason, setReceivingReason] = useState('');
  const [receivingQuery, setReceivingQuery] = useState('');
  const [receivingSearch, setReceivingSearch] = useState('');
  useEffect(() => {
    const timer = window.setTimeout(() => setReceivingSearch(receivingQuery.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [receivingQuery]);
  const [receivingBusy, setReceivingBusy] = useState(false);
  const receivingInFlight = useRef(false);
  useEffect(() => {
    setReceiving({});
    setReceivingReason('');
    setReceivingQuery('');
    setReceivingSearch('');
    setAddOpen(false);
  }, [shopId]);
  const submitReceiving = async () => {
    if (!shopId || receivingInFlight.current || !Object.keys(receiving).length) return;
    const entries = Object.values(receiving);
    if (entries.some(({quantity}) => !Number.isInteger(quantity) || quantity <= 0)) {
      message.warning('请为每个变体填写有效的入库数量');
      return;
    }
    receivingInFlight.current = true;
    setReceivingBusy(true);
    try {
      const response = await batchReceiveInventory({shop_id: shopId,
        items: entries.map(({item, quantity}) => ({group_key: item.group_key, quantity})), reason: receivingReason});
      message.success(`已入库 ${entries.length} 个变体，共 ${response.data.quantity} 件；系统已自动分配可用库存`);
      setReceiving({});
      setReceivingReason('');
      setAddOpen(false);
      actionRef.current?.reload();
    } catch (error) {
      message.error(getErrorMessage(error, '批量入库失败，请核对入库流水后重试'));
    } finally { receivingInFlight.current = false; setReceivingBusy(false); }
  };
  const [adjustMode, setAdjustMode] = useState<'add' | 'adjust'>('adjust');
  const [adjustBusy, setAdjustBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [adjustments, setAdjustments] = useState<InventoryAdjustment[]>([]);
  const [form] = Form.useForm<{ quantity_delta: number; reason: string }>();

  const openAdjustment = (item: InventoryItem, mode: 'add' | 'adjust' = 'adjust') => {
    setSelected(item);
    setAdjustMode(mode);
    setAddOpen(false);
    form.resetFields();
    if (mode === 'add') form.setFieldsValue({ quantity_delta: 1, reason: '手动入库' });
    setAdjustOpen(true);
  };

  const submitAdjustment = async () => {
    if (!shopId || !selected) return;
    const values = await form.validateFields();
    setAdjustBusy(true);
    try {
      const response = await createInventoryAdjustment({
        shop_id: shopId,
        group_key: selected.group_key,
        quantity_delta: values.quantity_delta,
        reason: values.reason?.trim() || '',
      });
      const released = response.data?.released_orders || [];
      const allocated = response.data?.allocated_orders || [];
      if (released.length) {
        // 扣减吃掉了订单占用的库存：这些订单变回缺货，需要重新采购
        message.warning(
          `库存已调整。占用的库存被扣减，订单 ${released
            .map((item) => item.order_number || item.order_id)
            .join('、')} 已回到待采购`,
          6,
        );
      } else if (allocated.length) {
        // 入库后自动备给了缺货订单，不需要再去采购
        const summary = Array.from(
          new Set(allocated.map((item) => item.order_number || item.order_id)),
        ).join('、');
        message.success(`库存数量已更新，相关需求订单：${summary}；实际扫码时扣减`, 6);
      } else {
        message.success('库存调整已记录');
      }
      setAdjustOpen(false);
      actionRef.current?.reload();
    } catch (error) {
      message.error(getErrorMessage(error, '库存调整失败'));
    } finally {
      setAdjustBusy(false);
    }
  };

  const openHistory = async (item: InventoryItem) => {
    if (!shopId) return;
    setSelected(item);
    setHistoryOpen(true);
    try {
      const response = await listInventoryAdjustments(shopId, item.group_key);
      setAdjustments(response.data);
    } catch (error) {
      message.error(getErrorMessage(error, '获取库存调整记录失败'));
    }
  };

  const merged = (row: InventoryItem) => (row.components?.length || 0) > 1;
  const componentLabel = (part: NonNullable<InventoryItem['components']>[number]) =>
    `${part.configuration ? part.configuration.replace(/^\+\s*/, '') : '普通款'}${part.sku ? ` · ${part.sku}` : ''}`;
  const componentRow = (row: InventoryItem, part: NonNullable<InventoryItem['components']>[number]) => ({
    ...row, group_key: part.group_key, sku: part.sku || row.sku, configuration: part.configuration,
  });
  const componentMenu = (row: InventoryItem, mode: 'adjust' | 'history') => ({
    items: (row.components || []).map((part) => ({
      key: `${mode}:${part.group_key}`,
      label: componentLabel(part),
      onClick: () => (mode === 'adjust' ? openAdjustment(componentRow(row, part)) : void openHistory(componentRow(row, part))),
    })),
  });
  const renderActions = (row: InventoryItem) => <Space size={0}>
    {merged(row) ? <Dropdown menu={componentMenu(row, 'adjust')} trigger={['click']}>
      <Button type="link" icon={<PlusOutlined />}>调整 ▾</Button>
    </Dropdown> : <Button type="link" icon={<PlusOutlined />} onClick={() => openAdjustment(row)}>调整</Button>}
    {merged(row) ? <Dropdown menu={componentMenu(row, 'history')} trigger={['click']}>
      <Button type="link" icon={<HistoryOutlined />}>记录 ▾</Button>
    </Dropdown> : <Button type="link" icon={<HistoryOutlined />} onClick={() => openHistory(row)}>记录</Button>}
  </Space>;

  const productCell = (title: string, imageUrl: string, alt: string) => <Space align="start">
    {imageUrl ? <Image src={imageUrl} alt={alt} width={56} height={56} style={{ objectFit: 'cover', borderRadius: 6 }} />
      : <div className="inventory-image-placeholder">无图</div>}
    <Typography.Text strong>{title}</Typography.Text>
  </Space>;


  const productColumns: ProColumns<InventoryProduct>[] = [
    { title: '搜索', dataIndex: 'search', hideInTable: true, fieldProps: { placeholder: '商品、颜色、型号或 SKU' } },
    { title: '商品大类', dataIndex: 'title', hideInSearch: true, width: screens.md ? '40%' : undefined,
      render: (_, row) => <div>{productCell(row.title, row.image_url, row.title)}<Typography.Text type="secondary">{row.color_count} 个颜色 · {row.variant_count} 个变体</Typography.Text></div> },
    { title: '库存 / 采购分布（件）', key: 'pool', hideInSearch: true,
      render: (_, row) => <><InventoryQuantities data={row} /><InventoryDemandOrders key={`${shopId}:${row.group_key}`}
        title={row.title} variants={row.colors.flatMap((color) => color.variants)} /></> },
  ];

  const variantColumns: TableColumnsType<InventoryItem> = [
    { title: '型号 / SKU', dataIndex: 'model', width: screens.md ? '28%' : undefined,
      render: (_, row) => <div className="inventory-product-text">
        <div><Typography.Text strong>{row.model}</Typography.Text>
          {row.configuration && <Tag color="geekblue" style={{ marginLeft: 6 }}>{row.configuration.replace(/^\+\s*/, '')}</Tag>}
        </div>
        {row.sku && <Typography.Text type="secondary">SKU：{row.sku}</Typography.Text>}</div> },
    { title: '库存 / 采购分布', key: 'pool', render: (_, row) => <><InventoryQuantities data={row} />
      <InventoryDemandOrders key={`${shopId}:${row.group_key}`} title={`${row.title} / ${row.color} / ${row.model}`} variants={[row]} /></> },
    { title: '操作', key: 'actions', width: 110, render: (_, row) => renderActions(row) },
  ];

  const pickerColumns: ProColumns<InventoryProduct>[] = [
    { title: '搜索', dataIndex: 'search', hideInTable: true, fieldProps: { placeholder: '商品、颜色、型号或 SKU' } },
    {
      title: '商品大类', dataIndex: 'title', hideInSearch: true,
      render: (_, row) => productCell(row.title, row.image_url, row.title),
    },
    { title: '颜色', dataIndex: 'color_count', hideInSearch: true, width: 80, align: 'right' },
    { title: '型号', dataIndex: 'variant_count', hideInSearch: true, width: 80, align: 'right' },
  ];

  const pickerColors = (product: InventoryProduct) => <Collapse
    className="inventory-picker-colors"
    items={product.colors.map((color) => ({
      key: color.original_name || color.name,
      label: <div className="inventory-picker-color-label">
        {color.image_url ? <Image src={color.image_url} alt={color.name} width={48} height={48} preview={false} style={{ objectFit: 'cover', borderRadius: 6 }} />
          : <div className="inventory-image-placeholder">无图</div>}
        <Typography.Text strong>{color.name}</Typography.Text>
        <Tag>{color.variant_count} 个型号</Tag>
      </div>,
      children: <div className="inventory-picker-models">
        {color.variants.map((variant) => <div className="inventory-picker-model" key={variant.group_key}>
          <div className="inventory-product-text">
            <div><Typography.Text strong>{variant.model}</Typography.Text>
              {variant.configuration && <Tag color="geekblue" style={{ marginLeft: 6 }}>{variant.configuration.replace(/^\+\s*/, '')}</Tag>}
            </div>
            {variant.sku && <Typography.Text type="secondary">SKU：{variant.sku}</Typography.Text>}
          </div>
          <Button type="primary" disabled={receivingBusy || Boolean(receiving[variant.group_key]) || Object.keys(receiving).length >= 100} onClick={() => setReceiving((prev) => ({...prev, [variant.group_key]: {item: variant, quantity: 1}}))}>
            {receiving[variant.group_key] ? '已加入' : '加入本批'}
          </Button>
        </div>)}
      </div>,
    }))}
  />;

  return <PageContainer title="库存管理" subTitle="公共库存，扫码时按实物扣减">
    {!shopId ? <ShopRequiredNotice reason="库存按店铺隔离，请先选择店铺。" /> : <Tabs
      activeKey={activeTab}
      onChange={(key) => setActiveTab(key as 'stock' | 'movements')}
      items={[
        {
          key: 'stock',
          label: '库存总览',
          children: <>
      <div className="inventory-summary">
        <Statistic title="商品大类" value={summary.product_count} />
        <Statistic title="有效变体数" value={summary.variant_count} />
        <Statistic title="在库件数" value={summary.on_hand_quantity} />
        <Statistic title="已采购未到" value={summary.incoming_quantity} />
        <Statistic title="按型号还差" value={summary.purchase_needed_quantity} />
        <Statistic title="未出库需求" value={summary.demand_quantity} />
      </div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`库存不绑定订单：总需求 ${summary.demand_quantity} 件 − 在库 ${summary.on_hand_quantity} − 已采购未到 ${summary.incoming_quantity} = 总量差 ${summary.net_shortage_quantity} 件；按颜色/型号分别算，还需采购 ${summary.purchase_needed_quantity} 件。`}
        description="“按型号还差”不能跨颜色/型号互抵。"
      />
      <ProTable<InventoryProduct>
        rowKey="group_key"
        actionRef={actionRef}
        columns={productColumns}
        toolBarRender={() => [<Button key="add" type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>手动入库</Button>]}
        expandable={{ expandRowByClick: true, expandedRowRender: (product) => <div className="inventory-product-groups">
          {product.colors.map((color) => <div className="inventory-color-group" key={color.original_name || color.name}>
            <div className="inventory-color-group__header">
              {color.image_url ? <Image src={color.image_url} alt={color.name} width={64} height={64} style={{ objectFit: 'cover', borderRadius: 6 }} />
                : <div className="inventory-image-placeholder">无图</div>}
              <div><Typography.Text strong>{color.name}</Typography.Text>
                <div className="inventory-color-group__meta">
                  <Typography.Text type="secondary">{color.variant_count} 个型号 · 在库 {color.on_hand_quantity} 件</Typography.Text>
                  <InventoryQuantities data={color} />
                  <InventoryDemandOrders key={`${shopId}:${product.group_key}:${color.original_name || color.name}`}
                    title={`${product.title} / ${color.name}`} variants={color.variants} />
                </div></div>
            </div>
            <Table<InventoryItem> rowKey="group_key" columns={variantColumns} dataSource={color.variants} pagination={false} size="small" />
          </div>)}
        </div> }}
        pagination={{ ...persistedPagination('inventory-products', 20), showSizeChanger: true }}
        request={async (params) => {
          const response = await listInventory({
            shop_id: shopId, group_by_product: true, page: params.current, per_page: params.pageSize,
            search: String(params.search || '').trim() || undefined,
          });
          setSummary({incoming_quantity: 0, purchase_needed_quantity: 0, ready_quantity: 0, waiting_quantity: 0, packing_quantity: 0, idle_quantity: 0,
            ...response.data.summary, product_count: response.data.summary.product_count || 0,
            demand_quantity: response.data.summary.demand_quantity || 0,
            net_shortage_quantity: response.data.summary.net_shortage_quantity ?? 0,
            structural_gap_quantity: response.data.summary.structural_gap_quantity ?? 0});
          return { data: response.data.products, total: response.data.pagination.total, success: response.success };
        }}
      />
          </>,
        },
        {
          key: 'movements',
          label: '出入库流水',
          children: <Movements shopId={shopId} />,
        },
      ]}
    />}
    <Modal title="批量手动入库 · 选择商品与数量" open={addOpen} onCancel={() => !receivingBusy && setAddOpen(false)}
      confirmLoading={receivingBusy} onOk={submitReceiving} okText={`确认入库（${Object.values(receiving).reduce((sum, row) => sum + row.quantity, 0)} 件）`}
      okButtonProps={{disabled: !Object.keys(receiving).length}} cancelButtonProps={{disabled: receivingBusy}}
      width={screens.md ? 1000 : '95%'} destroyOnHidden>
      <div style={{marginBottom: 16}}>
        <Typography.Text strong>本批已选 {Object.keys(receiving).length} 个变体</Typography.Text>
        <div style={{maxHeight: 240, overflowY: 'auto'}}>{Object.values(receiving).map(({item, quantity}) => <div key={item.group_key} style={{display: 'flex', flexWrap: 'wrap', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #e2e8f0'}}>
          {item.image_url && <Image src={item.image_url} alt={item.title} width={48} height={48} style={{objectFit: 'contain', marginRight: 12}} />}
          <span style={{flex: 1, minWidth: 160}}>{item.title} / {item.color} / <strong>{item.model}</strong></span>
          <InputNumber aria-label={`${item.title} ${item.color} ${item.model} 入库数量`} min={1} max={100000} precision={0} value={quantity} disabled={receivingBusy}
            onChange={(value) => setReceiving((prev) => ({...prev, [item.group_key]: {item, quantity: value ?? 0}}))} />
          <Button disabled={receivingBusy} onClick={() => setReceiving((prev) => {const next = {...prev}; delete next[item.group_key]; return next;})}>移除</Button>
        </div>)}</div>
        <Input.TextArea value={receivingReason} disabled={receivingBusy} maxLength={255} placeholder="入库备注（可选）" aria-label="批量入库备注" style={{marginTop: 12}} onChange={(event) => setReceivingReason(event.target.value)} />
      </div>
      <Alert type="info" showIcon message="先展开大类，再选颜色和型号。" style={{ marginBottom: 16 }} />
      <Input.Search value={receivingQuery} allowClear size="large" aria-label="搜索入库商品" placeholder="输入商品、颜色、型号或 SKU，停顿后自动搜索"
        style={{marginBottom: 12, fontSize: 16}} onChange={(event) => setReceivingQuery(event.target.value)} onSearch={(value) => setReceivingSearch(value.trim())} />
      <ProTable<InventoryProduct>
        rowKey="group_key"
        columns={pickerColumns}
        options={false}
        search={false}
        params={{search: receivingSearch}}
        expandable={{ expandRowByClick: true, expandedRowRender: pickerColors }}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        request={async (params) => {
          if (!shopId) return { data: [], total: 0, success: true };
          const response = await listReceivingProducts({
            shop_id: shopId, page: params.current, per_page: params.pageSize,
            search: String(params.search || '').trim() || undefined,
          });
          return { data: response.data.products, total: response.data.pagination.total, success: response.success };
        }}
      />
    </Modal>
    <Modal title={`${adjustMode === 'add' ? '手动入库' : '调整库存'} · ${selected?.title || ''} / ${selected?.color || ''} / ${selected?.model || ''}`} open={adjustOpen} confirmLoading={adjustBusy} onOk={submitAdjustment} onCancel={() => setAdjustOpen(false)} okText={adjustMode === 'add' ? '确认入库' : '确认调整'} destroyOnHidden>
      <Alert type={adjustMode === 'add' ? 'info' : 'warning'} showIcon message={adjustMode === 'add' ? `当前在库 ${selected?.on_hand_quantity ?? 0} 件；本次只增加公共库存。` : `当前在库 ${selected?.on_hand_quantity ?? 0} 件；扣减后自动重算缺货，不绑定订单。`} style={{ marginBottom: 16 }} />
      <Form form={form} layout="vertical">
        <Form.Item name="quantity_delta" label={adjustMode === 'add' ? '入库数量' : '调整数量（正数入库，负数扣减）'} rules={[{ required: true, message: '请输入数量' }]}>
          <InputNumber precision={0} min={adjustMode === 'add' ? 1 : -100000} max={100000} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="reason" label="调整原因（可选）" rules={[{ max: 255, message: '最多 255 字' }]}>
          <Input.TextArea rows={3} maxLength={255} placeholder="例如：期初盘点、非订单库存入库、盘亏" />
        </Form.Item>
      </Form>
    </Modal>
    <Drawer title={`${selected?.title || ''} / ${selected?.model || ''} · 调整记录`} open={historyOpen} onClose={() => setHistoryOpen(false)} width={screens.md ? 560 : '100%'}>
      {adjustments.length ? adjustments.map((row) => <div key={row.id} className="inventory-history-row">
        <Tag color={row.quantity_delta > 0 ? 'green' : 'red'}>{row.quantity_delta > 0 ? '+' : ''}{row.quantity_delta}</Tag>
        <div><Typography.Text>{row.reason}</Typography.Text><Typography.Text type="secondary">{formatBeijingTime(row.created_at)}</Typography.Text></div>
      </div>) : <Typography.Text type="secondary">暂无手工调整记录。采购入库和发货数量显示在库存列表中。</Typography.Text>}
    </Drawer>
  </PageContainer>;
};

export default Inventory;
