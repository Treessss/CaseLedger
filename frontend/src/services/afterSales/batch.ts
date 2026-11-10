import { runConcurrentBatches } from '@/utils/concurrentBatches';
import { getErrorMessage } from '@/utils/error';
import { bindAfterSaleFourPx, previewCfShopify, writebackCfShopify } from './api';
import type { CfFourPxRow } from './api';

export type CfBatchMode = 'bind' | 'overwrite';
export type CfBatchEntry = CfFourPxRow & {
  state: 'ready' | 'skipped' | 'failed' | 'success';
  detail: string;
  fulfillment_id?: string;
  expected_tracking_numbers?: string[];
};
export const cfPairKey = (row: CfFourPxRow) => JSON.stringify([row.customer_reference, row.tracking_number]);
export const canSelectCf = (row: CfFourPxRow) => Boolean(row.order_id && row.tracking_number
  && ['ready_to_bind', 'already_bound', 'original_order_only'].includes(row.status));

/** Prepare all rows read-only; never choose a random package or race two CFs on one original. */
export async function prepareCfBatch(
  rows: CfFourPxRow[], mode: CfBatchMode, shopId: number, date: string,
  isCurrent: () => boolean = () => true,
): Promise<CfBatchEntry[]> {
  const unique = Array.from(new Map(rows.map((row) => [cfPairKey(row), row])).values());
  const outcomes = await runConcurrentBatches(unique, 2, async (row): Promise<CfBatchEntry> => {
    const skip = (detail: string): CfBatchEntry => ({ ...row, state: 'skipped', detail });
    if (!isCurrent()) return skip('页面或店铺已切换，未处理');
    if (!canSelectCf(row)) return skip('对单结果异常，请重新查询');
    if (mode === 'bind') {
      if (!row.after_sale_id) return skip('没有售后单，不能只绑定售后');
      if (row.status === 'already_bound') return skip('售后已有相同单号，无需重复绑定');
      return { ...row, state: 'ready', detail: '只绑定售后，不更改原单、不通知客户' };
    }
    if (unique.filter((item) => item.order_id === row.order_id).length > 1) {
      return skip('同一原订单选了多条 CF，请逐条核对要覆盖的包裹');
    }
    const remote = (await previewCfShopify({ shop_id: shopId, date,
      customer_reference: row.customer_reference, tracking_number: row.tracking_number })).data;
    if (remote.order_id !== row.order_id || remote.tracking_number !== row.tracking_number
      || remote.customer_reference !== row.customer_reference) return skip('匹配信息已变化，请重新查询');
    if (remote.fulfillments.length !== 1) return skip('原单有多个包裹，请使用单条操作选择具体包裹');
    const fulfillment = remote.fulfillments[0];
    return { ...row, state: 'ready', fulfillment_id: fulfillment.id,
      expected_tracking_numbers: [...fulfillment.tracking_numbers],
      detail: fulfillment.tracking_numbers.length === 1 && fulfillment.tracking_numbers[0] === row.tracking_number
        ? 'Shopify 已有相同单号；只核对绑定，不重复通知'
        : '覆盖此包裹的原单号，并请求 Shopify 通知客户' };
  });
  return outcomes.map((outcome, index) => outcome.status === 'fulfilled' ? outcome.value
    : { ...unique[index], state: 'failed', detail: getErrorMessage(outcome.reason, '预览失败，请重新核对') });
}

/** Small independent requests avoid a single long-running batch timeout. Never auto-retry writes. */
export async function executeCfBatch(
  entries: CfBatchEntry[], mode: CfBatchMode, shopId: number, date: string,
  onResult: (entry: CfBatchEntry) => void,
  isCurrent: () => boolean = () => true,
): Promise<void> {
  await runConcurrentBatches(entries.filter((entry) => entry.state === 'ready'), 2, async (entry) => {
    let result: CfBatchEntry;
    try {
      if (!isCurrent()) result = { ...entry, state: 'skipped', detail: '页面或店铺已切换，未处理' };
      else if (mode === 'bind' && entry.after_sale_id) {
        await bindAfterSaleFourPx(entry.after_sale_id, date, entry.tracking_number);
        result = { ...entry, state: 'success', detail: '售后已绑定；原订单不变' };
      } else if (mode === 'overwrite' && entry.fulfillment_id && entry.expected_tracking_numbers) {
        const response = await writebackCfShopify({ shop_id: shopId, date,
          customer_reference: entry.customer_reference, tracking_number: entry.tracking_number,
          confirm_overwrite: true, fulfillment_id: entry.fulfillment_id,
          expected_tracking_numbers: entry.expected_tracking_numbers });
        result = { ...entry, state: 'success', detail: response.data.customer_notification_requested
          ? '已覆盖原单，已请求 Shopify 通知客户' : '单号已一致，未重复通知客户' };
      } else result = { ...entry, state: 'failed', detail: '确认信息不完整，请重新预览' };
    } catch (error) {
      result = { ...entry, state: 'failed', detail: getErrorMessage(error,
        '请求未确认完成；请先核对 Shopify，再刷新预览，不要直接重复回填') };
    }
    onResult(result);
  });
}
