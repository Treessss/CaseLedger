import { request } from '@umijs/max';
import { downloadPurchaseListExcel } from './api';

jest.mock('@umijs/max', () => ({request: jest.fn()}));
beforeEach(() => {jest.clearAllMocks();});

test('single-category Excel sends the original one-based product index', async () => {
  jest.mocked(request).mockResolvedValue(new Blob(['workbook']));
  await downloadPurchaseListExcel(37, 2);
  expect(request).toHaveBeenCalledWith('/api/purchase-lists/37/excel', expect.objectContaining({
    method: 'GET', responseType: 'blob', params: {product_index: 2},
  }));
});

test('whole-list Excel remains available without a category filter', async () => {
  await downloadPurchaseListExcel(37);
  expect(request).toHaveBeenCalledWith('/api/purchase-lists/37/excel', expect.objectContaining({params: undefined}));
});
