import { formatShippingDeclaration, getShippingContact } from './shippingDeclaration';

test('prioritizes shipping recipient over buyer details for declaration', () => {
  const contact = getShippingContact(
    { name: 'Jane Doe', phone: '12345', address1: '1 Main St', city: 'New York', zip: '10001', country: 'US' },
    { name: 'Buyer Name', phone: '99999', email: 'buyer@example.com' },
  );
  expect(contact.name).toBe('Jane Doe');
  expect(contact.phone).toBe('12345');
  expect(formatShippingDeclaration('#1001', contact)).toContain('地址1：1 Main St');
  expect(formatShippingDeclaration('#1001', contact)).not.toContain('Buyer Name');
});

test('falls back to customer and omits unavailable fields', () => {
  const contact = getShippingContact({ country: 'Hong Kong' }, { name: 'Alex', phone: '555' });
  const text = formatShippingDeclaration('#1002', contact);
  expect(text).toContain('收件人：Alex');
  expect(text).toContain('国家/地区：Hong Kong');
  expect(text).not.toContain('地址1：');
});
