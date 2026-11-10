import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { listRecentOutbound, listOutboundFailures, lookupPackingList, scanShippingLabel, undoOutbound } from '@/services/shipping/api';
import ShippingOutbound from './index';

jest.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children, extra }: any) => jest.requireActual('react').createElement('div', null, extra, children),
}));
jest.mock('@/components', () => ({
  PackingItems: () => null,
  ShopRequiredNotice: () => null,
  RecipientInfo: jest.requireActual('@/components/RecipientInfo').default,
}));
jest.mock('@umijs/max', () => ({useModel: () => ({initialState: {packingFocus: true}, setInitialState: jest.fn()})}));
jest.mock('@/utils/shop', () => ({ ALL_SHOPS: 'all', getSelectedShopId: () => '1' }));
jest.mock('@/services/shipping/api', () => ({
  listRecentOutbound: jest.fn(), listOutboundFailures: jest.fn(), lookupPackingList: jest.fn(),
  scanShippingLabel: jest.fn(), undoOutbound: jest.fn(),
}));

const lookup = jest.mocked(lookupPackingList);
const label = '4PX3003234093234CN';
const nextLabel = '4PX3003234315688CN';
const data = {
  type: 'order' as const, id: 1, reference: '12004', status: 'pending',
  labels: [], expected_parcel_count: 1, items: [], total_quantity: 1,
  outbound: { ready: true, blockers: [], code_matches_label: true, already_scanned: false,
    expected_parcel_count: 1, scanned_parcel_count: 0 },
};

beforeEach(() => {
  localStorage.clear();
  jest.useFakeTimers();
  jest.clearAllMocks();
  lookup.mockReset();
  jest.mocked(scanShippingLabel).mockReset();
  jest.mocked(listRecentOutbound).mockResolvedValue({ success: true, data: { events: [] } });
  lookup.mockResolvedValue({ success: true, data });
  jest.mocked(scanShippingLabel).mockImplementation(() => new Promise(() => {}));
});
afterEach(() => { jest.useRealTimers(); });

test('tablet scanner avoids the soft keyboard, consumes Tab scans and shows today goods', async () => {
  lookup.mockResolvedValue({success: true, data: {...data, outbound: {...data.outbound, code_matches_label: false}}});
  jest.mocked(listRecentOutbound).mockResolvedValue({success: true, data: {events: [], today: {date: '2026-10-12', quantity: 38, total_orders: 12}}});
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  const input = screen.getByRole('textbox', {name: '扫码枪输入（不弹键盘）'}) as HTMLInputElement;
  expect(input.readOnly).toBe(true);
  expect(input.inputMode).toBe('none');
  fireEvent.click(screen.getByRole('button', {name: /显示菜单/}));
  expect(localStorage.getItem('caseledger_packing_focus')).toBe('0');
  expect(document.querySelector('.packing-total-quantity strong')?.textContent).toBe('12');
  expect(document.querySelectorAll('.packing-total-quantity strong')[1]?.textContent).toBe('38');
  await act(async () => {
    for (const key of '12004') fireEvent.keyDown(input, {key});
    fireEvent.keyDown(input, {key: 'Tab'});
  });
  expect(lookup).toHaveBeenCalledWith(1, '12004');
  expect(input.value).toBe('');
  fireEvent.click(screen.getByRole('button', {name: '切换手动输入'}));
  expect(input.readOnly).toBe(false);
});

async function scan(value: string) {
  await act(async () => {
    fireEvent.change(screen.getByPlaceholderText('扫描面单条码'), { target: { value } });
  });
}

test('clears immediately and queues the next label during a slow authoritative scan', async () => {
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan(label);
  expect((screen.getByPlaceholderText('扫描面单条码') as HTMLInputElement).value).toBe('');
  await scan(nextLabel);
  expect(lookup).not.toHaveBeenCalled();
  expect(scanShippingLabel).toHaveBeenCalledTimes(1);
  expect(screen.getByText('正在确认出库；等待处理 1 张面单')).toBeTruthy();
});

test('failed scan is cleared, keeps the failed label visible and allows another scan', async () => {
  jest.mocked(scanShippingLabel).mockRejectedValueOnce(new Error('not found'));
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan(label);
  expect((screen.getByPlaceholderText('扫描面单条码') as HTMLInputElement).value).toBe('');
  expect(screen.getByText(/4PX3003234093234CN：/)).toBeTruthy();
  await scan(nextLabel);
  expect(scanShippingLabel).toHaveBeenCalledWith(1, nextLabel);
});

