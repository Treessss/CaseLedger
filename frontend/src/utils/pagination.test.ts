import {
  persistedPagination,
  readPagination,
  savePagination,
} from './pagination';

beforeEach(() => {
  localStorage.clear();
});

test('remembers page and page size for each table', () => {
  expect(readPagination('orders', 20)).toEqual({ current: 1, pageSize: 20 });
  persistedPagination('orders', 20).onChange(4, 50);
  expect(readPagination('orders', 20)).toEqual({ current: 4, pageSize: 50 });
  expect(readPagination('expenses', 10)).toEqual({ current: 1, pageSize: 10 });
});

test('isolates pagination by selected shop and ignores invalid values', () => {
  localStorage.setItem('caseledger_selected_shop', '1');
  savePagination('orders', 7, 20);
  localStorage.setItem('caseledger_selected_shop', '2');
  expect(readPagination('orders', 20)).toEqual({ current: 1, pageSize: 20 });
  savePagination('orders', -2, 1000);
  expect(readPagination('orders', 20)).toEqual({ current: 1, pageSize: 10 });
  localStorage.setItem('caseledger_selected_shop', '1');
  expect(readPagination('orders', 20)).toEqual({ current: 7, pageSize: 20 });
});
