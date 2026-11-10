import access from './access';

test('expense deletion requires module and explicit action permission', () => {
  expect(access({ currentUser: { access: 'user', permissions: ['expenses'] } }).expensesDelete).toBe(false);
  expect(access({ currentUser: { access: 'user', permissions: ['expenses_delete'] } }).expensesDelete).toBe(false);
  expect(access({ currentUser: { access: 'user', permissions: ['expenses', 'expenses_delete'] } }).expensesDelete).toBe(true);
  expect(access({ currentUser: { access: 'admin' } }).expensesDelete).toBe(true);
});