test('one scan ships and repeated scans never trigger duplicate shipping', async () => {
  jest.mocked(scanShippingLabel).mockImplementation(() => new Promise(() => {}));
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan(label);
  await scan(label);
  expect(lookup).not.toHaveBeenCalled();
  expect(scanShippingLabel).toHaveBeenCalledTimes(1);
  await act(async () => { jest.advanceTimersByTime(1200); });
  await scan(label);
  expect(scanShippingLabel).toHaveBeenCalledTimes(1);
  await act(async () => { jest.advanceTimersByTime(1200); });
  await scan(label);
  expect(scanShippingLabel).toHaveBeenCalledTimes(1);
});

test('a pasted pair of 4PX labels uses only the last label and ships once', async () => {
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan(label + nextLabel);
  expect(lookup).not.toHaveBeenCalled();
  expect(scanShippingLabel).toHaveBeenCalledWith(1, nextLabel);
});

test('an order number only looks up the list and does not ship', async () => {
  lookup.mockResolvedValue({ success: true, data: { ...data, outbound: { ...data.outbound, code_matches_label: false } } });
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan('12004');
  await act(async () => { jest.advanceTimersByTime(1000); });
  expect(scanShippingLabel).not.toHaveBeenCalled();
});

test('GV hardware barcode submits on pause without truncation or Enter', async () => {
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  const input = screen.getByRole('textbox', {name: '扫码枪输入（不弹键盘）'});
  await act(async () => {
    for (const key of 'GV26CAA0U076833783') fireEvent.keyDown(input, {key});
  });
  expect(lookup).not.toHaveBeenCalled();
  await act(async () => { jest.advanceTimersByTime(120); });
  expect(lookup).not.toHaveBeenCalled();
  expect(scanShippingLabel).toHaveBeenCalledWith(1, 'GV26CAA0U076833783');
  expect((input as HTMLInputElement).value).toBe('');
});

test('already scanned parcels are rejected by the authoritative scan endpoint but still show goods and address', async () => {
  jest.mocked(scanShippingLabel).mockRejectedValueOnce(new Error('该包裹已扫码，禁止重复出库'));
  lookup.mockResolvedValue({ success: true, data: {
    ...data, status: 'shipped',
    shipping_address: {name: 'Actual recipient', phone: '+1 555 0123', address1: '123 Main St',
      city: 'Toronto', province: 'Ontario', zip: 'M1A 1A1', country: 'Canada'},
    items: [{title: 'Phone Case', variant_title: 'Blue / iPhone 16', sku: 'CASE-B', image_url: 'https://cdn.example/case.jpg', quantity: 1}],
    outbound: {...data.outbound, ready: false, already_scanned: true},
  } });
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan(label);
  await act(async () => {});
  expect(scanShippingLabel).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/4PX3003234093234CN：/)).toBeTruthy();
  expect(screen.getByText(/重复扫码已忽略（不记录异常）/)).toBeTruthy();
  // 已出库的包裹再扫一次：依然把清单查回来，现场要看商品图片和收货地址核对实物。
  expect(lookup).toHaveBeenCalledWith(1, label);
  expect(screen.getByText('这单已经出库了')).toBeTruthy();
  expect(screen.getByText(/应装商品（1 款 \/ 1 件）/)).toBeTruthy();
  expect(screen.getByText('收货信息核对')).toBeTruthy();
  expect(screen.getByText('Actual recipient')).toBeTruthy();
  expect(screen.getByText('123 Main St')).toBeTruthy();
  expect(listOutboundFailures).not.toHaveBeenCalled();
});

test('a partially scanned order shows how many parcels are still left', async () => {
  jest.mocked(scanShippingLabel).mockRejectedValueOnce(new Error('该包裹已扫码，禁止重复出库'));
  lookup.mockResolvedValue({ success: true, data: {
    ...data, expected_parcel_count: 2,
    outbound: {...data.outbound, ready: false, already_scanned: true, expected_parcel_count: 2, scanned_parcel_count: 1},
  } });
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan(label);
  await act(async () => {});
  expect(screen.getByText('这张面单已经扫过')).toBeTruthy();
  expect(screen.getByText(/已扫 1\/2 个包裹/)).toBeTruthy();
});

test('next scan starts without waiting for slow packing display or counter refresh', async () => {
  jest.mocked(scanShippingLabel).mockResolvedValue({success: true, data: {
    order_id: 1, order_number: '12004', warehouse_status: 'shipped',
    expected_parcel_count: 1, scanned_parcel_count: 1,
  }});
  lookup.mockImplementation(() => new Promise(() => {}));
  await act(async () => {render(React.createElement(ShippingOutbound));});
  await scan(label);
  await scan(nextLabel);
  expect(scanShippingLabel).toHaveBeenCalledTimes(2);
});

