import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Alert, Button, Card, Drawer, Grid, Image, Input, InputNumber,
  List, message, Popconfirm, Select, Space, Table, Tabs, Tag, Typography,
} from 'antd';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  addAfterSaleCost, createAfterSale, deleteAfterSale, getAfterSale, getOriginalOrderSource, listAfterSales, lookupOriginalOrder,
  searchCatalogProducts, getCatalogVariants, updateAfterSaleNote,
  type AfterSale, type CatalogItem, type CatalogProduct,
} from '@/services/afterSales';
import { getErrorMessage } from '@/utils/error';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';
import { formatBeijingTime } from '@/utils/timezone';
import AfterSaleStates from './States';
import TrackingModal from './TrackingModal';

type SourceItem = { id: number; title: string; variant_title?: string; image_url?: string; quantity: number };
type DraftItem = { source_order_item_id: number; quantity: number };
type ExchangeDraftItem = { catalog_product_id: number; quantity: number; product: CatalogItem };
type OrderSource = { id: number; shop_id: number; order_number: string; warehouse_status: string; note?: string; line_items: SourceItem[] };

const kindNames = { resend: '原品补发', exchange: '换品补发', missing: '漏发补发' };
const catalogColorLabel = (item: CatalogItem) => `${item.color || '未标注颜色'} ${item.configuration || ''}`.trim();

