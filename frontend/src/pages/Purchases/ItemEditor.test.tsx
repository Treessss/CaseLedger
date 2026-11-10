import '@ant-design/v5-patch-for-react-19';
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ItemEditor from './ItemEditor';
import { createManualPurchase, getPurchaseVariants, searchPurchaseProducts, updatePurchaseItems } from '@/services/purchases';

jest.mock('@/services/purchases', () => ({ createManualPurchase: jest.fn(), getPurchaseVariants: jest.fn(),
  searchPurchaseProducts: jest.fn(), updatePurchaseItems: jest.fn() }));
const variant = { group_key: 'blue', title: 'Case', variant_title: 'Blue / iPhone 17 Pro',
  model_title: 'iPhone 17 Pro', sku: 'blue-17', vendor: '', image_url: '', quantity: 3, order_numbers: ['10000'] };
const detail = { id: 1, shop_id: 1, name: '编辑测试', order_count: 1, item_count: 1, edit_token: 'version-one',
  product_count: 1, color_count: 1, total_quantity: 3, status: 'partially_received' as const, created_at: '', order_ids: [1],
  receipt: [{ group_key: 'blue', planned_quantity: 3, received_quantity: 1, damaged_quantity: 0,
    confirmed_missing_quantity: 1, pending_quantity: 1, missing_quantity: 2 }],
  items: [variant], products: [{ group_key: 'case', title: 'Case', vendor: '', image_url: '',
    quantity: 3, variant_count: 1, color_count: 1, order_numbers: ['10000'], variants: [variant],
    colors: [{ name: 'Blue', quantity: 3, variant_count: 1, image_url: '', order_numbers: ['10000'], variants: [variant] }] }] };

beforeEach(() => { jest.clearAllMocks(); });

test('edits quantity with the version token and protects counted quantities', async () => {
  const saved = jest.fn();
  jest.mocked(updatePurchaseItems).mockResolvedValue({ success: true, data: detail });
  render(React.createElement(ItemEditor, { purchase: detail, shopId: 1, onSaved: saved, onClose: jest.fn() }));
  const quantity = screen.getByLabelText('Blue / iPhone 17 Pro 采购数量');
  expect(quantity.getAttribute('aria-valuemin')).toBe('2');
  fireEvent.change(quantity, { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: '保存采购单（2 件）' }));
  await waitFor(() => expect(updatePurchaseItems).toHaveBeenCalledWith(1, 'version-one',
    [{ group_key: 'blue', quantity: 2 }], []));
  expect(saved).toHaveBeenCalledWith(detail);
  expect(createManualPurchase).not.toHaveBeenCalled();
});

test('fetches all variant pages and manually creates stock without binding orders', async () => {
  const saved = jest.fn();
  const last = { id: 201, title: 'Case', color: 'Red', configuration: '+ wrist chain',
    variant_title: 'Red / iPhone 18 Pro + wrist chain', sku: 'RED-18' };
  jest.mocked(searchPurchaseProducts).mockResolvedValue({ success: true, data: {
    products: [{ shopify_product_id: 'case', title: 'Case', variant_count: 201 }], pagination: { total: 1, has_more: false } } });
  jest.mocked(getPurchaseVariants).mockImplementation(async (_shop, _id, page = 1) => ({ success: true, data: {
    items: page === 1 ? Array.from({ length: 200 }, (_, index) => ({ id: index + 1, title: 'Case',
      color: 'Blue', variant_title: `Blue / model ${index + 1}` })) : [last],
    pagination: { total: 201, has_more: page === 1 } } }));
  jest.mocked(createManualPurchase).mockResolvedValue({ success: true, data: detail });
  render(React.createElement(ItemEditor, { purchase: null, shopId: 1, onSaved: saved, onClose: jest.fn() }));
  fireEvent.change(screen.getByRole('combobox', { name: '选择采购商品' }), { target: { value: 'Case' } });
  fireEvent.click(await screen.findByText('Case（201 个变体）'));
  await screen.findByText('已完整加载 201 个变体，包含不同配置');
  expect(getPurchaseVariants).toHaveBeenCalledWith(1, 'case', 2);
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择采购颜色配置' }));
  fireEvent.click(await screen.findByTitle('Red + wrist chain'));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择采购型号变体' }));
  fireEvent.click(await screen.findByTitle('Red / iPhone 18 Pro + wrist chain · SKU: RED-18'));
  fireEvent.change(screen.getByLabelText('Red / iPhone 18 Pro + wrist chain 新增数量'), { target: { value: '4' } });
  fireEvent.click(screen.getByRole('button', { name: '保存采购单（4 件）' }));
  await waitFor(() => expect(createManualPurchase).toHaveBeenCalledWith(1, '手动采购清单', [{ product_id: 201, quantity: 4 }]));
  expect(saved).toHaveBeenCalledWith(detail);
  expect(updatePurchaseItems).not.toHaveBeenCalled();
});

test('failed or stale edits keep the editor and entered quantities open for review', async () => {
  jest.mocked(updatePurchaseItems).mockRejectedValue(new Error('采购单已发生入库，请刷新'));
  const saved = jest.fn();
  render(React.createElement(ItemEditor, { purchase: detail, shopId: 1, onSaved: saved, onClose: jest.fn() }));
  fireEvent.click(screen.getByRole('button', { name: '保存采购单（3 件）' }));
  await screen.findByText('采购单已发生入库，请刷新');
  expect(saved).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Blue / iPhone 17 Pro 采购数量')).toBeTruthy();
});
