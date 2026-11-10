import '@ant-design/v5-patch-for-react-19';
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Purchases from './index';
import { getPurchaseList, receivePurchaseList } from '@/services/purchases';

jest.setTimeout(60000);

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children }: { children: string }) => children,
  ProTable: () => null,
}));
jest.mock('@/components', () => ({ ShopRequiredNotice: () => null }));
jest.mock('@/utils/shop', () => ({ ALL_SHOPS: 'all', getSelectedShopId: () => '1' }));
jest.mock('@/services/purchases', () => ({ getPurchaseList: jest.fn(), receivePurchaseList: jest.fn() }));

const variant = { group_key: 'blue', title: 'Case', variant_title: 'Blue / iPhone 17 Pro',
  model_title: 'iPhone 17 Pro', sku: 'blue-17', vendor: '', image_url: '', quantity: 3, order_numbers: ['10000'] };
const detail = { id: 1, shop_id: 1, name: '清点测试', order_count: 1, item_count: 1,
  product_count: 1, color_count: 1, total_quantity: 3, status: 'ordered' as const, created_at: '', order_ids: [1],
  items: [variant], receipt: [], products: [{ group_key: 'case', title: 'Case', vendor: '', image_url: '',
    quantity: 3, variant_count: 1, color_count: 1, order_numbers: ['10000'], variants: [variant],
    colors: [{ name: 'Blue', quantity: 3, variant_count: 1, image_url: '', order_numbers: ['10000'], variants: [variant] }] }] };

beforeEach(() => {
  jest.clearAllMocks();
  window.history.replaceState(null, '', '/purchases?purchase_id=1');
  jest.mocked(getPurchaseList).mockResolvedValue({ success: true, data: detail });
  jest.mocked(receivePurchaseList).mockResolvedValue({ success: true, data: { ...detail, status: 'partially_received' } });
});

afterEach(() => { window.history.replaceState(null, '', '/'); });

test('marks remaining quantity missing and submits it separately without finalizing all deliveries', async () => {
  render(React.createElement(Purchases));
  fireEvent.click(await screen.findByRole('button', { name: '清点并确认入库' }, { timeout: 15000 }));
  fireEvent.change(await screen.findByLabelText('实到件数', {}, { timeout: 15000 }), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: '记录破损/少发' }));
  fireEvent.click(screen.getByRole('button', { name: '全部少发' }));
  expect((screen.getByLabelText('少发件数') as HTMLInputElement).value).toBe('2');
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对：实到 1 件、破损 0 件、少发 2 件/ }));
  fireEvent.click(screen.getByRole('button', { name: '保存本批入库' }));
  await waitFor(() => expect(receivePurchaseList).toHaveBeenCalledWith(1,
    [{ group_key: 'blue', received_quantity: 1, damaged_quantity: 0, missing_quantity: 2 }], false, expect.any(String)));
});

test('previously confirmed missing units are unavailable for later receipt and an all-missing batch is allowed', async () => {
  jest.mocked(getPurchaseList).mockResolvedValue({ success: true, data: { ...detail, status: 'partially_received',
    receipt: [{ group_key: 'blue', planned_quantity: 3, received_quantity: 1, damaged_quantity: 0,
      confirmed_missing_quantity: 1, pending_quantity: 1, missing_quantity: 2 }] } });
  render(React.createElement(Purchases));
  fireEvent.click(await screen.findByRole('button', { name: '继续清点到货' }, { timeout: 15000 }));
  fireEvent.click(await screen.findByRole('button', { name: '记录破损/少发' }));
  fireEvent.click(await screen.findByRole('button', { name: '全部少发' }));
  expect((screen.getByLabelText('少发件数') as HTMLInputElement).value).toBe('1');
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对：实到 0 件、破损 0 件、少发 1 件/ }));
  fireEvent.click(screen.getByRole('button', { name: '保存本批入库' }));
  await waitFor(() => expect(receivePurchaseList).toHaveBeenCalledWith(1,
    [{ group_key: 'blue', received_quantity: 0, damaged_quantity: 0, missing_quantity: 1 }], false, expect.any(String)));
});
