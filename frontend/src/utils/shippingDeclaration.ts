export interface ShippingAddress {
  name?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  phone?: string | null;
  address1?: string | null;
  address2?: string | null;
  city?: string | null;
  province?: string | null;
  province_code?: string | null;
  zip?: string | null;
  country?: string | null;
  country_code?: string | null;
}

export interface ShippingContact {
  name: string;
  phone: string;
  email: string;
  address1: string;
  address2: string;
  city: string;
  province: string;
  postalCode: string;
  country: string;
}

const clean = (value?: string | null) => (value || '').trim();

export const getShippingContact = (
  address: ShippingAddress | null | undefined,
  customer: { name?: string; phone?: string; email?: string },
): ShippingContact => ({
  name: clean(address?.name) ||
    [clean(address?.first_name), clean(address?.last_name)].filter(Boolean).join(' ') ||
    clean(customer.name),
  phone: clean(address?.phone) || clean(customer.phone),
  email: clean(customer.email),
  address1: clean(address?.address1),
  address2: clean(address?.address2),
  city: clean(address?.city),
  province: clean(address?.province) || clean(address?.province_code),
  postalCode: clean(address?.zip),
  country: clean(address?.country) || clean(address?.country_code),
});

export const formatShippingDeclaration = (orderNumber: string, contact: ShippingContact) =>
  [
    ['订单号', orderNumber],
    ['收件人', contact.name],
    ['电话', contact.phone],
    ['邮箱', contact.email],
    ['地址1', contact.address1],
    ['地址2', contact.address2],
    ['城市', contact.city],
    ['省/州', contact.province],
    ['邮编', contact.postalCode],
    ['国家/地区', contact.country],
  ]
    .filter(([, value]) => Boolean(value))
    .map(([label, value]) => `${label}：${value}`)
    .join('\n');
