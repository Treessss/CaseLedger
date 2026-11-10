import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import MailDraftJobs from './MailDraftJobs';
import {getDraftJob, startDraftJob, cancelDraftJob} from '@/services/mail/api';

jest.mock('@/services/mail/api', () => ({getDraftJob: jest.fn(), startDraftJob: jest.fn(), cancelDraftJob: jest.fn()}));
jest.mock('@/utils/shop', () => ({getSelectedShopId: () => 1}));
const job = {id: 'job', status: 'running', total: 10, done: 3, succeeded: 2, failed: 1, skipped: 0,
  failures: [{message_id: 'failed-mail', error: 'AI 输出截断'}]};
beforeEach(() => {
  jest.clearAllMocks();
  jest.mocked(getDraftJob).mockResolvedValue({data: job});
  jest.mocked(startDraftJob).mockResolvedValue({data: job});
  jest.mocked(cancelDraftJob).mockResolvedValue({data: {...job, status: 'cancelled'}});
});

test('restores durable progress after mounting and never starts generation automatically', async () => {
  const onOpen = jest.fn();
  render(React.createElement(MailDraftJobs, {ids: ['mail'], enabled: true, onOpen}));
  await screen.findByText('后台生成中');
  expect(startDraftJob).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('查看 1 封失败原因'));
  fireEvent.click(await screen.findByText('打开邮件'));
  expect(onOpen).toHaveBeenCalledWith('failed-mail');
  fireEvent.click(screen.getByText('停止后续生成'));
  await waitFor(() => expect(cancelDraftJob).toHaveBeenCalledWith('job'));
});

test('retry submits only failed task references', async () => {
  jest.mocked(getDraftJob).mockResolvedValue({data: {...job, status: 'completed'}});
  render(React.createElement(MailDraftJobs, {ids: ['mail'], enabled: true, onOpen: jest.fn()}));
  fireEvent.click(await screen.findByText('只重试失败和未完成项'));
  await waitFor(() => expect(startDraftJob).toHaveBeenCalledWith({retry_job_id: 'job'}));
});
