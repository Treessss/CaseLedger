#!/usr/bin/env python3
"""把 platform_accounts 表中的密码从旧密钥重新加密为新密钥。

背景：原实现把 Fernet 密钥硬编码在源码里，源码一旦泄露，库里的密码即可被解密。
整改后密钥来自环境变量 ENCRYPTION_KEY，历史数据需要用旧密钥解出来再重新加密。

使用步骤：
    1. 先备份数据库（项目根目录会自动保留 backups/ 下的副本）；
    2. 确认 .env 中已配置新的 ENCRYPTION_KEY（以及旧密钥 OLD_ENCRYPTION_KEY，可选）；
    3. 运行:  python migrate_encryption_key.py

脚本是幂等的：已经用新密钥加密的记录会被跳过，不会重复加密。
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models.platform_account import PlatformAccount  # noqa: E402

# 原代码中硬编码的密钥，仅用于解密历史数据
LEGACY_KEY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="


def main():
    new_key = os.environ.get("ENCRYPTION_KEY")
    if not new_key:
        print("错误: 未配置 ENCRYPTION_KEY，请先在 .env 中设置新密钥。")
        return 1

    old_key = os.environ.get("OLD_ENCRYPTION_KEY") or LEGACY_KEY
    if old_key == new_key:
        print("提示: 新旧密钥相同，无需迁移。")
        return 0

    old_cipher = Fernet(old_key.encode())
    new_cipher = Fernet(new_key.encode())

    app = create_app()
    migrated = 0
    already = 0
    failed = 0

    with app.app_context():
        accounts = PlatformAccount.query.all()
        print(f"待处理记录: {len(accounts)} 条")

        for account in accounts:
            token = (account.encrypted_password or "").encode()
            try:
                plain = old_cipher.decrypt(token)
            except InvalidToken:
                # 不是旧密钥加密的：确认是否已经是新密钥
                try:
                    new_cipher.decrypt(token)
                    already += 1
                    continue
                except InvalidToken:
                    failed += 1
                    print(f"  [跳过] id={account.id} 平台={account.platform} 无法用旧密钥或新密钥解密")
                    continue

            account.encrypted_password = new_cipher.encrypt(plain).decode()
            migrated += 1
            print(f"  [已迁移] id={account.id} 平台={account.platform}")

        if migrated:
            db.session.commit()
        else:
            db.session.rollback()

    print(f"完成: 迁移 {migrated} 条, 已是新密钥 {already} 条, 失败 {failed} 条")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
