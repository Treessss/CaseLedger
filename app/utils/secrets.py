"""对称加密的共用入口。

邮件模块要保存 Zoho refresh token 和 DeepSeek API Key，这类凭据不能明文落库。
密钥沿用平台账号那套 ENCRYPTION_KEY，避免再引入一份密钥配置。
"""

from cryptography.fernet import Fernet, InvalidToken


def _cipher():
    import os

    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        try:
            from flask import current_app

            key = current_app.config.get("ENCRYPTION_KEY")
        except RuntimeError:
            key = None
    if not key:
        raise RuntimeError(
            "缺少 ENCRYPTION_KEY 配置，无法加密保存邮件凭据。请在 .env 中配置后重试。"
        )
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def seal(plaintext):
    """加密成可入库的字符串；空值原样返回，省掉一次加解密。"""
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode()).decode()


def open_sealed(ciphertext):
    """解密。密钥换过或数据损坏时返回空串，由调用方给出可读提示。"""
    if not ciphertext:
        return ""
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
