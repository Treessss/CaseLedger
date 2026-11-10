import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import {
  checkFourPxDeclaration,
  exportFourPxDeclaration,
  getFourPxDeclarationJob,
  getFourPxDeclarationSettings,
  listFourPxDeclarationJobs,
  planFourPxDeclaration,
  saveFourPxDeclarationSettings,
  startFourPxDeclarationJob,
  syncFourPxManualDeclarations,
} from '@/services/shipping/api';
import DeclarationCheck from './index';

jest.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children, extra }: any) => jest.requireActual('react').createElement('div', null, extra, children),
}));
jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
}));
jest.mock('@/components', () => ({ ShopRequiredNotice: () => null }));
jest.mock('@/utils/shop', () => ({ ALL_SHOPS: 'all', getSelectedShopId: () => '1' }));
jest.mock('@/services/shipping/api', () => ({
  checkFourPxDeclaration: jest.fn(),
  exportFourPxDeclaration: jest.fn(),
  getFourPxDeclarationJob: jest.fn(),
  getFourPxDeclarationSettings: jest.fn(),
  listFourPxDeclarationJobs: jest.fn(),
  planFourPxDeclaration: jest.fn(),
  saveFourPxDeclarationSettings: jest.fn(),
  startFourPxDeclarationJob: jest.fn(),
  syncFourPxManualDeclarations: jest.fn(),
}));

const check = jest.mocked(checkFourPxDeclaration);
const exportCsv = jest.mocked(exportFourPxDeclaration);
const planBatch = jest.mocked(planFourPxDeclaration);
const startJob = jest.mocked(startFourPxDeclarationJob);
const syncManual = jest.mocked(syncFourPxManualDeclarations);
const getJob = jest.mocked(getFourPxDeclarationJob);
const listJobs = jest.mocked(listFourPxDeclarationJobs);
const loadSettings = jest.mocked(getFourPxDeclarationSettings);
const saveSettings = jest.mocked(saveFourPxDeclarationSettings);

const row = (overrides: Record<string, unknown> = {}) => ({
  order_id: 1, order_number: '15950', country: 'US', country_name: '美国', postal_code: '01028',
  quantity: 1, special_pair: false,
  product: { key: 'light', code: 'S5058', name: '联邮通轻小件-普货', tracking: false },
  product_reason: '1–2 件普货走轻小件', weight_grams: 120, declared_value: 1.2, declared_currency: 'USD',
  declared_items: [{ name_cn: '手机壳', name_en: 'Phone case', quantity: 1, unit_price: 1.2, amount: 1.2 }],
  duty_type: 'U', ready: true, reason_group: null, blockers: [],
  items: [{ title: 'Phone Case', variant_title: 'Blue', sku: 'SKU-1', quantity: 1, unit_price: 12.99, image_url: '' }],
  recipient: { first_name: 'Jane', last_name: 'Doe', phone: '5551234567', street: '1 Main St', house_number: '' },
  payload_preview: { ref_no: '15950', parcel_list: [{ weight: 120 }] },
  ...overrides,
});

const blockedRow = row({
  order_id: 2, order_number: '15951', country: 'AU', country_name: '澳大利亚', postal_code: '2371',
  product: null, product_reason: '「AU」不在当前发货规则内，请人工处理',
  declared_value: 1.2, declared_items: [], ready: false, reason_group: 'address',
  blockers: ['收货地址需同时包含门牌号和街道名（4PX 要求数字+字母），请核对'],
  payload_preview: null, items: [],
  recipient: { first_name: 'Haelie', last_name: 'Donaldson', phone: '+61423104385', street: '81', house_number: '' },
});

const payload = {
  summary: { candidates: 12, ready: 10, blocked: 2 },
  pagination: { page: 1, per_page: 20, total: 10, pages: 1 },
  rules: {
    products: {
      light: { code: 'S5058', name: '联邮通轻小件-普货' },
      standard: { code: 'QC', name: '联邮通标准挂号-普货' },
      smart: { code: 'S5191', name: '联邮通智选产品-标准普货' },
      island: { code: 'S5118', name: '美国岛屿专线-普货' },
    },
    weight: { one_item: 120, two_items: 180, extra_item: 120 },
    declare: { currency: 'USD', note: 'US/AU 1.20、CA/GB/NZ 1.00', categories: [] },
    duty_type: 'U', special_title: 'gradient magnetic',
    reason_groups: { address: '地址问题', contact: '联系方式缺失' },
  },
  notices: [],
  orders: [row()],
};

