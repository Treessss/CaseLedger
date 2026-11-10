import React from 'react';
import type { PackingList } from '@/services/shipping/api';

/**
 * 收货信息核对：装箱核对和扫码出库共用。
 *
 * 用本单解析到的包裹地址（售后补发单可能是另一个收件人），不是客户账单姓名。
 */
export default function RecipientInfo({ result }: { result: PackingList }) {
  const address = result.shipping_address || {};
  const text = (key: string) => String(address[key] || '').trim();
  const name = text('name') || [text('first_name'), text('last_name')].filter(Boolean).join(' ');
  const fields = [
    ['收件人', name || result.customer_name || '未提供'],
    ['联系电话', text('phone') || '未提供'],
    ['公司', text('company')],
    ['地址', [text('address1'), text('address2')].filter(Boolean).join('\n') || '未提供'],
    ['城市', text('city')],
    ['省 / 州', text('province') || text('province_code')],
    ['邮编', text('zip')],
    ['国家 / 地区', text('country') || text('country_code')],
  ];
  return (
    <aside className="packing-recipient" aria-label="收货信息">
      <h3>收货信息核对</h3>
      <div className="packing-recipient-reference">{result.reference}{result.type === 'after_sale' ? ' · 补发收货地址' : ''}</div>
      <dl>
        {fields.filter(([, value]) => value).map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
        ))}
      </dl>
      {!Object.values(address).some(Boolean) && <p>暂无收货地址，请先核对订单信息。</p>}
    </aside>
  );
}
