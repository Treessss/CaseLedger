import React from 'react';
import { render, screen } from '@testing-library/react';
import type { PackingList } from '@/services/shipping/api';
import RecipientInfo from './index';

const base: PackingList = { type: 'order', id: 1, reference: '13193', status: 'pending', labels: [], expected_parcel_count: 1, items: [], total_quantity: 0 };

test('shows full package recipient address, not the customer billing name', () => {
  render(<RecipientInfo result={{ ...base, customer_name: 'Billing customer', shipping_address: {
    name: 'Actual recipient', phone: '+1 555 0123', company: 'Example', address1: '123 Main St', address2: 'Unit 2', city: 'Toronto', province: 'Ontario', zip: 'M1A 1A1', country: 'Canada',
  } }} />);
  for (const value of ['Actual recipient', '+1 555 0123', 'Example', 'Toronto', 'Ontario', 'M1A 1A1', 'Canada']) expect(screen.getByText(value)).toBeTruthy();
  expect(screen.getByText(/123 Main St/).textContent).toBe('123 Main St\nUnit 2');
  expect(screen.queryByText('Billing customer')).toBeNull();
});

test('shows replacement address and safely renders missing fields', () => {
  const { rerender } = render(<RecipientInfo result={{ ...base, type: 'after_sale', reference: 'CF13193', shipping_address: { first_name: 'New', last_name: 'Recipient', country_code: 'US' } }} />);
  expect(screen.getByText('New Recipient')).toBeTruthy();
  expect(screen.getByText('CF13193 · 补发收货地址')).toBeTruthy();
  expect(screen.getByText('US')).toBeTruthy();
  rerender(<RecipientInfo result={base} />);
  expect(screen.getByText('暂无收货地址，请先核对订单信息。')).toBeTruthy();
});
