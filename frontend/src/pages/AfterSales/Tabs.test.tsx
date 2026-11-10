import '@ant-design/v5-patch-for-react-19';
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AfterSales from './index';
import { listAfterSales } from '@/services/afterSales';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@ant-design/pro-components', () => ({ PageContainer: ({ children }: { children: string }) => children }));
jest.mock('@/utils/shop', () => ({ ALL_SHOPS: 'all', getSelectedShopId: () => '1' }));
jest.mock('@/services/afterSales', () => ({ listAfterSales: jest.fn() }));

test('tabs show server counts and request independent workflow filters', async () => {
  window.history.replaceState(null, '', '/after-sales');
  jest.mocked(listAfterSales).mockResolvedValue({ success: true, data: { after_sales: [],
    counts: { all: 150, unprocessed: 30, unprocured: 8, shipped: 120 },
    pagination: { total: 150, page: 1, per_page: 20 } } });
  render(React.createElement(AfterSales));
  fireEvent.click(await screen.findByRole('tab', { name: '已扫码出库（120）' }));
  await waitFor(() => expect(listAfterSales).toHaveBeenLastCalledWith({ shop_id: 1, view: 'shipped', page: 1, per_page: 20 }));
  fireEvent.click(screen.getByRole('tab', { name: '未采购（8）' }));
  await waitFor(() => expect(listAfterSales).toHaveBeenLastCalledWith({ shop_id: 1, view: 'unprocured', page: 1, per_page: 20 }));
});
