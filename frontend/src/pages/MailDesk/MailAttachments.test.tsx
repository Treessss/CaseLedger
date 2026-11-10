import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import MailAttachments from './MailAttachments';
import { listMailAttachments, downloadMailAttachment } from '@/services/mail/api';

jest.mock('@/services/mail/api', () => ({listMailAttachments: jest.fn(),downloadMailAttachment:jest.fn()}));
beforeEach(() => {
  jest.clearAllMocks();
  URL.createObjectURL = jest.fn(() => 'blob:test-image');
  URL.revokeObjectURL = jest.fn();
  jest.mocked(listMailAttachments).mockResolvedValue({success:true,data:[
    {id:'100',name:'photo.png',size:20,inline:true,image:true}]});
  jest.mocked(downloadMailAttachment).mockResolvedValue(new Blob(['image'],{type:'image/png'}));
});

test('loads metadata independently and downloads image only on preview click', async () => {
  const view=render(React.createElement(MailAttachments,{messageId:'mail-1'}));
  expect(await screen.findByText('photo.png')).toBeTruthy();
  expect(screen.getByText('正文内嵌')).toBeTruthy();
  expect(downloadMailAttachment).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'eye 预览图片'}));
  await waitFor(() => expect(downloadMailAttachment).toHaveBeenCalledWith('mail-1','100'));
  expect(await screen.findByAltText('photo.png')).toBeTruthy();
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-image');
});

test('historical thread attachments are not fetched until requested', async () => {
  render(React.createElement(MailAttachments,{messageId:'older-mail',autoLoad:false}));
  expect(listMailAttachments).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'查看本封附件'}));
  expect(await screen.findByText('photo.png')).toBeTruthy();
});
