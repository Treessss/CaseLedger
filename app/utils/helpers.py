from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import re
from decimal import Decimal, InvalidOperation


def validate_email(email: str) -> bool:
    """验证邮箱格式"""
    if not email:
        return False
    
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_currency_amount(amount: Any) -> Optional[float]:
    """验证并转换货币金额"""
    if amount is None:
        return None
    
    try:
        # 处理字符串格式的金额
        if isinstance(amount, str):
            # 移除货币符号和空格
            amount = re.sub(r'[^\d.-]', '', amount)
            if not amount:
                return None
        
        # 转换为Decimal以确保精度
        decimal_amount = Decimal(str(amount))
        
        # 检查是否为负数
        if decimal_amount < 0:
            return None
        
        return float(decimal_amount)
        
    except (ValueError, InvalidOperation):
        return None


def format_currency(amount: float, currency: str = 'USD') -> str:
    """格式化货币显示"""
    if amount is None:
        return '0.00'
    
    currency_symbols = {
        'USD': '$',
        'EUR': '€',
        'GBP': '£',
        'CNY': '¥',
        'JPY': '¥'
    }
    
    symbol = currency_symbols.get(currency, currency)
    return f"{symbol}{amount:.2f}"


def parse_date_range(start_date: str, end_date: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """解析日期范围"""
    start_dt = None
    end_dt = None
    
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        except ValueError:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            except ValueError:
                pass
    
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            # 如果只提供了日期，设置为当天的23:59:59
            if end_dt.time() == end_dt.time().replace(hour=0, minute=0, second=0, microsecond=0):
                end_dt = end_dt.replace(hour=23, minute=59, second=59)
        except ValueError:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                end_dt = end_dt.replace(hour=23, minute=59, second=59)
            except ValueError:
                pass
    
    return start_dt, end_dt


def get_date_range_presets() -> Dict[str, tuple[datetime, datetime]]:
    """获取预设的日期范围"""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    return {
        'today': (today_start, now),
        'yesterday': (
            today_start - timedelta(days=1),
            today_start - timedelta(seconds=1)
        ),
        'this_week': (
            today_start - timedelta(days=now.weekday()),
            now
        ),
        'last_week': (
            today_start - timedelta(days=now.weekday() + 7),
            today_start - timedelta(days=now.weekday() + 1)
        ),
        'this_month': (
            today_start.replace(day=1),
            now
        ),
        'last_month': (
            (today_start.replace(day=1) - timedelta(days=1)).replace(day=1),
            today_start.replace(day=1) - timedelta(seconds=1)
        ),
        'last_7_days': (
            today_start - timedelta(days=7),
            now
        ),
        'last_30_days': (
            today_start - timedelta(days=30),
            now
        ),
        'last_90_days': (
            today_start - timedelta(days=90),
            now
        )
    }


def calculate_percentage_change(current: float, previous: float) -> float:
    """计算百分比变化"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    
    return ((current - previous) / previous) * 100


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """安全除法，避免除零错误"""
    if denominator == 0:
        return default
    return numerator / denominator


def truncate_string(text: str, max_length: int = 50, suffix: str = '...') -> str:
    """截断字符串"""
    if not text or len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def clean_phone_number(phone: str) -> str:
    """清理电话号码格式"""
    if not phone:
        return ''
    
    # 只保留数字、加号和连字符
    cleaned = re.sub(r'[^\d+\-\s()]', '', phone)
    return cleaned.strip()


def generate_order_summary(order_data: Dict[str, Any]) -> str:
    """生成订单摘要"""
    order_number = order_data.get('order_number', 'N/A')
    customer_name = order_data.get('customer_name', 'Unknown')
    total_price = order_data.get('total_price', 0)
    currency = order_data.get('currency', 'USD')
    
    return f"Order #{order_number} - {customer_name} - {format_currency(total_price, currency)}"


def validate_shopify_webhook_signature(data: bytes, signature: str, secret: str) -> bool:
    """验证Shopify Webhook签名"""
    import hmac
    import hashlib
    import base64
    
    try:
        computed_signature = base64.b64encode(
            hmac.new(
                secret.encode('utf-8'),
                data,
                hashlib.sha256
            ).digest()
        ).decode('utf-8')
        
        return hmac.compare_digest(computed_signature, signature)
    except Exception:
        return False


def get_business_days_between(start_date: datetime, end_date: datetime) -> int:
    """计算两个日期之间的工作日数量"""
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    
    business_days = 0
    current_date = start_date
    
    while current_date <= end_date:
        # 0-6 对应周一到周日，0-4是工作日
        if current_date.weekday() < 5:
            business_days += 1
        current_date += timedelta(days=1)
    
    return business_days


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小显示"""
    if size_bytes == 0:
        return "0B"
    
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    
    return f"{size_bytes:.1f}{size_names[i]}"


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除不安全字符"""
    # 移除或替换不安全的字符
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    # 移除控制字符
    filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', filename)
    
    # 限制长度
    if len(filename) > 255:
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
        max_name_length = 255 - len(ext) - 1 if ext else 255
        filename = name[:max_name_length] + ('.' + ext if ext else '')
    
    return filename.strip()