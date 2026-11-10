import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Input, message, Popconfirm, Select, Space, Tag, Typography } from 'antd';
import { confirmExchangeDiscount, generateDraft } from '@/services/mail/api';
import { getErrorMessage } from '@/utils/error';
import { formatBeijingTime } from '@/utils/timezone';

type MailDetail = import('@/services/mail/api').MailDetail;
type MailDraftPayload = import('@/services/mail/api').MailDraftPayload;
type MailSupportFacts = import('@/services/mail/api').MailSupportFacts;

const initialFacts: MailSupportFacts = {photos_verified: false, fault: 'unknown', damage_timing: 'unknown', received_date: ''};

export default function MailSupportReview({detail, guidance, onDraft}: {
  detail: MailDetail; guidance: string; onDraft: (draft: MailDraftPayload) => void;
}) {
  const analysis = detail.draft?.analysis;
  const [orderId, setOrderId] = useState<number | undefined>(analysis?.order_id || (detail.orders.length === 1 ? detail.orders[0].id : undefined));
  const [facts, setFacts] = useState<MailSupportFacts>(analysis?.facts || initialFacts);
  const [busy, setBusy] = useState('');
  const alive = useRef(true);
  const pending = useRef(false);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {
    if (analysis?.order_id && orderId === undefined) setOrderId(analysis.order_id);
  }, [analysis?.order_id]);
  const update = (value: Partial<MailSupportFacts>) => setFacts(prev => ({...prev, ...value}));
  const run = async (discount = false) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(discount ? 'discount' : 'draft');
    try {
      const result = discount ? await confirmExchangeDiscount(detail.mail.id)
        : await generateDraft(detail.mail.id, guidance, {order_id: orderId, facts});
      if (alive.current) {
        onDraft(result.data);
        message.success(discount ? '优惠已确认，草稿已更新；未发送邮件' : '分析与草稿已生成；未发送邮件');
      }
    } catch (error) {
      if (alive.current) message.error(getErrorMessage(error, '操作失败，请重试'));
    } finally {
      pending.current = false;
      if (alive.current) setBusy('');
    }
  };
  const changed = JSON.stringify(facts) !== JSON.stringify(analysis?.facts || initialFacts)
    || orderId !== analysis?.order_id || guidance !== (detail.draft?.guidance || '');
  return <Card size="small" title="AI 判断与人工核实" style={{marginBottom: 16}}>
    <Space direction="vertical" size={12} style={{width: '100%'}}>
      <Alert type="info" showIcon message="只生成草稿，不自动发送" description="照片与退款等须人工确认" />
      <Select aria-label="关联处理订单" allowClear value={orderId} onChange={setOrderId}
        placeholder="多个订单时请选择" style={{width: '100%'}}
        options={detail.orders.map(order => ({value: order.id, label: `${order.order_number} · ${order.warehouse_status === 'shipped' ? '已出库' : '未整单出库'}`}))} />
      <Checkbox checked={facts.photos_verified} onChange={event => update({photos_verified: event.target.checked})}>员工已核实照片证据</Checkbox>
      <Space wrap style={{width: '100%'}}>
        <Select aria-label="责任归属" value={facts.fault} onChange={fault => update({fault})} style={{minWidth: 180}}
          options={[{value: 'unknown', label: '责任尚未核实'}, {value: 'merchant', label: '我们发错'}, {value: 'customer', label: '客户下错'}]} />
        <Select aria-label="破损发生时间" value={facts.damage_timing} onChange={damage_timing => update({damage_timing})} style={{minWidth: 180}}
          options={[{value: 'unknown', label: '破损时间待核实'}, {value: 'on_arrival', label: '收到时即破损'}, {value: 'after_use', label: '使用一段时间后破损'}]} />
      </Space>
      <Space wrap>
        <Typography.Text>人工核实收到日期（可选）</Typography.Text>
        <Input aria-label="实际收到日期" type="date" value={facts.received_date} onChange={event => update({received_date: event.target.value})} style={{width: 180}} />
      </Space>
      <Button type="primary" loading={busy === 'draft'} disabled={Boolean(busy) || ['replied', 'ignored'].includes(detail.mail.status)} onClick={() => void run()} style={{minHeight: 44}}>综合往来、订单与物流生成草稿</Button>
      {detail.draft?.stale && <Alert type="warning" showIcon message="资料已更新，请重新生成草稿" />}
      {analysis?.tracking_updated && <Alert type="info" showIcon message="已显示最新轨迹；原回复正文未自动改写" />}
      {analysis?.intent && <>
        <Space wrap><Tag color="blue">{analysis.intent_label}</Tag><span>{analysis.language || '跟随客户语言'}</span><span>参考 {analysis.history_count || 0} 封往来</span>{analysis.order_number && <Tag>订单 {analysis.order_number}</Tag>}</Space>
        {detail.draft?.summary && <div><Typography.Text strong>摘要：</Typography.Text>{detail.draft.summary}</div>}
        {detail.draft?.reason && <div><Typography.Text strong>判断依据：</Typography.Text>{detail.draft.reason}</div>}
        {([['问题', analysis.issues], ['已提供证据线索', analysis.supplied_evidence], ['仍需核实 / 信息', [...(analysis.missing_evidence || []), ...(analysis.missing_information || [])]], ['待人工操作', analysis.actions]] as const).map(([label, lines]) => Boolean(lines?.length) && <div key={label}>
          <Typography.Text strong>{label}</Typography.Text>
          {[...new Set(lines || [])].map(line => <div key={`${label}-${line}`} style={{marginTop: 4, wordBreak: 'break-word'}}>• {line}</div>)}
        </div>)}
        {[...new Set(analysis.warnings || [])].map(warning => <Alert key={warning} type="warning" showIcon message={warning} />)}
        {analysis.tracking?.map((tracking, index) => <div key={`${tracking.number}-${index}`} style={{wordBreak: 'break-word'}}>
          <Typography.Text strong>{tracking.number}</Typography.Text>
          {tracking.url && <div><a href={tracking.url} target="_blank" rel="noreferrer">物流追踪链接</a></div>}
          {tracking.latest ? <div>最后轨迹：{formatBeijingTime(tracking.latest.happenedAt)} · {tracking.latest.message || tracking.latest.status}</div>
            : <Typography.Text type="warning">{tracking.tracking_error || '尚无已核验轨迹，请生成草稿时查询或人工核查承运商'}</Typography.Text>}
          {tracking.tracking_checked_at && <div><Typography.Text type="secondary">查询时间：{formatBeijingTime(tracking.tracking_checked_at)}</Typography.Text></div>}
          {tracking.tracking_warning && <div><Typography.Text type="warning">{tracking.tracking_warning}</Typography.Text></div>}
        </div>)}
        {analysis.discount && <Alert type="success" message={`已确认折扣码：${analysis.discount.code}`} description="全店 60% off，仅一次" />}
        {analysis.discount_eligible && <Popconfirm title="确认核实情况并创建 / 复用 Shopify 折扣码？" description="只建优惠，不发送；冲突会报错" onConfirm={() => run(true)} okText="确认优惠，不发送" cancelText="取消">
          <Button loading={busy === 'discount'} disabled={Boolean(busy) || Boolean(detail.draft?.stale) || changed} style={{minHeight: 44}}>人工确认 60% off 优惠并更新草稿</Button>
        </Popconfirm>}
        {changed && <Typography.Text type="warning">人工核实信息或订单已改变，请重新生成后再确认优惠。</Typography.Text>}
      </>}
    </Space>
  </Card>;
}
