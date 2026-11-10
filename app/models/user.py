from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

from app import db

# 子账户与店铺的多对多关系：一个子账户可以同时被分配多个店铺
user_shops = db.Table(
    "user_shops",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("shop_id", db.Integer, db.ForeignKey("shops.id", ondelete="CASCADE"), primary_key=True),
    db.Column("created_at", db.DateTime, default=datetime.utcnow),
)


class UserPermission(db.Model):
    """子账户被授予的功能权限（菜单/模块级）。

    一行一个权限 key，与 app/utils/permissions.py 的注册表对应；
    管理员不需要记录（默认拥有全部权限）。
    """

    __tablename__ = "user_permissions"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    permission = db.Column(db.String(50), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(db.Model):
    """系统账号模型：管理员(admin)与子账户(user)。"""

    __tablename__ = "users"

    ROLE_ADMIN = "admin"
    ROLE_USER = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100))
    email = db.Column(db.String(200))
    role = db.Column(db.String(20), nullable=False, default=ROLE_USER, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 子账户被授权的店铺
    shops = db.relationship(
        "Shop", secondary=user_shops, lazy="selectin", backref=db.backref("users", lazy="selectin")
    )

    # 子账户被授予的功能权限（管理员为空，表示拥有全部）
    permissions = db.relationship(
        "UserPermission",
        lazy="selectin",
        cascade="all, delete-orphan",
        backref=db.backref("user", lazy="selectin"),
    )

    def __repr__(self):
        return f"<User {self.username}:{self.role}>"

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN

    def shop_ids(self):
        """管理员默认拥有全部店铺（返回 None 表示不做店铺过滤）。"""
        if self.is_admin:
            return None
        return [shop.id for shop in self.shops]

    def permission_keys(self):
        """管理员返回全部权限 key；子账户返回被授予的 key 列表。"""
        from app.utils.permissions import ALL_KEYS

        if self.is_admin:
            return list(ALL_KEYS)
        return sorted(item.permission for item in self.permissions)

    def set_permissions(self, keys):
        """覆盖式设置权限（仅接受可授予的 key）。"""
        from app.utils.permissions import is_grantable

        wanted = []
        for key in keys or []:
            if not is_grantable(key):
                raise ValueError(f"权限不可授予子账户: {key}")
            if key not in wanted:
                wanted.append(key)

        self.permissions = [UserPermission(permission=key) for key in wanted]

    def to_dict(self, include_shops=True):
        data = {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name or self.username,
            "email": self.email,
            "role": self.role,
            "is_active": self.is_active,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_shops:
            data["shop_ids"] = [shop.id for shop in self.shops]
            data["shops"] = [
                {"id": shop.id, "name": shop.name, "shop_url": shop.shop_url} for shop in self.shops
            ]
            data["permissions"] = self.permission_keys()
        return data

    @staticmethod
    def get_by_username(username):
        if not username:
            return None
        return User.query.filter_by(username=username).first()
