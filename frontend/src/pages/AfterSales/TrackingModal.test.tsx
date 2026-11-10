import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { bindAfterSaleTracking } from '@/services/afterSales';
import TrackingModal from './TrackingModal';

jest.mock('@/services/afterSales', () => ({bindAfterSaleTracking: jest.fn()}));
const sale = {id: 1, shop_id: 1, order_id: 1, order_number: '9379', customer_reference: 'CF9379',
  status: 'ready' as const, kind: 'resend' as const, reason: 'test', items: [], costs: [], total_cost_cny: 0, created_at: '2026-10-15T00:00:00'};
beforeEach(() => {jest.clearAllMocks(); jest.mocked(bindAfterSaleTracking).mockReset();});

test('requires a waybill and saves a local-only binding with optional barcode', async () => {
  const onSaved = jest.fn();
  const bound = {...sale, tracking_number: '4PX12345678CN'};
  jest.mocked(bindAfterSaleTracking).mockResolvedValue({success: true, data: bound});
  render(React.createElement(TrackingModal, {record: sale, onClose: jest.fn(), onSaved}));
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: '保存绑定'}));});
  expect(bindAfterSaleTracking).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getAllByText('请输入补发物流单号').length).toBeGreaterThan(0));
  fireEvent.change(screen.getByLabelText('补发物流单号'), {target: {value: '4PX12345678CN'}});
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: '保存绑定'}));});
  expect(bindAfterSaleTracking).toHaveBeenCalledWith(1, {tracking_number: '4PX12345678CN',
    label_barcode: undefined, expected_tracking_number: null, expected_label_barcode: null, confirm_replace: false});
  expect(onSaved).toHaveBeenCalledWith(bound);
});

test('replacement clears inherited barcode, requires confirmation and keeps errors visible', async () => {
  const record = {...sale, tracking_number: 'OLD12345678', label_barcode: 'GV26CAA0U077777777'};
  jest.mocked(bindAfterSaleTracking).mockRejectedValue(new Error('物流绑定已被其他操作更新，请刷新售后单后重试'));
  render(React.createElement(TrackingModal, {record, onClose: jest.fn(), onSaved: jest.fn()}));
  fireEvent.change(screen.getByLabelText('补发物流单号'), {target: {value: 'NEW12345678'}});
  expect((screen.getByLabelText('实体面单条码（选填）') as HTMLInputElement).value).toBe('');
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: '保存绑定'}));});
  expect(bindAfterSaleTracking).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('checkbox', {name: '我已核对面单，确认替换现有绑定'}));
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: '保存绑定'}));});
  expect(bindAfterSaleTracking).toHaveBeenCalledWith(1, expect.objectContaining({
    tracking_number: 'NEW12345678', label_barcode: undefined, expected_tracking_number: record.tracking_number,
    expected_label_barcode: record.label_barcode, confirm_replace: true,
  }));
  expect(screen.getByText('物流绑定已被其他操作更新，请刷新售后单后重试')).toBeTruthy();
  fireEvent.change(screen.getByLabelText('补发物流单号'), {target: {value: 'NEW98765432'}});
  expect((screen.getByRole('checkbox', {name: '我已核对面单，确认替换现有绑定'}) as HTMLInputElement).checked).toBe(false);
});