test('history paginates beyond 50, persists the page and can search and undo an older order', async () => {
  jest.mocked(listRecentOutbound).mockImplementation(async (_shopId, params) => ({
    success: true,
    data: {
      events: params?.search ? [{id: 1, order_id: 8, order_number: 'OLD12004', title: 'OLD12004',
        code: 'OLD-LABEL', created_at: '2026-10-01T12:00:00', undoable: true,
        kind: 'order' as const, status: 'shipped'}] : [],
      pagination: {page: params?.page || 1, per_page: params?.per_page || 20,
        total: params?.search ? 1 : 126, pages: params?.search ? 1 : 7},
    },
  }));
  jest.mocked(undoOutbound).mockResolvedValue({success: true, data: {}});
  const view = await act(async () => render(React.createElement(ShippingOutbound)));
  expect(screen.getByText('共 126 条出库记录')).toBeTruthy();
  await act(async () => {fireEvent.click(document.querySelector('.ant-pagination-next button') as Element);});
  expect(listRecentOutbound).toHaveBeenLastCalledWith(1, {page: 2, per_page: 20, search: ''});
  expect(localStorage.getItem('caseledger_pagination_v1:outbound-history:1')).toContain('"current":2');
  view.unmount();
  await act(async () => {render(React.createElement(ShippingOutbound));});
  expect(listRecentOutbound).toHaveBeenLastCalledWith(1, {page: 2, per_page: 20, search: ''});
  await act(async () => {
    const search = screen.getByRole('searchbox', {name: '搜索出库记录'});
    fireEvent.change(search, {target: {value: 'OLD-LABEL'}});
    fireEvent.keyDown(search, {key: 'Enter', code: 'Enter', keyCode: 13});
  });
  expect(listRecentOutbound).toHaveBeenLastCalledWith(1, {page: 1, per_page: 20, search: 'OLD-LABEL'});
  expect(screen.getByText('OLD12004')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: /撤\s*回/}));
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: '确认撤回'}));});
  expect(undoOutbound).toHaveBeenCalledWith({shop_id: 1, order_id: 8, reason: undefined});
});

test('queued labels drain in order and pending duplicates are ignored', async () => {
  let finish: (value: any) => void = () => {};
  jest.mocked(scanShippingLabel).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
  await act(async () => {render(React.createElement(ShippingOutbound));});
  await scan(label);
  await scan(nextLabel);
  await scan(nextLabel);
  expect(scanShippingLabel).toHaveBeenCalledTimes(1);
  await act(async () => {finish({success: true, data: {
    order_id: 1, order_number: '12004', warehouse_status: 'shipped',
    expected_parcel_count: 1, scanned_parcel_count: 1,
  }});});
  expect(jest.mocked(scanShippingLabel).mock.calls.map(args => args[1])).toEqual([label, nextLabel]);
});

test('a failed queued scan does not discard the following label', async () => {
  let fail: (reason: Error) => void = () => {};
  jest.mocked(scanShippingLabel).mockImplementationOnce(() => new Promise((_resolve, reject) => {fail = reject;}));
  await act(async () => {render(React.createElement(ShippingOutbound));});
  await scan(label);
  await scan(nextLabel);
  await act(async () => {fail(new Error('库存不足'));});
  expect(jest.mocked(scanShippingLabel).mock.calls.map(args => args[1])).toEqual([label, nextLabel]);
  expect(screen.getByText(/4PX3003234093234CN：/)).toBeTruthy();
});

test('blocked scans use one compact feedback instead of accumulating a failure list', async () => {
  jest.mocked(scanShippingLabel).mockRejectedValue({ response: { data: {
    message: '实际库存不足', scan_category: 'stock_shortage', scan_failure_recorded: true,
  } } });
  await act(async () => { render(React.createElement(ShippingOutbound)); });
  await scan(label);
  expect(screen.getByText(/已保存到出库异常记录/)).toBeTruthy();
  await scan(nextLabel);
  expect(screen.queryByText(new RegExp(`${label}：`))).toBeNull();
  expect(screen.getByText(new RegExp(`${nextLabel}：`))).toBeTruthy();
  expect(screen.queryByText('以下面单未确认成功，请核对出库状态后重新扫描')).toBeNull();
  expect(listOutboundFailures).not.toHaveBeenCalled();
  const clear = screen.getByRole('button', {name: /清空/}) as HTMLButtonElement;
  expect(clear.disabled).toBe(false);
  fireEvent.click(clear);
  expect(screen.queryByText(/已保存到出库异常记录/)).toBeNull();
});
