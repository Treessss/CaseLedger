import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { history } from '@umijs/max';
import InventoryDemandOrders, { collectDemandOrders } from './InventoryDemandOrders';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
type InventoryItem = import('@/services/inventory/api').InventoryItem;

const variant = (overrides: Partial<InventoryItem>): InventoryItem => ({
  group_key: 'v1', product_key: 'p1', title: '磁吸指环', color: '青蓝色', model: 'iPhone 18 Pro',
  sku: 'sku-18', image_url: '', received_quantity: 1, shipped_quantity: 0, reserved_quantity: 0,
  occupied_orders: [], adjustment_quantity: 0, on_hand_quantity: 1, available_quantity: 1, ...overrides,
});
const variants = [
  variant({ demand_orders: [
    { order_id: 10, order_number: '9379', quantity: 2 },
    { order_id: 10, order_number: 'CF9379', after_sale_id: 5, quantity: 1 },
  ] }),
  variant({ group_key: 'v2', model: 'iPhone 16 Pro', sku: 'sku-16', demand_orders: [
    { order_id: 10, order_number: '9379', quantity: 1 },
    { order_id: 11, order_number: '10000', quantity: 0 },
  ] }),
];

test('aggregates order quantities without merging original and after-sale demands or distinct variants', () => {
  const orders = collectDemandOrders(variants);
  expect(orders).toHaveLength(2);
  expect(orders[0].quantity).toBe(3);
  expect(orders[0].lines.map((line) => line.model)).toEqual(['iPhone 18 Pro', 'iPhone 16 Pro']);
  expect(orders[1].quantity).toBe(1);
  expect(variants[0].on_hand_quantity).toBe(1);
});

test('opens clear searchable demand detail and navigates original and CF orders separately', async () => {
  const push = jest.spyOn(history, 'push').mockImplementation(() => {});
  const rowClick = jest.fn();
  render(React.createElement('div', { onClick: rowClick, role: 'presentation' },
    React.createElement(InventoryDemandOrders, { title: '磁吸指环', variants })));
  fireEvent.click(screen.getByRole('button', { name: '查看需求订单（2 单）' }));
  expect(rowClick).not.toHaveBeenCalled();
  const original = await screen.findByRole('region', { name: '9379需求明细' });
  expect(within(original).getByText('需要 3 件')).toBeTruthy();
  expect(within(original).getByText('iPhone 18 Pro')).toBeTruthy();
  expect(within(original).getByText('SKU：sku-16')).toBeTruthy();
  fireEvent.click(within(original).getByRole('button', { name: '9379' }));
  expect(push).toHaveBeenLastCalledWith('/orders/detail/10');
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索需求订单' }), { target: { value: 'CF9379' } });
  expect(screen.queryByRole('region', { name: '9379需求明细' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'CF9379' }));
  expect(push).toHaveBeenLastCalledWith('/after-sales?after_sale_id=5');
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索需求订单' }), { target: { value: '不存在' } });
  expect(screen.getByText('没有匹配的需求订单')).toBeTruthy();
  push.mockRestore();
});

test('has explicit disabled empty state when no order needs this variant', () => {
  render(React.createElement(InventoryDemandOrders, { title: '磁吸指环', variants: [variant({})] }));
  expect((screen.getByRole('button', { name: '暂无需求订单' }) as HTMLButtonElement).disabled).toBe(true);
});

test('paginates large demands locally and searches across all pages', async () => {
  const many = variant({ demand_orders: Array.from({ length: 25 }, (_, index) => ({
    order_id: index + 1, order_number: String(10000 + index), quantity: 1,
  })) });
  render(React.createElement(InventoryDemandOrders, { title: '磁吸指环', variants: [many] }));
  fireEvent.click(screen.getByRole('button', { name: '查看需求订单（25 单）' }));
  expect(await screen.findByText('25 单 · 共 25 件需求')).toBeTruthy();
  expect(screen.getAllByRole('region')).toHaveLength(20);
  expect(screen.queryByRole('button', { name: '10024' })).toBeNull();
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索需求订单' }), { target: { value: '10024' } });
  expect(screen.getByRole('button', { name: '10024' })).toBeTruthy();
});
