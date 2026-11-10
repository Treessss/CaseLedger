import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import MailSupportReview from './MailSupportReview';
import { generateDraft, confirmExchangeDiscount } from '@/services/mail/api';
type MailDetail = import('@/services/mail/api').MailDetail;

jest.mock('@/services/mail/api', () => ({generateDraft: jest.fn(), confirmExchangeDiscount: jest.fn()}));

const facts = {photos_verified: false, fault: 'unknown' as const, damage_timing: 'unknown' as const, received_date: ''};
const detail = {
  mail: {id: 'test-mail', status: 'pending'},
  orders: [{id: 1, order_number: '10472', warehouse_status: 'pending'}],
  draft: {reply: 'Draft', guidance: '', safe: false, edited: false, stale: false,
    analysis: {intent: 'customer_wrong', intent_label: '客户选错', order_id: 1, discount_eligible: true, facts}},
  thread: [], send: null,
} as unknown as MailDetail;
const readyDraft = detail.draft as NonNullable<MailDetail['draft']>;

beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(generateDraft).mockResolvedValue({success: true, data: readyDraft});
  jest.mocked(confirmExchangeDiscount).mockResolvedValue({success: true, data: readyDraft});
});

test('drafting is explicit and never creates a discount on load or generation', async () => {
  const onDraft = jest.fn();
  render(React.createElement(MailSupportReview, {detail, guidance: '先核实', onDraft}));
  expect(generateDraft).not.toHaveBeenCalled();
  expect(confirmExchangeDiscount).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: '综合往来、订单与物流生成草稿'}));
  await waitFor(() => expect(generateDraft).toHaveBeenCalledWith('test-mail', '先核实', {order_id: 1, facts}));
  expect(confirmExchangeDiscount).not.toHaveBeenCalled();
  expect(onDraft).toHaveBeenCalledWith(detail.draft);
});

test('discount creation requires a second human confirmation and stale drafts cannot confirm', async () => {
  const view = render(React.createElement(MailSupportReview, {detail, guidance: '', onDraft: jest.fn()}));
  fireEvent.click(screen.getByRole('button', {name: '人工确认 60% off 优惠并更新草稿'}));
  expect(confirmExchangeDiscount).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole('button', {name: '确认优惠，不发送'}));
  await waitFor(() => expect(confirmExchangeDiscount).toHaveBeenCalledWith('test-mail'));
  view.unmount();
  render(React.createElement(MailSupportReview, {detail: {...detail, draft: {...readyDraft, stale: true}}, guidance: '', onDraft: jest.fn()}));
  expect((screen.getByRole('button', {name: '人工确认 60% off 优惠并更新草稿'}) as HTMLButtonElement).disabled).toBe(true);
});

test('changed evidence requires regenerating before confirming a discount', () => {
  render(React.createElement(MailSupportReview, {detail, guidance: '', onDraft: jest.fn()}));
  fireEvent.click(screen.getByRole('checkbox'));
  expect((screen.getByRole('button', {name: '人工确认 60% off 优惠并更新草稿'}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText('人工核实信息或订单已改变，请重新生成后再确认优惠。')).toBeTruthy();
});

test('shows current verified tracking and warns old reply is not rewritten', () => {
  const current = {...detail, draft: {...readyDraft, stale: true, analysis: {
    ...readyDraft.analysis, tracking_updated: true, tracking: [{number: '4PXEXACT',
      latest: {happenedAt: '2026-09-02T07:00:00Z', status: 'DELIVERED', message: 'Collected from pick up location'},
      tracking_checked_at: '2026-10-16T00:00:00Z', tracking_warning: '最近一次查询失败；当前显示上次成功查询的可靠轨迹'}],
  }}};
  render(React.createElement(MailSupportReview, {detail: current, guidance: '', onDraft: jest.fn()}));
  expect(screen.getByText(/Collected from pick up location/)).toBeTruthy();
  expect(screen.getByText(/原回复正文未自动改写/)).toBeTruthy();
  expect(screen.getByText(/查询时间/)).toBeTruthy();
  expect(screen.getByText(/最近一次查询失败/)).toBeTruthy();
  expect(screen.queryByText('尚未取得可靠轨迹')).toBeNull();
  expect(generateDraft).not.toHaveBeenCalled();
});

test('real missing tracking displays an explicit error instead of inventing a status', () => {
  const missing = {...detail, draft: {...readyDraft, analysis: {...readyDraft.analysis,
    tracking: [{number: '4PXUNKNOWN', tracking_error: '4PX 暂无可核验的轨迹记录'}]}}};
  render(React.createElement(MailSupportReview, {detail: missing, guidance: '', onDraft: jest.fn()}));
  expect(screen.getByText('4PX 暂无可核验的轨迹记录')).toBeTruthy();
  expect(screen.queryByText(/最后轨迹/)).toBeNull();
});
