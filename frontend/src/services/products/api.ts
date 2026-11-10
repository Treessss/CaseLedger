import { request } from '@umijs/max';

export type ProductColorName = {
  original_color: string; chinese_name: string; image_url: string; variant_count: number;
};
export type ManagedProduct = {
  shop_id: number; shopify_product_id?: string | null; original_title: string; chinese_name: string; display_title: string;
  image_url: string; variant_count: number; colors: ProductColorName[]; mapped: boolean;
};

export function listManagedProducts(params: { shop_id: number; search?: string; mapped?: string; page: number; per_page: number }) {
  return request<{ success: boolean; data: { items: ManagedProduct[]; pagination: { total: number } } }>('/api/products', { params });
}

export function saveProductNames(data: { shop_id: number; shopify_product_id?: string | null; original_title: string; chinese_name: string;
  colors: { original_color: string; chinese_name: string }[] }) {
  return request('/api/products/translations', { method: 'PUT', data });
}
