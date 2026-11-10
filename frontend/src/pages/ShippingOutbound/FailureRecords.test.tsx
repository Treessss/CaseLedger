import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';
import { listOutboundFailures } from '@/services/shipping/api';
import FailureRecords from './FailureRecords';

jest.mock('@umijs/max', () => ({ history: { push: jest.fn() } }));
jest.mock('@/services/shipping/api', () => ({ listOutboundFailures: jest.fn() }));

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(listOutboundFailures).mockResolvedValue({ success: true, data: {
    date: '2026-10-15', categories: { stock_shortage: '库存不足', label_error: '面单不齐或不匹配' },
    summary: { stock_shortage: { records: 1, attempts: 2 } }, pagination: { page: 1, per_page: 20, total: 1 },
    records: [{ id: 1, shop_id: 1, scan_code: 'GV-CF-BLOCKED', category: 'stock_shortage', reason: '实际库存不足：缺 1 件',
      order_id: 2, after_sale_id: 3, order_number: 'CF9379', attempts: 2, business_date: '2026-10-15',
      created_at: '2026-10-14T16:01:00', last_seen_at: '2026-10-14T16:05:00', operator: 'ml' }],
  } });
});

test('loads daily shop-scoped records only on demand and supports searchable CF links', async () => {
  render(React.createElement(FailureRecords, { shopId: 1 }));
  expect(listOutboundFailures).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '出库异常记录' }));
  expect(await screen.findByRole('button', { name: 'CF9379' })).toBeTruthy();
  expect(screen.getByText('库存不足：1 条 / 2 次')).toBeTruthy();
  expect(screen.getByText('最近操作人：ml')).toBeTruthy();
  expect(screen.getByText(/2026-10-15 00:01:00/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'CF9379' }));
  expect(history.push).toHaveBeenCalledWith('/after-sales?after_sale_id=3');
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索异常单号' }), { target: { value: 'CF9379' } });
  await waitFor(() => expect(listOutboundFailures).toHaveBeenLastCalledWith(expect.objectContaining({ shop_id: 1, search: 'CF9379', page: 1 })));
});

test('reports errors without silently displaying an empty success list', async () => {
  jest.mocked(listOutboundFailures).mockRejectedValueOnce(new Error('服务暂时不可用'));
  render(React.createElement(FailureRecords, { shopId: 1 }));
  fireEvent.click(screen.getByRole('button', { name: '出库异常记录' }));
  expect(await screen.findByText('服务暂时不可用')).toBeTruthy();
  expect(screen.queryByText('当天没有此类出库异常')).toBeNull();
});