const AfterSales: React.FC = () => {
  const screens = Grid.useBreakpoint();
  const selected = getSelectedShopId();
  const shopId = selected === ALL_SHOPS ? null : Number(selected);
  const [rows, setRows] = useState<AfterSale[]>([]);
  const [view, setView] = useState('all');
  const [page, setPage] = useState(1);
  const [searchText, setSearchText] = useState('');
  const [search, setSearch] = useState('');
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const reloadSeq = useRef(0);
  const [detail, setDetail] = useState<AfterSale | null>(null);
  const [trackingTarget, setTrackingTarget] = useState<AfterSale | null>(null);
  const [creating, setCreating] = useState(false);
  const [orderNumber, setOrderNumber] = useState('');
  const [order, setOrder] = useState<OrderSource | null>(null);
  const [kind, setKind] = useState<'resend' | 'exchange' | 'missing'>('resend');
  const [reason, setReason] = useState('');
  const [note, setNote] = useState('');
  const [sourceMailId, setSourceMailId] = useState<string>();
  const [detailNote, setDetailNote] = useState('');
  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [exchangeDraft, setExchangeDraft] = useState<ExchangeDraftItem[]>([]);
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogProducts, setCatalogProducts] = useState<CatalogProduct[]>([]);
  const [catalogProduct, setCatalogProduct] = useState<string>();
  const [catalogColor, setCatalogColor] = useState<string>();
  const [productSearch, setProductSearch] = useState('');
  const [productPage, setProductPage] = useState(1);
  const [moreProducts, setMoreProducts] = useState(false);
  const catalogRequest = useRef(0);
  const catalogTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingOrder, setLoadingOrder] = useState(false);
  const [costType, setCostType] = useState<'product' | 'shipping' | 'other'>('product');
  const [costAmount, setCostAmount] = useState<number | null>(null);
  const [costNote, setCostNote] = useState('');

  const reload = useCallback(async () => {
    const seq = ++reloadSeq.current;
    setLoading(true);
    try {
      const result = await listAfterSales({ shop_id: shopId || 'all', view, page, per_page: 20, search: search || undefined });
      if (seq === reloadSeq.current) {
        setRows(result.data.after_sales);
        setTotal(result.data.pagination.total);
        setCounts(result.data.counts || {});
        if (!result.data.after_sales.length && page > 1) setPage(page - 1);
      }
    } catch (error) { if (seq === reloadSeq.current) message.error(getErrorMessage(error, '获取售后单失败')); }
    finally { if (seq === reloadSeq.current) setLoading(false); }
  }, [shopId, view, page, search]);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { setTrackingTarget(null); setPage(1); setSearch(''); setSearchText(''); }, [shopId]);
  useEffect(() => () => { catalogRequest.current++; if (catalogTimer.current) clearTimeout(catalogTimer.current); }, []);
  useEffect(() => {
    catalogRequest.current++;
    if (catalogTimer.current) clearTimeout(catalogTimer.current);
    setCatalog([]); setCatalogProducts([]); setCatalogProduct(undefined); setCatalogColor(undefined);
    setCatalogLoading(false); setMoreProducts(false);
  }, [order?.id, creating, shopId]);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initial = Number(params.get('order_id'));
    const mailId = params.get('mail_id') || undefined;
    setSourceMailId(mailId);
    if (mailId) setNote(`邮件售后来源：${mailId}`);
    if (initial > 0) {
      setCreating(true);
      getOriginalOrderSource(initial).then((response) => {
        const source = response.data as unknown as OrderSource;
        setOrder(source); setOrderNumber(source.order_number.replace(/^#/, ''));
      }).catch((error) => message.error(getErrorMessage(error, '获取原订单失败')));
    }
    const afterSaleId = Number(params.get('after_sale_id'));
    if (afterSaleId > 0) getAfterSale(afterSaleId).then((response) => setDetail(response.data))
      .catch((error) => message.error(getErrorMessage(error, '获取售后单失败')));
  }, []);

  const loadOrder = async () => {
    if (loadingOrder) return;
    if (!orderNumber.trim()) { message.warning('请输入 Shopify 订单号'); return; }
    setOrder(null); setDraft([]); setExchangeDraft([]); setCatalog([]);
    setSourceMailId(undefined);
    setLoadingOrder(true);
    try {
      const response = await lookupOriginalOrder(orderNumber.trim(), shopId);
      const source = response.data as unknown as OrderSource;
      setOrder(source);
      setOrderNumber(source.order_number.replace(/^#/, ''));
      if (!['shipped', 'legacy_unverified'].includes(source.warehouse_status)) {
        message.warning('此订单尚未确认出库，暂不能发起售后补发');
      }
    } catch (error) { message.error(getErrorMessage(error, '获取原订单失败')); }
    finally { setLoadingOrder(false); }
  };

  const toggleItem = (item: SourceItem, selectedItem: boolean) => {
    setDraft((current) => selectedItem
      ? [...current, { source_order_item_id: item.id, quantity: 1 }]
      : current.filter((entry) => entry.source_order_item_id !== item.id));
  };

  const changeDraft = (id: number, change: Partial<DraftItem>) =>
    setDraft((current) => current.map((entry) => entry.source_order_item_id === id ? { ...entry, ...change } : entry));

  const loadProducts = async (term: string, page: number, requestId: number) => {
    if (!order) return;
    setCatalogLoading(true);
    try {
      const response = await searchCatalogProducts(order.shop_id, term, page);
      if (requestId !== catalogRequest.current) return;
      setCatalogProducts((previous) => page === 1 ? response.data.products : [...previous, ...response.data.products]);
      setProductPage(page); setMoreProducts(response.data.pagination.has_more);
    } catch (error) {
      if (requestId === catalogRequest.current) message.error(getErrorMessage(error, '搜索店铺商品失败'));
    } finally { if (requestId === catalogRequest.current) setCatalogLoading(false); }
  };

  const searchProducts = (term: string) => {
    if (catalogTimer.current) clearTimeout(catalogTimer.current);
    const requestId = ++catalogRequest.current;
    setProductSearch(term.trim()); setCatalogProducts([]); setMoreProducts(false);
    setCatalog([]); setCatalogProduct(undefined); setCatalogColor(undefined);
    if (!order || !term.trim()) { setCatalogLoading(false); return; }
    catalogTimer.current = setTimeout(() => void loadProducts(term.trim(), 1, requestId), 250);
  };

  const selectCatalogProduct = async (productId: string) => {
    if (!order) return;
    if (catalogTimer.current) clearTimeout(catalogTimer.current);
    const requestId = ++catalogRequest.current;
    setCatalogProduct(productId); setCatalogColor(undefined); setCatalog([]); setCatalogLoading(true);
    try {
      const items = new Map<number, CatalogItem>();
      let page = 1; let hasMore = true; let expected = 0;
      while (hasMore && requestId === catalogRequest.current) {
        const response = await getCatalogVariants(order.shop_id, productId, page++);
        if (requestId !== catalogRequest.current) return;
        if (!response.data.items.length && response.data.pagination.has_more) throw new Error('商品变体分页异常，请重新选择商品');
        const previousSize = items.size;
        for (const item of response.data.items) items.set(item.id, item);
        expected = response.data.pagination.total;
        hasMore = response.data.pagination.has_more;
        if (hasMore && items.size === previousSize) throw new Error('商品变体分页重复，请重新选择商品');
      }
      if (requestId !== catalogRequest.current) return;
      if (items.size !== expected) throw new Error('商品库在加载期间发生变化，请重新选择商品，避免漏掉变体');
      setCatalog(Array.from(items.values()));
    } catch (error) {
      if (requestId === catalogRequest.current) message.error(getErrorMessage(error, '加载完整变体失败，请重新选择商品'));
    } finally { if (requestId === catalogRequest.current) setCatalogLoading(false); }
  };

  const addExchangeProduct = (productId: number) => {
    const product = catalog.find((item) => item.id === productId);
    if (!product) return;
    setExchangeDraft((current) => current.some((item) => item.catalog_product_id === productId)
      ? current : [...current, { catalog_product_id: productId, quantity: 1, product }]);
  };

  const create = async () => {
    if (busy) return;
    const items = kind === 'exchange'
      ? exchangeDraft.map(({ catalog_product_id, quantity }) => ({ catalog_product_id, quantity }))
      : draft;
    if (!order || !reason.trim() || items.length === 0) { message.warning('请选择补发商品并填写售后原因'); return; }
    setBusy(true);
    try {
      const result = await createAfterSale({ order_id: order.id, kind, reason, note, items, source_mail_message_id: sourceMailId });
      setCreating(false); setDetail(result.data); setOrder(null); setDraft([]); setReason('');
      setExchangeDraft([]); setCatalog([]);
      setNote('');
      setSourceMailId(undefined);
      message.success(`售后单 ${result.data.customer_reference} 已创建；待采购商品请到采购管理的“售后待采购”筛选处理`);
      void reload();
    } catch (error) { message.error(getErrorMessage(error, '创建售后单失败')); }
    finally { setBusy(false); }
  };

  const update = async (action: () => Promise<{ data: AfterSale }>, success: string) => {
    setBusy(true);
    try {
      const result = await action(); setDetail(result.data); message.success(success); void reload();
    } catch (error) { message.error(getErrorMessage(error, '操作失败')); }
    finally { setBusy(false); }
  };

  const remove = async (record: AfterSale) => {
    setBusy(true);
    try {
      await deleteAfterSale(record.id);
      if (detail?.id === record.id) setDetail(null);
      message.success('售后单已隐藏，费用、库存与出库历史保留');
      await reload();
    } catch (error) { message.error(getErrorMessage(error, '删除售后单失败')); }
    finally { setBusy(false); }
  };

  return <PageContainer title="售后管理" subTitle="独立补发，物流对单到发货中心">
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert type="info" showIcon message="待采购商品进入采购管理，可合并生成清单。" />
      <Card>
        <Space wrap>
          <Button type="primary" onClick={() => setCreating(true)}>发起售后</Button>
          <Button onClick={() => void reload()} loading={loading}>刷新</Button>
          <Input.Search
            allowClear
            value={searchText}
            placeholder="搜索售后单号 / 原订单号 / 物流单号 / 客户"
            style={{ width: 380 }}
            onChange={(event) => {
              setSearchText(event.target.value);
              if (!event.target.value) {
                setSearch('');
                setPage(1);
              }
            }}
            onSearch={(value) => {
              setSearch(value.trim());
              setPage(1);
            }}
          />
        </Space>
      </Card>
      <Card title="售后单">
        <Tabs activeKey={view} onChange={(key) => { setView(key); setPage(1); }} items={[
          { key: 'all', label: `全部（${counts.all ?? 0}）` },
          { key: 'unprocessed', label: `未处理（${counts.unprocessed ?? 0}）` },
          { key: 'unprocured', label: `未采购（${counts.unprocured ?? 0}）` },
          { key: 'shipped', label: `已扫码出库（${counts.shipped ?? 0}）` },
        ]} />
        <Table size="small" rowKey="id" loading={loading} dataSource={rows} scroll={{ x: 800 }} pagination={{
          current: page, total, pageSize: 20, showSizeChanger: false,
          showTotal: (value) => `共 ${value} 条售后单`, onChange: (value) => setPage(value),
        }} columns={[
          { title: '售后单号', dataIndex: 'customer_reference', render: (value: string, row: AfterSale) => <Button type="link" onClick={() => setDetail(row)}>{value}</Button> },
          { title: '原订单', dataIndex: 'order_number', render: (value: string, row: AfterSale) => <a onClick={() => history.push(`/orders/detail/${row.order_id}`)}>{value}</a> },
          { title: '类型', dataIndex: 'kind', render: (value: AfterSale['kind']) => kindNames[value] },
          { title: '采购进度 / 库存情况', key: 'states', render: (_: unknown, row: AfterSale) => <AfterSaleStates record={row} /> },
          { title: '补发单号', dataIndex: 'tracking_number', render: (value?: string) => value || '待绑定' },
          { title: '售后成本', dataIndex: 'total_cost_cny', render: (value: number) => `¥${value.toFixed(2)}` },
          { title: '创建时间', dataIndex: 'created_at', render: (value: string) => formatBeijingTime(value) },
          { title: '操作', render: (_: unknown, row: AfterSale) => <Space wrap>
            {row.status !== 'shipped' && !row.shipped_at && <Button type="link" disabled={busy}
              onClick={() => setTrackingTarget(row)}>{row.tracking_number ? '修改物流' : '手动绑定物流'}</Button>}
            <Popconfirm title={`删除售后单 ${row.customer_reference}？`}
            description="隐藏并释放未出库占用；费用和出库历史保留。" onConfirm={() => remove(row)}>
            <Button danger type="link" disabled={busy}>删除</Button>
          </Popconfirm></Space> },
        ]} />
      </Card>
    </Space>

    <Drawer title="发起售后" width={screens.md ? 760 : '100%'} open={creating} onClose={() => setCreating(false)}
      extra={<Button type="primary" onClick={() => void create()} loading={busy}>创建售后单</Button>}>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space wrap><Input value={orderNumber} onChange={(event) => { setOrderNumber(event.target.value); setOrder(null); setDraft([]); setExchangeDraft([]); setCatalog([]); }}
          onPressEnter={() => void loadOrder()} placeholder="输入 Shopify 订单号，例如 8690" style={{ width: 250 }} />
          <Button loading={loadingOrder} onClick={() => void loadOrder()}>按订单号加载</Button></Space>
        {order && <>
          <Typography.Text>原订单 {order.order_number} · {order.line_items?.length || 0} 个商品行</Typography.Text>
          {!!order.note && <Alert type="info" showIcon message="原订单 Shopify 备注" description={order.note} />}
          <Select value={kind} onChange={setKind} style={{ width: '100%' }} options={Object.entries(kindNames).map(([value, label]) => ({ value, label }))} />
          <Input.TextArea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="售后原因，例如漏发、损坏、客户换款" rows={2} />
          <Input.TextArea value={note} onChange={(event) => setNote(event.target.value)} maxLength={5000} showCount
            placeholder="备注信息（可选）：客户要求、处理进度、采购或补发注意事项" rows={3} />
          {kind === 'exchange' ? <>
            <Select aria-label="选择补发商品" showSearch filterOption={false} value={catalogProduct} loading={catalogLoading}
              placeholder="搜索商品名称、颜色、型号或 SKU，然后选择商品"
              style={{ width: '100%' }} onSearch={searchProducts} onChange={(value) => void selectCatalogProduct(value)}
              options={catalogProducts.map((product) => ({ value: product.shopify_product_id,
                label: `${product.title}（${product.variant_count} 个变体）` }))} />
            {moreProducts && <Button disabled={catalogLoading} onClick={() => void loadProducts(productSearch, productPage + 1, catalogRequest.current)}>加载更多匹配商品</Button>}
            {catalogProduct && <Typography.Text type="secondary">{catalogLoading ? '正在加载全部变体，请稍候…' : catalog.length ? `已完整加载 ${catalog.length} 个变体 · ${new Set(catalog.map(catalogColorLabel)).size} 个颜色／配置` : '未加载到变体，请重新选择商品'}</Typography.Text>}
            <Select aria-label="选择补发颜色配置" value={catalogColor} allowClear disabled={catalogLoading || !catalog.length}
              placeholder="筛选颜色／配置（可选）" style={{ width: '100%' }} onChange={setCatalogColor}
              options={Array.from(new Set(catalog.map(catalogColorLabel))).map((color) => ({ value: color, label: `${color}（${catalog.filter((item) => catalogColorLabel(item) === color).length} 个变体）` }))} />
            <Select aria-label="选择补发型号变体" showSearch optionFilterProp="label" value={null}
              disabled={catalogLoading || !catalog.length} placeholder="搜索并选择具体型号／完整变体"
              style={{ width: '100%' }} onChange={addExchangeProduct}
              options={catalog.filter((item) => !catalogColor || catalogColorLabel(item) === catalogColor).map((product) => ({ value: product.id,
                label: `${product.variant_title || '默认变体'}${product.sku ? ` · ${product.sku}` : ''}${product.status !== 'active' ? ' · 未上架／历史商品' : ''}` }))} />
            <List bordered dataSource={exchangeDraft} locale={{ emptyText: '尚未选择要补发的商品' }} renderItem={(entry) => <List.Item
              actions={[<Button key="remove" type="link" danger onClick={() => setExchangeDraft((current) => current.filter((item) => item.catalog_product_id !== entry.catalog_product_id))}>移除</Button>]}>
              <Space wrap>{entry.product.image_url && <Image src={entry.product.image_url} width={54} height={54} style={{ objectFit: 'contain' }} />}
                <Typography.Text>{entry.product.title} / {entry.product.variant_title || '默认变体'}</Typography.Text>
                <InputNumber min={1} value={entry.quantity} addonBefore="补发件数" onChange={(value) => setExchangeDraft((current) => current.map((item) => item.catalog_product_id === entry.catalog_product_id ? { ...item, quantity: value || 1 } : item))} />
              </Space>
            </List.Item>} />
          </> : <List dataSource={order.line_items || []} renderItem={(item) => {
            const entry = draft.find((row) => row.source_order_item_id === item.id);
            return <List.Item style={{ display: 'block' }}>
              <Space align="start" wrap><input type="checkbox" checked={!!entry} onChange={(event) => toggleItem(item, event.target.checked)} />
                {item.image_url && <Image src={item.image_url} width={54} height={54} style={{ objectFit: 'contain' }} />}
                <Typography.Text>{item.title} / {item.variant_title}（原单 {item.quantity} 件）</Typography.Text></Space>
              {entry && <Space wrap style={{ marginTop: 8, marginLeft: 24 }}>
                <span>补发数量</span><InputNumber min={1} max={kind === 'missing' ? item.quantity : undefined} value={entry.quantity}
                  onChange={(value) => changeDraft(item.id, { quantity: value || 1 })} />
              </Space>}
            </List.Item>;
          }} />}
        </>}
      </Space>
    </Drawer>

    <Drawer title={detail ? `售后单 ${detail.customer_reference}` : '售后详情'} width={screens.md ? 860 : '100%'} open={!!detail}
      onClose={() => setDetail(null)} afterOpenChange={(open) => { if (open) setDetailNote(detail?.note || ''); }}>
      {detail && <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Alert type="info" showIcon message={`原订单 ${detail.order_number} · ${kindNames[detail.kind]}`}
          description={<Space direction="vertical"><AfterSaleStates record={detail} /><Typography.Text>{detail.reason}</Typography.Text></Space>} />
        <Card size="small" title="售后备注">
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input.TextArea value={detailNote} onChange={(event) => setDetailNote(event.target.value)}
              maxLength={5000} showCount rows={3} placeholder="记录客户要求、处理进度或补发注意事项" />
            <Button loading={busy} disabled={detailNote.trim() === (detail.note || '')}
              onClick={() => void update(() => updateAfterSaleNote(detail.id, detailNote), '备注已保存')}>保存备注</Button>
          </Space>
        </Card>
        <Card size="small" title="补发商品">
          <List dataSource={detail.items} renderItem={(item) => <List.Item>
            <Space wrap>{item.image_url && <Image src={item.image_url} width={52} height={52} style={{ objectFit: 'contain' }} />}
              <span>{item.title} / {item.variant_title}</span>
              <Tag>需求 {item.quantity}</Tag>{detail.status !== 'shipped' && <Tag color="green">当前可用 {item.available_quantity ?? Math.max(item.quantity - item.needed_quantity, 0)}</Tag>}
              {!!item.needed_quantity && <Tag color="orange">待补 {item.needed_quantity}</Tag>}
            </Space>
          </List.Item>} />
        </Card>
        <Card size="small" title="采购流程">
          <Space wrap>{detail.purchase_lists?.map((purchase) => <Button key={purchase.id}
            onClick={() => history.push(`/purchases?purchase_id=${purchase.id}`)}>
            采购清单 #{purchase.id} · {purchase.status}</Button>)}
            {!detail.purchase_lists?.length && <Typography.Text type="secondary">{detail.status === 'ready' ? '尚无采购清单；现货可满足本单，不代表已为本单采购到货' : '待采购商品请到采购管理的“售后待采购”筛选生成清单'}</Typography.Text>}
            <Button onClick={() => history.push('/purchases?view=after_sale')}>打开售后待采购</Button>
          </Space>
        </Card>
        <Card size="small" title={`售后成本明细 · 合计 ¥${detail.total_cost_cny.toFixed(2)}`}>
          <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="未登记的费用不计入原订单利润。" />
          <List size="small" dataSource={detail.costs} renderItem={(cost) => <List.Item>{cost.cost_type} · ¥{cost.amount_cny.toFixed(2)} · {cost.note || '—'} · {formatBeijingTime(cost.incurred_at)}</List.Item>} />
          <Space wrap><Select value={costType} onChange={setCostType} options={[{ value: 'product', label: '采购成本' }, { value: 'shipping', label: '物流成本' }, { value: 'other', label: '其他成本' }]} />
            <InputNumber min={0.01} precision={2} prefix="¥" value={costAmount} onChange={setCostAmount} placeholder="人民币金额" />
            <Input value={costNote} onChange={(event) => setCostNote(event.target.value)} placeholder="费用说明" />
            <Button loading={busy} onClick={() => void update(() => addAfterSaleCost(detail.id, { cost_type: costType, amount_cny: costAmount, note: costNote }), '售后成本已计入原单利润')}>记录费用</Button>
          </Space>
        </Card>
        <Card size="small" title="补发物流">
          <Space direction="vertical">
            <Typography.Text>4PX 客户单号：<Typography.Text copyable>{detail.customer_reference}</Typography.Text></Typography.Text>
            <Typography.Text>补发物流单号：{detail.tracking_number || '待绑定'}</Typography.Text>
            <Typography.Text>实体面单条码：{detail.label_barcode || '待获取面单'}</Typography.Text>
            {detail.status !== 'shipped' && !detail.shipped_at && <Button onClick={() => setTrackingTarget(detail)}>
              {detail.tracking_number ? '修改物流绑定' : '手动绑定物流单号'}</Button>}
            <Button onClick={() => history.push('/shipping')}>前往发货中心对单、打印与扫码</Button>
            {detail.status === 'ready' && detail.label_barcode && <Tag color="green">现货可满足本单，面单已获取；扫码时再次核对库存</Tag>}
          </Space>
        </Card>
      </Space>}
    </Drawer>
    {trackingTarget && <TrackingModal key={trackingTarget.id} record={trackingTarget} onClose={() => setTrackingTarget(null)}
      onSaved={(record) => {
        reloadSeq.current++;
        setLoading(false);
        setRows((current) => current.map((row) => row.id === record.id ? record : row));
        setDetail((current) => current?.id === record.id ? record : current);
        setTrackingTarget(null);
        message.success('售后物流已绑定，尚未出库；未回填 Shopify 或发送通知');
      }} />}
  </PageContainer>;
};

export default AfterSales;
