import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';
import timezone from 'dayjs/plugin/timezone';

// 扩展dayjs插件
dayjs.extend(utc);
dayjs.extend(timezone);

// 北京时区
const BEIJING_TIMEZONE = 'Asia/Shanghai';

/**
 * 将UTC时间转换为北京时间显示
 * @param utcTime UTC时间字符串或Date对象
 * @param format 显示格式，默认为 'YYYY-MM-DD HH:mm:ss'
 * @returns 北京时间格式化字符串
 */
export const formatBeijingTime = (utcTime: string | Date, format: string = 'YYYY-MM-DD HH:mm:ss'): string => {
  if (!utcTime) return '';
  
  return dayjs.utc(utcTime).tz(BEIJING_TIMEZONE).format(format);
};

/**
 * 将北京时间转换为UTC时间（用于发送给后端）
 * @param beijingTime 北京时间字符串或Date对象
 * @returns UTC时间的ISO字符串
 */
export const convertBeijingToUTC = (beijingTime: string | Date): string => {
  if (!beijingTime) return '';
  
  return dayjs.tz(beijingTime, BEIJING_TIMEZONE).utc().toISOString();
};

/**
 * 获取当前北京时间
 * @param format 显示格式，默认为 'YYYY-MM-DD HH:mm:ss'
 * @returns 当前北京时间格式化字符串
 */
export const getCurrentBeijingTime = (format: string = 'YYYY-MM-DD HH:mm:ss'): string => {
  return dayjs().tz(BEIJING_TIMEZONE).format(format);
};

/**
 * 将北京时间的日期范围转换为UTC时间范围（用于API查询）
 * @param startDate 开始日期（北京时间）
 * @param endDate 结束日期（北京时间）
 * @returns UTC时间范围对象
 */
export const convertDateRangeToUTC = (startDate: string, endDate: string) => {
  const start = startDate ? dayjs.tz(startDate + ' 00:00:00', BEIJING_TIMEZONE).utc().format('YYYY-MM-DD') : null;
  const end = endDate ? dayjs.tz(endDate + ' 23:59:59', BEIJING_TIMEZONE).utc().format('YYYY-MM-DD') : null;
  
  return { start, end };
};

/**
 * 获取当前月份的北京时间日期范围（转换为UTC）
 * @returns UTC时间范围对象
 */
export const getCurrentMonthUTCRange = () => {
  const now = dayjs().tz(BEIJING_TIMEZONE);
  const startOfMonth = now.startOf('month').format('YYYY-MM-DD');
  const endOfMonth = now.endOf('month').format('YYYY-MM-DD');
  
  return convertDateRangeToUTC(startOfMonth, endOfMonth);
};

/**
 * 将dayjs对象转换为北京时间的dayjs对象
 * @param date dayjs对象或时间字符串
 * @returns 北京时间的dayjs对象
 */
export const toBeijingTime = (date: any) => {
  if (!date) return null;
  
  if (typeof date === 'string') {
    return dayjs.utc(date).tz(BEIJING_TIMEZONE);
  }
  
  return dayjs(date).tz(BEIJING_TIMEZONE);
};

/**
 * 格式化简短的北京时间（用于列表显示）
 * @param utcTime UTC时间字符串或Date对象
 * @returns 简短的北京时间格式 'MM-DD HH:mm'
 */
export const formatShortBeijingTime = (utcTime: string | Date): string => {
  return formatBeijingTime(utcTime, 'MM-DD HH:mm');
};