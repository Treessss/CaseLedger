import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { lookupPackingList } from '@/services/shipping/api';
import PackingCheck from './index';

jest.mock('@ant-design/pro-components', () => ({ PageContainer: ({ children, extra }: any) => jest.requireActual('react').createElement('div', null, extra, children) }));
jest.mock('@umijs/max', () => ({ history: { push: jest.fn() }, useModel: () => ({ initialState: {}, setInitialState: jest.fn() }) }));
jest.mock('@/components', () => ({ PackingItems: () => null, RecipientInfo: () => null }));
jest.mock('@/utils/shop', () => ({ ALL_SHOPS: 'all', getSelectedShopId: () => 1 }));
jest.mock('@/services/shipping/api', () => ({ lookupPackingList: jest.fn() }));

beforeEach(() => {
  localStorage.clear();
  jest.clearAllMocks();
  jest.spyOn(window, 'scrollTo').mockImplementation(() => {});
  jest.mocked(lookupPackingList).mockImplementation(() => new Promise(() => {}));
});

test('products appear before status explanations and complete remarks are retained', async () => {
  jest.mocked(lookupPackingList).mockResolvedValue({success: true, data: {
    type: 'order', id: 1, reference: '12542', status: 'pending', labels: [], expected_parcel_count: 1,
    total_quantity: 2, items: [{title: 'Phone Case', variant_title: 'Blue / iPhone 16', quantity: 2, sku: '', image_url: ''}],
    order_note: '请放礼品卡', outbound: {ready: true, blockers: [], code_matches_label: true,
      already_scanned: false, expected_parcel_count: 1, scanned_parcel_count: 0},
  }});
  render(React.createElement(PackingCheck));
  await scan('4PX3003234093234CN');
  const products = document.querySelector('.packing-products-card');
  const note = screen.getByText('请放礼品卡');
  expect(products).toBeTruthy();
  expect(products?.querySelector('.packing-total-quantity strong')?.textContent).toBe('2');
  expect((products?.compareDocumentPosition(note) || 0) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  expect(screen.getByText('有备注，请核对下方备注')).toBeTruthy();
});

async function scan(label: string) {
  const input = screen.getByRole('textbox') as HTMLInputElement;
  await act(async () => {
    for (const key of label) fireEvent.keyDown(input, {key});
    fireEvent.keyDown(input, {key: 'Enter'});
  });
}

test('scanner mode is read-only, receives hardware keys and consumes scans immediately', async () => {
  render(React.createElement(PackingCheck));
  const input = screen.getByRole('textbox') as HTMLInputElement;
  expect(input.readOnly).toBe(true);
  expect(input.inputMode).toBe('none');
  await scan('4PX3003234093234CN');
  expect(lookupPackingList).toHaveBeenCalledWith(1, '4PX3003234093234CN');
  expect(input.value).toBe('');
  await scan('4PX3003234315688CN');
  expect(lookupPackingList).toHaveBeenLastCalledWith(1, '4PX3003234315688CN');
  expect(lookupPackingList).toHaveBeenCalledTimes(2);
});

test('an order number plus scanner Tab suffix queries without moving focus', async () => {
  render(React.createElement(PackingCheck));
  const input = screen.getByRole('textbox') as HTMLInputElement;
  input.focus();
  await act(async () => {
    for (const key of '12542') fireEvent.keyDown(input, {key});
    fireEvent.keyDown(input, {key: 'Tab'});
  });
  expect(lookupPackingList).toHaveBeenCalledWith(1, '12542');
  expect(document.activeElement).toBe(input);
});

test('manual mode allows normal typing and persists the preference', async () => {
  render(React.createElement(PackingCheck));
  fireEvent.click(screen.getByText('切换手动输入'));
  const input = screen.getByRole('textbox') as HTMLInputElement;
  expect(input.readOnly).toBe(false);
  expect(input.inputMode).toBe('text');
  expect(localStorage.getItem('caseledger_packing_scanner_mode')).toBe('manual');
  await act(async () => {
    fireEvent.change(input, {target: {value: '12542'}});
    fireEvent.keyDown(input, {key: 'Enter', keyCode: 13});
  });
  expect(lookupPackingList).toHaveBeenCalledWith(1, '12542');
});
