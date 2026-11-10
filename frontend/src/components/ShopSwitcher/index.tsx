import { ShopOutlined } from '@ant-design/icons';
import { request, useLocation, useModel } from '@umijs/max';
import { Select, Tooltip } from 'antd';
import React, { useEffect, useState } from 'react';
import type { ShopItem } from '@/services/shops/api';
import { ALL_SHOPS, getSelectedShopId, setSelectedShopId } from '@/utils/shop';

/** 这些页面与店铺数据无关，不显示全局店铺切换器 */
const HIDDEN_PATHS = ['/system', '/settings'];

/**
 * 必须选定具体店铺才能用的页面。
 *
 * 这类页面（例如装箱核对）在「全部店铺」下是灰的，所以首次进入时自动落到
 * 第一个可用店铺，省掉一次手动选择。
 */
const REQUIRE_SHOP_PATHS = ['/packing-check'];

/**
 * 全局店铺切换器。
 *
 * 放在页面内容区顶部（而不是侧边栏底部）：切换后全站请求都会带上 shop_id，
 * 只展示该店铺的订单、费用与报表数据。
 */
const ShopSwitcher: React.FC = () => {
  const [shops, setShops] = useState<ShopItem[]>([]);
  const [value, setValue] = useState<string>(getSelectedShopId());
  const { initialState } = useModel('@@initialState');
  const { pathname } = useLocation();

  useEffect(() => {
    let mounted = true;
    request<{ success: boolean; data: ShopItem[] }>('/api/shops', {
      method: 'GET',
      skipErrorHandler: true,
    })
      .then((res) => {
        if (mounted && res?.success) {
          setShops(res.data || []);
        }
      })
      .catch(() => {
        // 未登录或接口异常时不展示切换器
      });
    return () => {
      mounted = false;
    };
  }, [initialState?.currentUser?.userid]);

  useEffect(() => {
    if (!shops.length) return;
    if (!REQUIRE_SHOP_PATHS.some((prefix) => pathname.startsWith(prefix))) return;
    if (getSelectedShopId() !== ALL_SHOPS) return;

    setSelectedShopId(String(shops[0].id));
    window.location.reload();
  }, [shops, pathname]);

  if (!initialState?.currentUser || shops.length === 0) {
    return null;
  }
  if (HIDDEN_PATHS.some((path) => pathname.startsWith(path))) {
    return null;
  }

  const current = shops.find((shop) => String(shop.id) === value);

  return (
    <div className="global-toolbar">
      <Tooltip title="切换后只查看该店铺的订单、费用与报表数据">
        <span className="global-toolbar__label">
          <ShopOutlined /> 当前店铺
        </span>
      </Tooltip>
      <Select
        value={value}
        style={{ width: 220 }}
        options={[
          { value: ALL_SHOPS, label: '全部店铺' },
          ...shops.map((shop) => ({
            value: String(shop.id),
            label: shop.name || shop.shop_url,
          })),
        ]}
        onChange={(next) => {
          const nextValue = String(next);
          setValue(nextValue);
          setSelectedShopId(nextValue);
          // 重新加载页面，确保所有列表与图表都按新店铺重新取数
          window.location.reload();
        }}
      />
      {current && current.is_active === false && <span className="global-toolbar__hint">已停用</span>}
    </div>
  );
};

export default ShopSwitcher;
