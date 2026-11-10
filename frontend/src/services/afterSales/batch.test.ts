import { bindAfterSaleFourPx, previewCfShopify, writebackCfShopify } from './api';
import { executeCfBatch, prepareCfBatch } from './batch';

type CfFourPxRow = import('./api').CfFourPxRow;

jest.mock('./api', () => ({ bindAfterSaleFourPx: jest.fn(), previewCfShopify: jest.fn(), writebackCfShopify: jest.fn() }));
const preview = previewCfShopify as jest.Mock;
const bind = bindAfterSaleFourPx as jest.Mock;
const write = writebackCfShopify as jest.Mock;
const row = (id: number, status = 'ready_to_bind'): CfFourPxRow => ({ order_id: id, after_sale_id: id,
  order_number: String(id), customer_reference: `CF${id}`, tracking_number: `4PX${id}`, status });
beforeEach(() => { jest.clearAllMocks(); });

test('bind-only skips already bound/missing/abnormal rows and deduplicates selections', async () => {
  const entries = await prepareCfBatch([row(1), row(1), row(2, 'already_bound'),
    { ...row(3, 'original_order_only'), after_sale_id: undefined }, row(4, 'tracking_conflict')], 'bind', 1, '2026-10-13');
  expect(entries.map((entry) => entry.state)).toEqual(['ready', 'skipped', 'skipped', 'skipped']);
  expect(preview).not.toHaveBeenCalled();
  const results: string[] = [];
  await executeCfBatch(entries, 'bind', 1, '2026-10-13', (entry) => results.push(entry.state));
  expect(bind).toHaveBeenCalledTimes(1);
  expect(bind).toHaveBeenCalledWith(1, '2026-10-13', '4PX1');
  expect(write).not.toHaveBeenCalled();
  expect(results).toEqual(['success']);
});

test('overwrite previews read-only and skips multiple packages, duplicate originals and failed previews', async () => {
  preview.mockImplementation(async ({ customer_reference }) => {
    if (customer_reference === 'CF3') throw new Error('preview failed');
    const source = row(customer_reference === 'CF1' ? 1 : 2);
    return { data: { ...source, fulfillments: customer_reference === 'CF1'
      ? [{ id: 'f1', tracking_numbers: ['OLD1'] }]
      : [{ id: 'f2', tracking_numbers: ['OLD2'] }, { id: 'f3', tracking_numbers: ['OLD3'] }] } };
  });
  const entries = await prepareCfBatch([row(1), row(2), row(3), row(4),
    { ...row(4), customer_reference: 'CF4-2', tracking_number: '4PX4-2' }], 'overwrite', 1, '2026-10-13');
  expect(entries.map((entry) => entry.state)).toEqual(['ready', 'skipped', 'failed', 'skipped', 'skipped']);
  expect(entries[0].expected_tracking_numbers).toEqual(['OLD1']);
  expect(preview).toHaveBeenCalledTimes(3);
  expect(write).not.toHaveBeenCalled();
  expect(bind).not.toHaveBeenCalled();
});

test('overwrite preserves expected tracking, reports failures independently and never auto retries', async () => {
  write.mockImplementation(async ({ customer_reference }) => {
    if (customer_reference === 'CF2') throw new Error('timeout');
    return { data: { customer_notification_requested: customer_reference !== 'CF3' } };
  });
  const entries = [1, 2, 3].map((id) => ({ ...row(id), state: 'ready' as const, detail: '',
    fulfillment_id: `f${id}`, expected_tracking_numbers: [`OLD${id}`] }));
  const results: Array<{ state: string; detail: string }> = [];
  await executeCfBatch(entries, 'overwrite', 7, '2026-10-13', (entry) => results.push(entry));
  expect(write).toHaveBeenCalledTimes(3);
  expect(write).toHaveBeenCalledWith(expect.objectContaining({ shop_id: 7, date: '2026-10-13',
    fulfillment_id: 'f1', expected_tracking_numbers: ['OLD1'], confirm_overwrite: true }));
  expect(results.filter((result) => result.state === 'success')).toHaveLength(2);
  expect(results.filter((result) => result.state === 'failed')).toHaveLength(1);
  expect(results.some((result) => result.detail.includes('未重复通知'))).toBe(true);
  expect(bind).not.toHaveBeenCalled();
});

test('context changes abort queued work and mismatched previews cannot be confirmed', async () => {
  preview.mockResolvedValue({ data: { ...row(99), fulfillments: [{ id: 'f99', tracking_numbers: [] }] } });
  expect((await prepareCfBatch([row(1)], 'overwrite', 1, '2026-10-13'))[0].state).toBe('skipped');
  const entry = { ...row(1), state: 'ready' as const, detail: '' };
  const results: string[] = [];
  await executeCfBatch([entry], 'bind', 1, '2026-10-13', (result) => results.push(result.state), () => false);
  expect(bind).not.toHaveBeenCalled();
  expect(results).toEqual(['skipped']);
});
