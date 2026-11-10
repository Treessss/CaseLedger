import React from 'react';
import { render, screen } from '@testing-library/react';
import type { AfterSale } from '@/services/afterSales';
import AfterSaleStates from './States';

const sale: AfterSale = { id: 1, shop_id: 1, order_id: 1, order_number: '9379', customer_reference: 'CF9379',
  kind: 'resend', reason: 'test', created_at: '2026-10-15T00:00:00', total_cost_cny: 0, costs: [],
  status: 'ready', procurement_status: 'not_started', stock_status: 'ready', items: [], stock_missing_quantity: 0 };

test('shared stock readiness never masquerades as procurement completion', () => {
  render(<AfterSaleStates record={sale} />);
  expect(screen.getByText('未发起采购')).toBeTruthy();
  expect(screen.getByText('现货可满足本单')).toBeTruthy();
  expect(screen.queryByText('商品到齐')).toBeNull();
  expect(screen.queryByText('已完成入库')).toBeNull();
});

test('completed procurement can coexist with a current stock shortage', () => {
  render(<AfterSaleStates record={{ ...sale, procurement_status: 'received', stock_status: 'missing', stock_missing_quantity: 2 }} />);
  expect(screen.getByText('已完成入库')).toBeTruthy();
  expect(screen.getByText('本单缺 2 件')).toBeTruthy();
});

test('in-progress procurement stays distinct from stock availability and outbound completion', () => {
  const { rerender } = render(<AfterSaleStates record={{ ...sale, procurement_status: 'partially_received', stock_status: 'missing', stock_missing_quantity: 1 }} />);
  expect(screen.getByText('分批入库中')).toBeTruthy();
  expect(screen.getByText('本单缺 1 件')).toBeTruthy();
  rerender(<AfterSaleStates record={{ ...sale, status: 'shipped', procurement_status: 'received', stock_status: 'shipped' }} />);
  expect(screen.getByText('已扫码出库')).toBeTruthy();
});