const blockedPayload = {
  ...payload,
  summary: { candidates: 12, ready: 10, blocked: 2, groups: { address: 2 } },
  pagination: { page: 1, per_page: 20, total: 2, pages: 1 },
  orders: [blockedRow],
};

beforeEach(() => {
  jest.clearAllMocks();
  check.mockResolvedValue({ success: true, data: payload });
  loadSettings.mockResolvedValue({ success: true, data: { shop_id: 1, auto_writeback: false } });
  saveSettings.mockResolvedValue({ success: true, data: { shop_id: 1, auto_writeback: true } });
  exportCsv.mockResolvedValue(new Blob(['order'], { type: 'text/csv' }));
  listJobs.mockResolvedValue({ success: true, data: { jobs: [] } });
  syncManual.mockResolvedValue({
    success: true,
    data: {
      summary: { requested: 1, bound: 1, already_bound: 0, not_found: 0, failed: 0 },
      results: [{ order_id: 1, order_number: '15950', status: 'bound', tracking_number: '4PX-MANUAL-1' }],
    },
  });
  startJob.mockResolvedValue({
    success: true,
    data: {
      id: 'job-1', shop_id: 1, status: 'done', requested: 1, processed: 1, created: 1, failed: 0,
      results: [{ order_id: 1, order_number: '15950', status: 'created', tracking_number: '4PX3003332909999CN' }],
    },
  });
  getJob.mockResolvedValue({
    success: true,
    data: {
      id: 'job-1', shop_id: 1, status: 'done', requested: 1, processed: 1, created: 1, failed: 0,
      results: [{ order_id: 1, order_number: '15950', status: 'created', tracking_number: '4PX3003332909999CN' }],
    },
  });
  planBatch.mockResolvedValue({
    success: true,
    data: {
      summary: { requested: 1, matched: 1, eligible: 1, blocked: 0, invalid: 0 },
      invalid_order_ids: [],
      groups: [{
        product: { key: 'light', code: 'S5058', name: '联邮通轻小件-普货', tracking: false },
        order_numbers: ['15950'], order_count: 1, quantity: 1, weight_grams: 120, declared_value: 1.2,
      }],
      orders: [row()],
    },
  });
});

test('ready tab lists product, weight and value', async () => {
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  expect(check).toHaveBeenCalledWith(1, expect.objectContaining({ page: 1, per_page: 20, status: 'ready' }));
  expect(screen.getByText('15950')).toBeTruthy();
  expect(screen.getByText('S5058')).toBeTruthy();
  expect(screen.getByText('USD 1.20')).toBeTruthy();
  expect(screen.getByText(/可预申报（10）/)).toBeTruthy();
  expect(screen.getByText(/待人工处理（2）/)).toBeTruthy();
});

test('switches to blocked tab and shows reasons with copy action', async () => {
  check.mockResolvedValue({ success: true, data: blockedPayload });
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  await act(async () => { fireEvent.click(screen.getByText(/待人工处理（2）/)); });
  expect(check).toHaveBeenLastCalledWith(1, expect.objectContaining({ status: 'blocked' }));
  expect(screen.getByText(/门牌号和街道名/)).toBeTruthy();
  expect(screen.getAllByRole('button').some((button) => button.textContent?.includes('复制'))).toBeTruthy();
});

test('opens the payload preview drawer for a ready order', async () => {
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  await act(async () => {
    const button = screen.getAllByRole('button').find((item) => item.textContent?.includes('查看请求体'));
    fireEvent.click(button as HTMLElement);
  });
  expect(screen.getByText('15950 请求体')).toBeTruthy();
  expect(screen.getByText(/"ref_no": "15950"/)).toBeTruthy();
});

test('searches by order number', async () => {
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  const input = screen.getByPlaceholderText('订单号');
  await act(async () => { fireEvent.change(input, { target: { value: '15951' } }); });
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13 }); });
  expect(check).toHaveBeenLastCalledWith(1, expect.objectContaining({ search: '15951', page: 1, per_page: 20 }));
});

test('builds a batch plan from selected ready orders', async () => {
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  const checkboxes = screen.getAllByRole('checkbox');
  await act(async () => { fireEvent.click(checkboxes[0]); });
  const planButton = screen.getAllByRole('button').find((button) => button.textContent?.includes('生成清单'));
  expect(planButton?.textContent).toContain('（1）');
  await act(async () => { fireEvent.click(planButton as HTMLElement); });
  expect(planBatch).toHaveBeenCalledWith(1, [1]);
  expect(await screen.findByText(/选中 1 单/)).toBeTruthy();
});

