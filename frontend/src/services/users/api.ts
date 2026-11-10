import { request } from '@umijs/max';

export interface ManagedUser {
  id: number;
  username: string;
  display_name?: string;
  email?: string;
  role: 'admin' | 'user';
  is_active: boolean;
  last_login_at?: string | null;
  created_at?: string;
  updated_at?: string;
  shop_ids: number[];
  shops: Array<{ id: number; name: string; shop_url: string }>;
  /** 已授予的功能权限 key（管理员为全部） */
  permissions: string[];
}

export interface UserPayload {
  username?: string;
  password?: string;
  display_name?: string;
  email?: string;
  is_active?: boolean;
  shop_ids?: number[];
  permissions?: string[];
}

export interface PermissionItem {
  key: string;
  label: string;
  /** 展示分组，如「仓储作业」「财务」 */
  group?: string;
  /** 这项权限具体覆盖哪些功能 */
  description?: string;
  /** 同时需要的其它权限 key（如仪表板需要订单+报表） */
  requires?: string[];
  /** 额外提示（如仪表板依赖说明） */
  note?: string;
  /** true = 仅管理员可用，不可授予子账户 */
  admin_only: boolean;
}

/** 账号列表（仅管理员） */
export async function listUsers(params?: { role?: string; keyword?: string }) {
  return request<{ success: boolean; data: ManagedUser[] }>('/api/users', {
    method: 'GET',
    params: params || {},
  });
}

/** 新增子账户并分配店铺（仅管理员） */
export async function createUser(payload: UserPayload) {
  return request<{ success: boolean; message?: string; data: ManagedUser }>(
    '/api/users',
    {
      method: 'POST',
      data: payload,
    },
  );
}

/** 修改子账户（仅管理员） */
export async function updateUser(id: number, payload: UserPayload) {
  return request<{ success: boolean; message?: string; data: ManagedUser }>(
    `/api/users/${id}`,
    {
      method: 'PUT',
      data: payload,
    },
  );
}

/** 给子账户分配店铺（支持多个，仅管理员） */
export async function assignUserShops(id: number, shopIds: number[]) {
  return request<{ success: boolean; message?: string; data: ManagedUser }>(
    `/api/users/${id}/shops`,
    {
      method: 'PUT',
      data: { shop_ids: shopIds },
    },
  );
}

/** 删除子账户（仅管理员） */
export async function deleteUser(id: number) {
  return request<{ success: boolean; message?: string }>(`/api/users/${id}`, {
    method: 'DELETE',
  });
}

/** 功能权限清单（仅管理员） */
export async function listPermissions() {
  return request<{
    success: boolean;
    data: {
      permissions: PermissionItem[];
      grantable_keys: string[];
      admin_only_keys: string[];
    };
  }>('/api/permissions', { method: 'GET' });
}

/** 给子账户分配可访问的功能模块（覆盖式，立即生效） */
export async function assignUserPermissions(id: number, permissions: string[]) {
  return request<{ success: boolean; message?: string; data: ManagedUser }>(
    `/api/users/${id}/permissions`,
    {
      method: 'PUT',
      data: { permissions },
    },
  );
}

/** 修改自己的密码 */
export async function changeOwnPassword(
  oldPassword: string,
  newPassword: string,
) {
  return request<{ success: boolean; message?: string }>('/api/users/me/password', {
    method: 'POST',
    data: { old_password: oldPassword, new_password: newPassword },
  });
}
