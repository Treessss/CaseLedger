/** Only split complete 4PX labels, never guess boundaries in order numbers. */
export function normalizePackingScan(raw: string): string {
  const value = raw.trim().toUpperCase();
  // GV barcodes have no reliable terminator; only collapse an exact repeated scan.
  const repeatedGv = value.match(/^(GV\d{2}[A-Z0-9]{8,}?)\1+$/i);
  if (repeatedGv) return repeatedGv[1];
  const labels = value.match(/4PX\d+CN/gi);
  if (labels?.length && labels.join('').toUpperCase() === value.toUpperCase()) {
    return labels[labels.length - 1].toUpperCase();
  }
  return value;
}

export function isCompletePackingLabel(raw: string): boolean {
  return /^(?:4PX\d+CN)+$/i.test(raw.trim());
}

/** Reference/order codes remain read-only; physical labels are validated by the scan API. */
export function isOutboundLabel(raw: string): boolean {
  const value = normalizePackingScan(raw);
  if (/^#?\d{1,9}$/.test(value) || /^CF\d+(?:-\d+)?$/i.test(value)) return false;
  return /^[A-Z0-9-]{8,}$/i.test(value) && (/[A-Z]/i.test(value) || /^\d{10,}$/.test(value));
}

export type PackingScanAttempt = { shopId: number; value: string; startedAt: number };

export function isDuplicatePackingScan(
  previous: PackingScanAttempt | null,
  current: PackingScanAttempt,
): boolean {
  return previous !== null && previous.shopId === current.shopId &&
    previous.value === current.value && current.startedAt - previous.startedAt < 1200;
}
