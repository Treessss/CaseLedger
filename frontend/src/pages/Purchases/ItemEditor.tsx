import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Image, Input, InputNumber, List, message, Modal, Select, Space, Typography } from 'antd';
import { createManualPurchase, getPurchaseVariants, searchPurchaseProducts, updatePurchaseItems,
  type PurchaseCatalogItem, type PurchaseCatalogProduct, type PurchaseItem, type PurchaseListDetail } from '@/services/purchases';
import { getErrorMessage } from '@/utils/error';

type Addition = { product: PurchaseCatalogItem; quantity: number };
const colorLabel = (item: PurchaseCatalogItem) => `${item.color || '未标注颜色'} ${item.configuration || ''}`.trim();

export default function ItemEditor({ purchase, shopId, onSaved, onClose }: {
  purchase: PurchaseListDetail | null; shopId: number;
  onSaved: (purchase: PurchaseListDetail) => void; onClose: () => void;
}) {
  const [name, setName] = useState('手动采购清单');
  const [quantities, setQuantities] = useState<Record<string, number>>(() =>
    Object.fromEntries((purchase?.items || []).map(item => [item.group_key, item.quantity])));
  const [additions, setAdditions] = useState<Addition[]>([]);
  const [products, setProducts] = useState<PurchaseCatalogProduct[]>([]);
  const [product, setProduct] = useState<string>();
  const [color, setColor] = useState<string>();
  const [variants, setVariants] = useState<PurchaseCatalogItem[]>([]);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const sequence = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { sequence.current++; if (timer.current) clearTimeout(timer.current); }, []);

  const loadProducts = async (term: string, nextPage: number, seq: number) => {
    setLoading(true);
    try {
      const response = await searchPurchaseProducts(shopId, term, nextPage);
      if (seq !== sequence.current) return;
      setProducts(current => nextPage === 1 ? response.data.products : [...current, ...response.data.products]);
      setPage(nextPage); setMore(response.data.pagination.has_more);
    } catch (error) {
      if (seq === sequence.current) message.error(getErrorMessage(error, '查询商品失败'));
    } finally { if (seq === sequence.current) setLoading(false); }
  };
  const searchProducts = (term: string) => {
    if (timer.current) clearTimeout(timer.current);
    const seq = ++sequence.current;
    setSearch(term.trim()); setProducts([]); setMore(false); setProduct(undefined); setVariants([]); setColor(undefined);
    if (!term.trim()) { setLoading(false); return; }
    timer.current = setTimeout(() => void loadProducts(term.trim(), 1, seq), 250);
  };
  const selectProduct = async (id: string) => {
    if (timer.current) clearTimeout(timer.current);
    const seq = ++sequence.current;
    setProduct(id); setColor(undefined); setVariants([]); setLoading(true);
    try {
      const first = await getPurchaseVariants(shopId, id);
      if (seq !== sequence.current) return;
      const collected = new Map(first.data.items.map(item => [item.id, item]));
      const total = first.data.pagination.total;
      // Fetch every page, with bounded concurrency; never silently truncate variants.
      const pages = Math.ceil(total / 200);
      for (let start = 2; start <= pages; start += 3) {
        const batch = await Promise.all(Array.from({ length: Math.min(3, pages - start + 1) },
          (_, index) => getPurchaseVariants(shopId, id, start + index)));
        if (seq !== sequence.current) return;
        for (const response of batch) {
          if (response.data.pagination.total !== total) throw new Error('商品库已变化，请重新选择商品');
          for (const item of response.data.items) collected.set(item.id, item);
        }
      }
      if (collected.size !== total) throw new Error('变体未完整加载，请重新选择商品');
      setVariants(Array.from(collected.values()));
    } catch (error) {
      if (seq === sequence.current) message.error(getErrorMessage(error, '加载完整变体失败'));
    } finally { if (seq === sequence.current) setLoading(false); }
  };
  const addVariant = (id: number) => {
    const chosen = variants.find(item => item.id === id);
    if (!chosen) return;
    setAdditions(current => current.some(item => item.product.id === id)
      ? current.map(item => item.product.id === id ? { ...item, quantity: Math.min(item.quantity + 1, 100000) } : item)
      : [...current, { product: chosen, quantity: 1 }]);
  };
  const minimum = (item: PurchaseItem) => {
    const row = purchase?.receipt?.find(receipt => receipt.group_key === item.group_key);
    return (row?.received_quantity || 0) + (row?.damaged_quantity || 0) + (row?.confirmed_missing_quantity || 0);
  };
  const total = Object.values(quantities).reduce((sum, quantity) => sum + quantity, 0)
    + additions.reduce((sum, row) => sum + row.quantity, 0);
  const save = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const extra = additions.map(row => ({ product_id: row.product.id, quantity: row.quantity }));
      const response = purchase
        ? await updatePurchaseItems(purchase.id, purchase.edit_token || '',
          purchase.items.map(item => ({ group_key: item.group_key, quantity: quantities[item.group_key] })), extra)
        : await createManualPurchase(shopId, name, extra);
      message.success(purchase ? '已保存采购数量，库存和历史入库记录未改动' : '手动采购草稿已创建，请核对后确认发起采购');
      onSaved(response.data);
    } catch (error) { message.error(getErrorMessage(error, '保存采购单失败')); }
    finally { setSaving(false); }
  };

  return <Modal open title={purchase ? '添加商品 / 编辑采购数量' : '手动创建采购单'} width={960}
    onCancel={onClose} onOk={() => void save()} okText={`保存采购单（${total} 件）`} confirmLoading={saving}
    okButtonProps={{ disabled: total === 0 || !!purchase && !purchase.edit_token }}
    cancelButtonProps={{ disabled: saving }} closable={!saving} maskClosable={!saving}>
    <div style={{ maxHeight: '65vh', overflowY: 'auto', paddingRight: 8 }}>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Alert type="info" showIcon message="额外备货，不改原订单；已清点数量不可减少。" />
        {!purchase && <Input aria-label="手动采购清单名称" value={name} maxLength={120} onChange={event => setName(event.target.value)} placeholder="采购清单名称" />}
        <Card size="small" title="添加商品 · 商品大类 → 颜色／配置 → 型号">
          <Space direction="vertical" style={{ width: '100%' }}>
            <Select aria-label="选择采购商品" showSearch filterOption={false} loading={loading} value={product}
              onSearch={searchProducts} onChange={value => void selectProduct(value)} style={{ width: '100%' }}
              placeholder="输入商品名称或 SKU 搜索" options={products.map(item => ({ value: item.shopify_product_id,
                label: `${item.title}（${item.variant_count} 个变体）` }))} />
            {more && <Button disabled={loading} onClick={() => void loadProducts(search, page + 1, sequence.current)}>加载更多商品</Button>}
            <Select aria-label="选择采购颜色配置" value={color} onChange={setColor} allowClear disabled={loading || !variants.length}
              style={{ width: '100%' }} placeholder="选择颜色／配置（可选）"
              options={Array.from(new Set(variants.map(colorLabel))).map(value => ({ value, label: value }))} />
            <Select aria-label="选择采购型号变体" showSearch optionFilterProp="label" value={null}
              onChange={addVariant} disabled={loading || !variants.length} style={{ width: '100%' }} placeholder="选择型号添加到采购单"
              options={variants.filter(item => !color || colorLabel(item) === color).map(item => ({ value: item.id,
                label: `${item.variant_title || '默认变体'}${item.sku ? ` · SKU: ${item.sku}` : ''}` }))} />
            {product && <Typography.Text type="secondary">{loading ? '正在加载全部变体…' : `已完整加载 ${variants.length} 个变体，包含不同配置`}</Typography.Text>}
          </Space>
        </Card>
        {additions.length > 0 && <List bordered header="本次额外添加" dataSource={additions} renderItem={row => <List.Item
          actions={[<Button key="remove" danger type="link" disabled={saving} onClick={() => setAdditions(current => current.filter(item => item.product.id !== row.product.id))}>移除</Button>]}>
          <Space wrap>{row.product.image_url && <Image src={row.product.image_url} width={60} height={60} style={{ objectFit: 'contain' }} />}
            <Typography.Text>{row.product.title} / {row.product.variant_title || '默认款'} {row.product.sku && `· SKU: ${row.product.sku}`}</Typography.Text>
            <InputNumber aria-label={`${row.product.variant_title || '默认款'} 新增数量`} min={1} max={100000} precision={0} value={row.quantity}
              disabled={saving} onChange={value => setAdditions(current => current.map(item => item.product.id === row.product.id ? { ...item, quantity: value ?? 1 } : item))} />
          </Space>
        </List.Item>} />}
        {purchase?.products.map(group => <Card size="small" key={group.group_key} title={group.title}>
          {group.colors.map(shade => <div key={shade.original_name || shade.name} style={{ marginBottom: 12 }}>
            <Space>{shade.image_url && <Image src={shade.image_url} width={64} height={64} style={{ objectFit: 'contain' }} />}
              <Typography.Text strong>{shade.name}</Typography.Text></Space>
            {shade.variants.map(item => <div key={item.group_key} style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'space-between', padding: '8px 0', flexWrap: 'wrap' }}>
              <div><Typography.Text>{item.model_title || item.variant_title || '默认款'}</Typography.Text>
                <div><Typography.Text type="secondary">原采购 {item.quantity} 件 · 已清点 {minimum(item)} 件 · {item.sku || '无 SKU'}</Typography.Text></div></div>
              <InputNumber aria-label={`${item.variant_title || '默认款'} 采购数量`} min={minimum(item)} max={100000} precision={0}
                disabled={saving} value={quantities[item.group_key]} onChange={value => setQuantities(current => ({ ...current, [item.group_key]: value ?? minimum(item) }))} />
            </div>)}
          </div>)}
        </Card>)}
        {purchase && <Typography.Text type="secondary">未清点商品数量改为 0 可移除；保存后导出 PDF、Excel 和采购中数量会同步更新。已结束到货的清单请另建采购单。</Typography.Text>}
      </Space>
    </div>
  </Modal>;
}
