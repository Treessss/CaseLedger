import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Logistics from './index';
import { listTracking, refreshTracking, reconcileTracking, setTrackingHandled } from '@/services/logistics/api';

jest.mock('@umijs/max', () => ({useAccess: () => ({trackingForceOutbound: true})}));
jest.mock('@ant-design/pro-components', () => ({PageContainer: ({children}: {children: any}) => children}));
jest.mock('@/utils/shop', () => ({ALL_SHOPS: 'all', getSelectedShopId: () => '1'}));
jest.mock('@/services/logistics/api', () => ({listTracking: jest.fn(), refreshTracking: jest.fn(),
  reconcileTracking: jest.fn(), listTrackingAudits: jest.fn(), setTrackingHandled: jest.fn()}));

const listing = jest.mocked(listTracking);
const refreshing = jest.mocked(refreshTracking);
const reconciling = jest.mocked(reconcileTracking);
const response = {success: true, data: {items: [], counts: {preinfo: 1, pending: 2, overdue: 3},
  total: 0, page: 1, per_page: 20, coverage: {total: 10, checked: 8}}};

beforeEach(() => {
  jest.clearAllMocks();
  listing.mockResolvedValue(response);
  refreshing.mockResolvedValue({success: true, data: {id: 'job', scope: 'all', status: 'queued',
    total: 10, done: 0, failed: 0, updated_at: '2026-10-15T00:00:00'}});
});

test('opening and switching tabs only reads stored results; no outbound or carrier refresh', async () => {
  render(React.createElement(Logistics));
  await screen.findByText(/已核查 8\/10 个物流号/);
  fireEvent.click(screen.getByRole('tab', {name: '未出库 · 已有运输轨迹（2）'}));
  await waitFor(() => expect(listing).toHaveBeenLastCalledWith(expect.objectContaining({shop_id: 1, tab: 'pending'})));
  expect(refreshing).not.toHaveBeenCalled();
  expect(reconciling).not.toHaveBeenCalled();
});

test('explicit refresh submits a background job and returns to normal page', async () => {
  render(React.createElement(Logistics));
  await screen.findByText(/已核查 8\/10 个物流号/);
  fireEvent.click(screen.getByRole('button', {name: '更新全部到期运单'}));
  await waitFor(() => expect(refreshing).toHaveBeenCalledWith(1, 'due', undefined));
  await waitFor(() => expect(listing).toHaveBeenCalledTimes(2));
  expect(reconciling).not.toHaveBeenCalled();
});

test('history does not block due refresh and forced refresh is limited to one selected number', async () => {
  listing.mockResolvedValue({success: true, data: {...response.data,
    history_job: {id: 'history', scope: 'history', status: 'running', total: 100, done: 20, failed: 0, updated_at: ''},
    items: [{id: 7, order_id: 3, order_number: '10000', number: '4PXTEST', events: [],
      event_count: 1, outbound: true, observing: false, url: 'https://track.4px.com/#/result/0/4PXTEST'}]}});
  render(React.createElement(Logistics));
  await screen.findByText('10000');
  expect((screen.getByRole('button', {name: '更新全部到期运单'}) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole('button', {name: '强制刷新此单'}));
  await waitFor(() => expect(refreshing).toHaveBeenCalledWith(1, 'single', 7));
  expect(reconciling).not.toHaveBeenCalled();
});

test('overdue defaults to unhandled and staff can mark, filter, and reopen without outbound or carrier writes', async () => {
  let handled = false;
  listing.mockImplementation(async params => {
    const row = {id: 7, order_id: 3, order_number: '10000', number: '4PXTEST', events: [],
      event_count: 2, outbound: true, observing: false, url: 'https://track.4px.com/#/result/0/4PXTEST',
      handled, handled_by_name: handled ? 'staff' : undefined};
    const show = params.tab === 'overdue' && (params.handling === 'all' || (params.handling === 'handled') === handled);
    return {success: true, data: {...response.data, total: show ? 1 : 0, items: show ? [row] : [],
      handling_counts: {handled: handled ? 1 : 0, unhandled: handled ? 0 : 1}}};
  });
  jest.mocked(setTrackingHandled).mockImplementation(async (_shop, id, value) => {
    handled = value;
    return {success: true, data: {id, handled}};
  });
  render(React.createElement(Logistics));
  await screen.findByText(/已核查 8\/10 个物流号/);
  fireEvent.click(screen.getByRole('tab', {name: '预报满15天 · 未送达（3）'}));
  await screen.findByRole('button', {name: '标记已处理'});
  expect(listing).toHaveBeenLastCalledWith(expect.objectContaining({handling: 'unhandled'}));
  fireEvent.click(screen.getByRole('button', {name: '标记已处理'}));
  await waitFor(() => expect(setTrackingHandled).toHaveBeenCalledWith(1, 7, true));
  await waitFor(() => expect(screen.queryByRole('button', {name: '标记已处理'})).toBeNull());
  fireEvent.click(screen.getByRole('radio', {name: '已处理（1）'}));
  await screen.findByRole('button', {name: '恢复未处理'});
  expect(listing).toHaveBeenLastCalledWith(expect.objectContaining({handling: 'handled'}));
  expect(screen.getByText('处理人：staff')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name: '恢复未处理'}));
  await waitFor(() => expect(setTrackingHandled).toHaveBeenCalledWith(1, 7, false));
  expect(refreshing).not.toHaveBeenCalled();
  expect(reconciling).not.toHaveBeenCalled();
});

test('preinfo splits 24h and both groups can be marked handled', async () => {
  let handled = false;
  listing.mockImplementation(async params => {
    const recent = params.age !== 'beyond24h';
    const row = {id: 9, order_id: 4, order_number: '10100', number: '4PX24H', events: [],
      event_count: 1, outbound: true, observing: recent, url: 'https://track.4px.com/#/result/0/4PX24H',
      handled};
    const visible = params.handling === 'all' || (params.handling === 'handled') === handled;
    return {success: true, data: {...response.data, total: visible ? 1 : 0, items: visible ? [row] : [],
      preinfo_age_counts: {within24h: 1, beyond24h: 1},
      handling_counts: {handled: handled ? 1 : 0, unhandled: handled ? 0 : 1}}};
  });
  jest.mocked(setTrackingHandled).mockImplementation(async (_shop, id, value) => {
    handled = value;
    return {success: true, data: {id, handled}};
  });
  render(React.createElement(Logistics));
  await screen.findByRole('radio', {name: '出库24小时内（1）'});
  expect(listing).toHaveBeenLastCalledWith(expect.objectContaining({tab: 'preinfo', age: 'within24h'}));
  await screen.findByRole('button', {name: '标记已处理'});
  fireEvent.click(screen.getByRole('radio', {name: '出库24小时以外（1）'}));
  await waitFor(() => expect(listing).toHaveBeenLastCalledWith(expect.objectContaining({age: 'beyond24h'})));
  await screen.findByRole('button', {name: '标记已处理'});
  fireEvent.click(screen.getByRole('button', {name: '标记已处理'}));
  await waitFor(() => expect(setTrackingHandled).toHaveBeenCalledWith(1, 9, true));
  await waitFor(() => expect(screen.queryByRole('button', {name: '标记已处理'})).toBeNull());
});
