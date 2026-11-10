import { isCompletePackingLabel, isDuplicatePackingScan, isOutboundLabel, normalizePackingScan } from './packingScan';

test('recovers double scan and takes the latest complete 4PX label', () => {
  const label = '4PX3003234093234CN';
  expect(normalizePackingScan(label + label)).toBe(label);
  expect(normalizePackingScan(`${label}4PX3003234315688CN`)).toBe('4PX3003234315688CN');
  expect(isCompletePackingLabel(label)).toBe(true);
  expect(isCompletePackingLabel('4PX3003234')).toBe(false);
});

test('never shortens repeated digits in a real order or an unknown barcode', () => {
  expect(normalizePackingScan('121212')).toBe('121212');
  expect(normalizePackingScan('ABCABC')).toBe('ABCABC');
  expect(normalizePackingScan('4PX123CNinvalid')).toBe('4PX123CNINVALID');
});

test('scan input is normalized to uppercase for labels and references', () => {
  expect(normalizePackingScan('4px3003234093234cn')).toBe('4PX3003234093234CN');
  expect(normalizePackingScan(' cf9379 ')).toBe('CF9379');
  expect(normalizePackingScan('gv26caa0u076833783')).toBe('GV26CAA0U076833783');
  expect(isCompletePackingLabel('4px3003234093234cn')).toBe(true);
});

test('GV barcodes keep their full identifier and exact double scans are consumed once', () => {
  const label = 'GV26CAA0U076833783';
  expect(normalizePackingScan(label)).toBe(label);
  expect(normalizePackingScan(label + label)).toBe(label);
  expect(isCompletePackingLabel('GV26CAA0')).toBe(false);
});

test('only ignores consecutive duplicate lookups briefly within the same shop', () => {
  const previous = { shopId: 1, value: '12004', startedAt: 1000 };
  expect(isDuplicatePackingScan(previous, { ...previous, startedAt: 1100 })).toBe(true);
  expect(isDuplicatePackingScan(previous, { ...previous, startedAt: 2200 })).toBe(false);
  expect(isDuplicatePackingScan(previous, { ...previous, shopId: 2, startedAt: 1100 })).toBe(false);
  expect(isDuplicatePackingScan(previous, { ...previous, value: '12005', startedAt: 1100 })).toBe(false);
});

test('physical labels use the fast outbound path but order/CF references remain read-only', () => {
  for (const label of ['4PX3003234093234CN', 'GV26CAA0U076833783', 'OLS3060232810393', '9400111899562864096841']) {
    expect(isOutboundLabel(label)).toBe(true);
  }
  for (const ref of ['12004', '#12004', 'CF9379', 'CF9379-2']) expect(isOutboundLabel(ref)).toBe(false);
});