test('creates real 4PX declarations only after explicit confirmation', async () => {
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  const checkboxes = screen.getAllByRole('checkbox');
  await act(async () => { fireEvent.click(checkboxes[0]); });
  const createButton = screen.getAllByRole('button').find((button) => button.textContent?.includes('真实预申报'));
  await act(async () => { fireEvent.click(createButton as HTMLElement); });
  expect(screen.getByText(/将向 4PX 创建 1 张直发委托单/)).toBeTruthy();
  await act(async () => {
    const confirm = screen.getAllByRole('button').find((button) => button.textContent?.replace(/\s/g, '') === '确认建单');
    fireEvent.click(confirm as HTMLElement);
  });
  expect(startJob).toHaveBeenCalledWith(1, [1]);
  expect(screen.getByText('预申报已完成')).toBeTruthy();
  await act(async () => {
    const resultButton = screen.getAllByRole('button').find((button) => button.textContent?.includes('查看结果'));
    fireEvent.click(resultButton as HTMLElement);
  });
  expect(await screen.findByText(/共 1 单：成功 1/)).toBeTruthy();
});

test('exports current tab as CSV and filters by order range', async () => {
  const createObjectURL = jest.fn(() => 'blob:mock');
  Object.assign(URL, { createObjectURL, revokeObjectURL: jest.fn() });
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  await act(async () => { fireEvent.change(screen.getByPlaceholderText('起始单号'), { target: { value: '15950' } }); });
  await act(async () => { fireEvent.change(screen.getByPlaceholderText('结束单号'), { target: { value: '15960' } }); });
  await act(async () => {
    const button = screen.getAllByRole('button').find((item) => item.textContent?.replace(/\s/g, '') === '筛选');
    fireEvent.click(button as HTMLElement);
  });
  expect(check).toHaveBeenLastCalledWith(1, expect.objectContaining({ order_from: '15950', order_to: '15960' }));
  await act(async () => {
    const button = screen.getAllByRole('button').find((item) => item.textContent?.replace(/\s/g, '') === '导出');
    fireEvent.click(button as HTMLElement);
  });
  expect(exportCsv).toHaveBeenCalledWith(1, undefined, { from: '15950', to: '15960' }, { status: 'ready' });
  expect(createObjectURL).toHaveBeenCalled();
});

test('toggles automatic Shopify writeback for the shop', async () => {
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  expect(loadSettings).toHaveBeenCalledWith(1);
  const toggle = screen.getByRole('switch');
  await act(async () => { fireEvent.click(toggle); });
  expect(saveSettings).toHaveBeenCalledWith(1, true);
});

test('shows running job progress from the last task', async () => {
  listJobs.mockResolvedValue({
    success: true,
    data: { jobs: [{
      id: 'job-running', shop_id: 1, status: 'running', requested: 159, processed: 60, created: 58, failed: 2,
      results: [],
    }] },
  });
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  expect(screen.getByText('预申报进行中（后台执行，关页面不丢）')).toBeTruthy();
  expect(screen.getByText(/已处理 60\/159/)).toBeTruthy();
});

test('syncs manually declared 4PX orders for selected rows', async () => {
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  const checkboxes = screen.getAllByRole('checkbox');
  await act(async () => { fireEvent.click(checkboxes[0]); });
  const syncButton = screen.getAllByRole('button').find((button) => button.textContent?.includes('同步 4PX 已有单'));
  await act(async () => { fireEvent.click(syncButton as HTMLElement); });
  expect(syncManual).toHaveBeenCalledWith(1, { orderIds: [1] });
  expect(await screen.findByText(/已绑定 1 单/)).toBeTruthy();
});

test('blocked tab offers one-click 4PX sync per row', async () => {
  check.mockResolvedValue({ success: true, data: blockedPayload });
  await act(async () => { render(React.createElement(DeclarationCheck)); });
  await act(async () => { fireEvent.click(screen.getByText(/待人工处理（2）/)); });
  const syncButton = screen.getAllByRole('button').find((button) => button.textContent?.replace(/\s/g, '') === '同步4PX');
  expect(syncButton).toBeTruthy();
  await act(async () => { fireEvent.click(syncButton as HTMLElement); });
  expect(syncManual).toHaveBeenCalledWith(1, { orderIds: [2] });
});
