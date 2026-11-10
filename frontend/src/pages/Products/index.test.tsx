import '@ant-design/v5-patch-for-react-19';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { listManagedProducts, saveProductNames } from '@/services/products/api';
import Products from './index';

jest.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children }: { children: string }) => jest.requireActual('react').createElement('div', null, children),
}));
jest.mock('@/components', () => ({ ShopRequiredNotice: () => null }));
jest.mock('@/utils/shop', () => ({ ALL_SHOPS: 'all', getSelectedShopId: () => 1 }));
jest.mock('@/services/products/api', () => ({ listManagedProducts: jest.fn(), saveProductNames: jest.fn() }));

test('saves user-entered names using the original product and color identities', async () => {
  jest.useFakeTimers();
  jest.mocked(listManagedProducts).mockResolvedValue({ success: true, data: { pagination: { total: 1 }, items: [{
    shop_id: 1, original_title: 'Case', chinese_name: '', display_title: 'Case', image_url: '', variant_count: 2,
    mapped: false, colors: [{ original_color: 'Rose Red', chinese_name: '', image_url: '', variant_count: 2 }],
  }] } });
  jest.mocked(saveProductNames).mockResolvedValue({ success: true });
  try {
    await act(async () => { render(React.createElement(Products)); });
    await act(async () => { jest.advanceTimersByTime(250); });
    expect(screen.getByText('Case')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '编辑中文' })); });
    fireEvent.change(screen.getByLabelText('商品中文名称'), { target: { value: '手机壳' } });
    fireEvent.change(screen.getByLabelText('Rose Red'), { target: { value: '玫红' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '保 存' })); });
    expect(saveProductNames).toHaveBeenCalledWith({ shop_id: 1, original_title: 'Case', chinese_name: '手机壳',
      colors: [{ original_color: 'Rose Red', chinese_name: '玫红' }] });
  } finally { jest.useRealTimers(); }
});
