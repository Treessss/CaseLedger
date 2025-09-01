import requests
from decimal import Decimal
from flask import current_app
from typing import Optional, Dict
import time

class ExchangeRateService:
    """汇率服务 - 获取实时汇率"""
    
    def __init__(self):
        self.cache = {}
        self.cache_duration = 3600  # 缓存1小时
        # 支持的货币列表
        self.supported_currencies = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'CNY']
    
    def get_exchange_rate(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """获取两种货币之间的汇率"""
        if from_currency == to_currency:
            return Decimal('1')
            
        cache_key = f'{from_currency}_{to_currency}'
        current_time = time.time()
        
        # 检查缓存
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            if current_time - cached_data['timestamp'] < self.cache_duration:
                return cached_data['rate']
        
        # 尝试从多个API获取汇率
        rate = self._fetch_rate_from_apis(from_currency, to_currency)
        
        if rate:
            # 缓存汇率
            self.cache[cache_key] = {
                'rate': rate,
                'timestamp': current_time
            }
            return rate
        
        # 如果所有API都失败，返回默认汇率
        default_rates = {
            'USD_CNY': Decimal('7.2'),
            'EUR_CNY': Decimal('7.8'),
            'GBP_CNY': Decimal('9.1'),
            'CAD_CNY': Decimal('5.3'),
            'AUD_CNY': Decimal('4.8'),
            'JPY_CNY': Decimal('0.048')
        }
        
        default_rate = default_rates.get(cache_key, Decimal('1'))
        try:
            current_app.logger.warning(f"无法获取实时汇率，使用默认汇率 {from_currency} -> {to_currency}: {default_rate}")
        except RuntimeError:
            # 在应用上下文外部运行时
            print(f"警告: 无法获取实时汇率，使用默认汇率 {from_currency} -> {to_currency}: {default_rate}")
        return default_rate
    
    def get_usd_to_cny_rate(self) -> Optional[Decimal]:
        """获取USD到CNY的汇率（保持向后兼容）"""
        return self.get_exchange_rate('USD', 'CNY')
    
    def _fetch_rate_from_apis(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """从多个API尝试获取汇率"""
        apis = [
            lambda: self._fetch_from_exchangerate_api(from_currency, to_currency),
            lambda: self._fetch_from_fixer_api(from_currency, to_currency),
            lambda: self._fetch_from_currencyapi(from_currency, to_currency)
        ]
        
        for api_func in apis:
            try:
                rate = api_func()
                if rate:
                    return rate
            except Exception as e:
                try:
                    current_app.logger.warning(f"汇率API调用失败: {e}")
                except RuntimeError:
                    # 在应用上下文外部运行时
                    print(f"警告: 汇率API调用失败: {e}")
                continue
        
        return None
    
    def _fetch_from_exchangerate_api(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """从 exchangerate-api.com 获取汇率"""
        try:
            url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if 'rates' in data and to_currency in data['rates']:
                rate = Decimal(str(data['rates'][to_currency]))
                try:
                    current_app.logger.info(f"从 exchangerate-api 获取汇率 {from_currency}->{to_currency}: {rate}")
                except RuntimeError:
                    print(f"信息: 从 exchangerate-api 获取汇率 {from_currency}->{to_currency}: {rate}")
                return rate
        except Exception as e:
            try:
                current_app.logger.warning(f"exchangerate-api 调用失败: {e}")
            except RuntimeError:
                print(f"警告: exchangerate-api 调用失败: {e}")
        
        return None
    
    def _fetch_from_fixer_api(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """从 fixer.io 获取汇率（需要API key）"""
        try:
            # 这里需要配置API key
            try:
                api_key = current_app.config.get('FIXER_API_KEY')
            except RuntimeError:
                api_key = None
            
            if not api_key:
                return None
            
            url = f"http://data.fixer.io/api/latest?access_key={api_key}&base={from_currency}&symbols={to_currency}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if data.get('success') and 'rates' in data and to_currency in data['rates']:
                rate = Decimal(str(data['rates'][to_currency]))
                try:
                    current_app.logger.info(f"从 fixer.io 获取汇率 {from_currency}->{to_currency}: {rate}")
                except RuntimeError:
                    print(f"信息: 从 fixer.io 获取汇率 {from_currency}->{to_currency}: {rate}")
                return rate
        except Exception as e:
            try:
                current_app.logger.warning(f"fixer.io 调用失败: {e}")
            except RuntimeError:
                print(f"警告: fixer.io 调用失败: {e}")
        
        return None
    
    def _fetch_from_currencyapi(self, from_currency: str, to_currency: str) -> Optional[Decimal]:
        """从 exchangerate.host 获取汇率"""
        try:
            # 使用免费的 exchangerate.host API
            url = f"https://api.exchangerate.host/latest?base={from_currency}&symbols={to_currency}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if 'rates' in data and to_currency in data['rates']:
                rate = Decimal(str(data['rates'][to_currency]))
                try:
                    current_app.logger.info(f"从 exchangerate.host 获取汇率 {from_currency}->{to_currency}: {rate}")
                except RuntimeError:
                    print(f"信息: 从 exchangerate.host 获取汇率 {from_currency}->{to_currency}: {rate}")
                return rate
        except Exception as e:
            try:
                current_app.logger.warning(f"exchangerate.host 调用失败: {e}")
            except RuntimeError:
                print(f"警告: exchangerate.host 调用失败: {e}")
        
        return None
    
    def convert_currency(self, amount: float, from_currency: str, to_currency: str) -> Decimal:
        """将金额从一种货币转换为另一种货币"""
        if not amount:
            return Decimal('0')
        
        # 如果目标货币为空或None，直接返回原始金额
        if not to_currency:
            return Decimal(str(amount)).quantize(Decimal('0.01'))
        
        if from_currency == to_currency:
            return Decimal(str(amount)).quantize(Decimal('0.01'))
        
        rate = self.get_exchange_rate(from_currency, to_currency)
        if rate is None:
            # 如果无法获取汇率，返回原始金额
            try:
                current_app.logger.warning(f"无法获取汇率 {from_currency} -> {to_currency}，返回原始金额")
            except RuntimeError:
                print(f"警告: 无法获取汇率 {from_currency} -> {to_currency}，返回原始金额")
            return Decimal(str(amount)).quantize(Decimal('0.01'))
        
        amount_decimal = Decimal(str(amount))
        converted_amount = amount_decimal * rate
        
        return converted_amount.quantize(Decimal('0.01'))  # 保留两位小数
    
    def convert_to_cny(self, amount: float, from_currency: str) -> Decimal:
        """将任意货币金额转换为CNY"""
        return self.convert_currency(amount, from_currency, 'CNY')
    
    def convert_usd_to_cny(self, usd_amount: float) -> Decimal:
        """将USD金额转换为CNY（保持向后兼容）"""
        return self.convert_currency(usd_amount, 'USD', 'CNY')
    
    def get_supported_currencies(self) -> list:
        """获取支持的货币列表"""
        return self.supported_currencies.copy()
    
    def is_currency_supported(self, currency: str) -> bool:
        """检查货币是否被支持"""
        return currency.upper() in self.supported_currencies

# 创建全局实例
exchange_rate_service = ExchangeRateService()