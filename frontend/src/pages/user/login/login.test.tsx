// @ts-ignore
import { TestBrowser } from '@@/testBrowser';
import { render } from '@testing-library/react';
import React, { act } from 'react';

describe('Login Page', () => {
  it('renders the CaseLedger login form', async () => {
    const historyRef = React.createRef<any>();
    const rootContainer = render(
      <TestBrowser
        historyRef={historyRef}
        location={{
          pathname: '/user/login',
        }}
      />,
    );

    act(() => {
      historyRef.current?.push('/user/login');
    });

    // 品牌信息与副标题（校验页面已本地化为案例账本，而不是模板默认文案）
    expect(await rootContainer.findByText('案例账本')).toBeTruthy();
    expect(
      rootContainer.baseElement?.querySelector('.ant-pro-form-login-desc')
        ?.textContent,
    ).toBe('跨境电商订单成本管理系统');

    // 账号密码登录表单必须存在
    expect(rootContainer.container.querySelector('input')).toBeTruthy();
    expect(rootContainer.container.querySelector('input[type="password"]')).toBeTruthy();

    // 不应再暴露默认账号密码提示（历史安全问题）
    expect(rootContainer.baseElement?.textContent).not.toContain('qweasdzxc');

    rootContainer.unmount();
  });
});
