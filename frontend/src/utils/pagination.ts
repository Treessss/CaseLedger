import { getSelectedShopId } from '@/utils/shop';

const PREFIX = 'caseledger_pagination_v1';
const MAX_PAGE = 100000;
const MAX_PAGE_SIZE = 100;

type PageState = { current: number; pageSize: number };

const validNumber = (value: unknown, fallback: number, max: number) => {
  const number = Number(value);
  return Number.isInteger(number) && number >= 1 && number <= max
    ? number
    : fallback;
};

const storageKey = (table: string) =>
  `${PREFIX}:${table}:${getSelectedShopId()}`;

export const readPagination = (table: string, defaultPageSize: number, maxPageSize = MAX_PAGE_SIZE): PageState => {
  const fallback = { current: 1, pageSize: defaultPageSize };
  try {
    const raw = localStorage.getItem(storageKey(table));
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<PageState>;
    return {
      current: validNumber(saved.current, 1, MAX_PAGE),
      pageSize: validNumber(saved.pageSize, defaultPageSize, maxPageSize),
    };
  } catch {
    return fallback;
  }
};

export const savePagination = (table: string, current: number, pageSize: number, maxPageSize = MAX_PAGE_SIZE) => {
  try {
    localStorage.setItem(
      storageKey(table),
      JSON.stringify({
        current: validNumber(current, 1, MAX_PAGE),
        pageSize: validNumber(pageSize, 10, maxPageSize),
      }),
    );
  } catch {
    // 隐私模式或存储空间已满时，仍允许表格正常分页。
  }
};

/** ProTable 使用默认页而非受控 current，保留搜索时自动回到第一页的行为。 */
export const persistedPagination = (table: string, defaultPageSize: number, maxPageSize = MAX_PAGE_SIZE) => {
  const saved = readPagination(table, defaultPageSize, maxPageSize);
  return {
    defaultCurrent: saved.current,
    defaultPageSize: saved.pageSize,
    onChange: (current: number, pageSize: number) =>
      savePagination(table, current, pageSize, maxPageSize),
  };
};
