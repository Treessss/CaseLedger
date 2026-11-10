import { Button, message, Space, Tag, Typography } from 'antd';
import React, { useRef, useState } from 'react';
import { cancelPacking, setPackingPriority, startPacking, type PackingList } from '@/services/shipping/api';
import { getErrorMessage } from '@/utils/error';
import { formatBeijingTime } from '@/utils/timezone';

export default function PackingControls({shopId, result, onChanged, compact = false}: {
  shopId: number; result: PackingList; onChanged: () => void; compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const identity = `${shopId}:${result.type}:${result.id}`;
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const target = {shop_id: shopId, kind: result.type, id: result.id};
  const run = async (action: 'start' | 'cancel' | 'priority') => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      if (action === 'start') await startPacking(target);
      else if (action === 'cancel') await cancelPacking(target);
      else await setPackingPriority(target, result.priority ? 0 : 100);
      message.success(`${result.reference}：${action === 'start' ? '已开始核对，不占用库存' : action === 'cancel' ? '已取消核对任务' : '需求优先级已更新'}`);
      if (currentIdentity.current === identity) onChanged();
    } catch (error) {
      message.error(getErrorMessage(error, '打包操作失败，请刷新后核对'));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  if (result.outbound?.already_scanned || result.status === 'shipped') return null;
  if ((result.outbound?.scanned_parcel_count || 0) > 0) return <Tag color="blue">整单库存已扣减，继续扫码剩余包裹即可</Tag>;
  const canStart = result.type === 'after_sale' ? result.status === 'ready' : result.status === 'pending' && !result.cancelled;
  return <Space wrap style={{marginTop: compact ? 0 : 8}}>
    {result.packing ? <>
      <Tag color="blue">正在核对</Tag>
      {!compact && <Typography.Text type="secondary">核对记录有效至 {formatBeijingTime(result.packing.expires_at)}；不占用库存</Typography.Text>}
      <Button style={{minHeight: 44}} loading={busy} disabled={busy} onClick={() => void run('cancel')}>取消打包</Button>
    </> : <Button type="primary" style={{minHeight: 44}} disabled={!canStart || busy} loading={busy} onClick={() => void run('start')}>开始装箱核对</Button>}
    {result.type === 'order' && <Button style={{minHeight: 44}} disabled={busy} onClick={() => void run('priority')}>
      {result.priority ? '取消加急' : '优先发货'}
    </Button>}
    {!compact && !result.packing && <Typography.Text type="secondary">查询和开始核对都不占货；实际扫码时校验并扣减公共库存</Typography.Text>}
  </Space>;
}
