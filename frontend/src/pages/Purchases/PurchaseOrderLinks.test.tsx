import '@ant-design/v5-patch-for-react-19';
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { history } from '@umijs/max';
import PurchaseOrderLinks from './PurchaseOrderLinks';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));

test('shows CF-only purchases and distinguishes removed history from current quantity', async () => {
  const purchase = { orders: [], after_sale_orders: [
    { id: 1, customer_reference: 'CF999', order_id: 9, order_number: '999', removed: false, deleted: false },
    { id: 43, customer_reference: 'CF12204', order_id: 11479, order_number: '12204', removed: true, deleted: true },
  ] };
  render(React.createElement(PurchaseOrderLinks, { purchase }));
  fireEvent.click(screen.getByRole('button', { name: /关联订单 1 单.*历史关联 1 单/ }));
  expect(await screen.findByText('CF999')).toBeTruthy();
  expect(screen.getByText('CF12204')).toBeTruthy();
  expect(screen.getByText('已移出采购 · 不计采购数量')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '原单 12204' }));
  expect(history.push).toHaveBeenCalledWith('/orders/detail/11479');
});

test('hides empty links', () => {
  render(React.createElement(PurchaseOrderLinks, { purchase: { orders: [], after_sale_orders: [] } }));
  expect(screen.queryByRole('button')).toBeNull();
});
