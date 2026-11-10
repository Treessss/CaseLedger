import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { Form } from 'antd';
import LegacyLoginInput from './LegacyLoginInput';

test('typing does not replace the focused native input or change its type', () => {
  const { rerender } = render(<Form><Form.Item name="username">
    <LegacyLoginInput aria-label="用户名" type="text" prefix={<span>账号</span>} />
  </Form.Item></Form>);
  const input = screen.getByLabelText('用户名') as HTMLInputElement;
  input.focus();
  for (const value of ['a', 'ad', 'admin']) {
    fireEvent.change(input, { target: { value } });
    expect(screen.getByLabelText('用户名')).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe(value);
  }
  rerender(<Form><Form.Item name="username">
    <LegacyLoginInput aria-label="用户名" type="text" prefix={<span>账号</span>} />
  </Form.Item></Form>);
  expect(screen.getByLabelText('用户名')).toBe(input);
  expect(document.activeElement).toBe(input);
});

test('password input stays password and disables text correction', () => {
  render(<LegacyLoginInput aria-label="密码" type="password" prefix={<span>密码</span>}
    autoComplete="current-password" value="test" readOnly />);
  const input = screen.getByLabelText('密码');
  expect(input.getAttribute('type')).toBe('password');
  expect(input.getAttribute('autocomplete')).toBe('current-password');
  expect(input.getAttribute('autocapitalize')).toBe('none');
});
