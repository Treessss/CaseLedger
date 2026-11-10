import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import InventoryQuantities from './InventoryQuantities';

test('shows distinct stock planning metrics and drill-down order references', async () => {
  render(<InventoryQuantities data={{incoming_quantity: 3, purchase_needed_quantity: 2,
    demand_quantity: 6, on_hand_quantity: 1,
    demand_orders: [{order_id: 10, order_number: 'CF9379', after_sale_id: 5, quantity: 1}]}} />);
  expect(screen.getByRole('button', {name: '已采购未到 3 件'})).toBeTruthy();
  expect(screen.getByRole('button', {name: '按型号还差 2 件'})).toBeTruthy();
  expect(screen.getByRole('button', {name: '在库 1 件'})).toBeTruthy();
  expect(screen.queryByText('打包锁定')).toBeNull();
  const hold = screen.getByRole('button', {name: '总需求 6 件，点击查看明细'});
  expect((hold as HTMLElement).style.height).toBe('44px');
  fireEvent.click(hold);
  expect(await screen.findByRole('button', {name: 'CF9379'})).toBeTruthy();
});
