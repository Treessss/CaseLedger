import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PackingControls from './PackingControls';
import { startPacking, cancelPacking, setPackingPriority } from '@/services/shipping/api';

jest.mock('@/services/shipping/api', () => ({
  startPacking: jest.fn(), cancelPacking: jest.fn(), setPackingPriority: jest.fn(),
}));
const result = {type: 'order' as const, id: 12, reference: '12004', status: 'pending',
  labels: [], items: [], expected_parcel_count: 1, total_quantity: 1};
beforeEach(() => jest.clearAllMocks());

test('lookup alone never locks stock; double click starts only one request', async () => {
  let finish: (value: {success: boolean; data: {id: number; state: string; expires_at: string}}) => void = () => {};
  jest.mocked(startPacking).mockImplementation(() => new Promise((resolve) => {finish = resolve;}));
  const changed = jest.fn();
  render(React.createElement(PackingControls, {shopId: 1, result, onChanged: changed}));
  expect(startPacking).not.toHaveBeenCalled();
  const button = screen.getByRole('button', {name: '开始装箱核对'});
  fireEvent.click(button);
  fireEvent.click(button);
  expect(startPacking).toHaveBeenCalledTimes(1);
  expect(startPacking).toHaveBeenCalledWith({shop_id: 1, kind: 'order', id: 12});
  finish({success: true, data: {id: 1, state: 'packing', expires_at: '2026-10-14T00:00:00Z'}});
  await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
});

test('hard hold offers cancellation and normal order priority can be toggled', async () => {
  jest.mocked(cancelPacking).mockResolvedValue({success: true, data: {id: 1, state: 'cancelled', expires_at: '2026-10-14T00:00:00Z'}});
  jest.mocked(setPackingPriority).mockResolvedValue({success: true, data: {id: 12, priority: 0}});
  render(React.createElement(PackingControls, {shopId: 1, result: {...result, priority: 100,
    packing: {id: 1, state: 'packing', expires_at: '2026-10-14T00:00:00Z'}}, onChanged: () => {}}));
  expect(screen.queryByRole('button', {name: '开始装箱核对'})).toBeNull();
  fireEvent.click(screen.getByRole('button', {name: '取消加急'}));
  await waitFor(() => expect(setPackingPriority).toHaveBeenCalledWith({shop_id: 1, kind: 'order', id: 12}, 0));
  await waitFor(() => expect(screen.getByRole('button', {name: '取消打包'}).hasAttribute('disabled')).toBe(false));
  fireEvent.click(screen.getByRole('button', {name: '取消打包'}));
  await waitFor(() => expect(cancelPacking).toHaveBeenCalledTimes(1));
});

test('completed packages have no packing or priority controls', () => {
  render(React.createElement(PackingControls, {shopId: 1, result: {...result, status: 'shipped'}, onChanged: () => {}}));
  expect(screen.queryByRole('button')).toBeNull();
});
