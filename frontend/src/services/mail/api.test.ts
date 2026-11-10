import { request } from '@umijs/max';
import { getMail, ignoreMail, listMails, sendMailReply } from './api';

jest.mock('@umijs/max', () => ({ request: jest.fn().mockResolvedValue({ success: true }) }));

describe('mailbox shop scope', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    localStorage.setItem('caseledger_selected_shop', '2');
  });

  it('scopes both reads and writes to the selected shop', async () => {
    await listMails({ page: 1 });
    await getMail('message');
    await ignoreMail('message');
    await sendMailReply('message');
    const calls = (request as jest.Mock).mock.calls;
    expect(calls).toHaveLength(4);
    calls.forEach(([, options]) => expect(options.params.shop_id).toBe('2'));
    expect(calls[2][1].data).toEqual({ ignored: true });
  });

  it('does not retain the previous shop after switching', async () => {
    await getMail('message');
    localStorage.setItem('caseledger_selected_shop', '3');
    await getMail('message');
    expect((request as jest.Mock).mock.calls[1][1].params.shop_id).toBe('3');
  });
});
