import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { listInventoryMovements } from '@/services/inventory';
import Movements from './Movements';

jest.mock('@/services/inventory', () => ({ listInventoryMovements: jest.fn() }));
const requestMovements = listInventoryMovements as jest.Mock;
beforeEach(() => {
  jest.clearAllMocks();
  requestMovements.mockResolvedValue({ data: { period: {}, days: [],
    totals: { in_quantity: 0, out_quantity: 2 }, pagination: { total: 501, page: 1, per_page: 20, pages: 26 },
    movements: [{ id: 'scan:1:key', date: '2026-10-13', at: '2026-10-13T08:00:00Z',
      movement_type: 'scan_outbound', movement_type_label: '扫码出库', order_number: '12884',
      direction: 'out', quantity: 2, title: '磁吸指环', variant_title: 'Blue / iPhone 17',
      sku: '00123', image_url: '', source: '扫码出库 · 订单 12884' }] } });
});

test('shows origin and order, searches on submit and uses server pagination', async () => {
  render(React.createElement(Movements, { shopId: 1 }));
  await screen.findByText('12884');
  expect(screen.getByText('出入库明细（501 条）')).toBeTruthy();
  expect(requestMovements).toHaveBeenLastCalledWith(expect.objectContaining({ shop_id: 1, page: 1,
    per_page: 20, movement_type: 'all', direction: 'all' }));
  const search = screen.getByLabelText('搜索库存流水');
  fireEvent.change(search, { target: { value: '12884' } });
  fireEvent.keyDown(search, { key: 'Enter', code: 'Enter', charCode: 13 });
  await waitFor(() => expect(requestMovements).toHaveBeenLastCalledWith(expect.objectContaining({ search: '12884' })));
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '流水业务类型' }));
  fireEvent.click(await screen.findByTitle('手动调整'));
  await waitFor(() => expect(requestMovements).toHaveBeenLastCalledWith(expect.objectContaining({ movement_type: 'manual_adjustment', page: 1 })));
});
