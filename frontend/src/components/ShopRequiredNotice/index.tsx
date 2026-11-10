import { ShopOutlined } from '@ant-design/icons';
import { Alert, Button, Select } from 'antd';
import React, { useEffect, useMemo, useState } from 'react';
import { listShops, type ShopItem } from '@/services/shops/api';
import { setSelectedShopId } from '@/utils/shop';

interface ShopRequiredNoticeProps {
  /** 说明这个页面为什么必须先选定店铺 */
  reason: string;
}

/**
 * 需要先选定店铺的页面（采购 / 库存 / 发货）在「全部店铺」下的统一空状态。
 *
 * 原来只在顶部放一行提示，页面其余部分全是空白，用户容易以为页面坏了。
 * 这里给一块居中的引导卡片，并允许直接选店铺进入，不用再回顶部找切换器。
 */
const ShopRequiredNotice: React.FC<ShopRequiredNoticeProps> = ({ reason }) => {
  const [shops, setShops] = useState<ShopItem[]>([]);
  const [picked, setPicked] = useState<string | undefined>();
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let mounted = true;
    listShops()
      .then((res) => {
        if (!mounted || !res?.success) return;
        const active = (res.data || []).filter(
          (shop) => shop.is_active !== false,
        );
        setShops(active);
        if (active.length === 1) {
          setPicked(String(active[0].id));
        }
      })
      .catch(() => {
        // 拉不到店铺时只提示，不阻塞页面
      })
      .finally(() => {
        if (mounted) setLoaded(true);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const options = useMemo(
    () =>
      shops.map((shop) => ({
        value: String(shop.id),
        label: shop.name || shop.shop_url,
      })),
    [shops],
  );

  const enter = () => {
    if (!picked) return;
    setSelectedShopId(picked);
    window.location.reload();
  };

  return (
    <div className="shop-required">
      <div className="shop-required__body">
        <span className="shop-required__icon">
          <ShopOutlined />
        </span>
        <p className="shop-required__title">先选一个店铺</p>
        <p className="shop-required__desc">{reason}</p>
        {options.length > 0 ? (
          <div className="shop-required__actions">
            <Select
              value={picked}
              placeholder="选择店铺"
              style={{ width: 240 }}
              options={options}
              onChange={(value) => setPicked(String(value))}
            />
            <Button
              type="primary"
              style={{ marginLeft: 8 }}
              disabled={!picked}
              onClick={enter}
            >
              进入
            </Button>
          </div>
        ) : loaded ? (
          <Alert
            type="warning"
            showIcon
            message="还没有可用店铺，请先到「系统管理 → 店铺管理」新增并绑定店铺。"
          />
        ) : null}
      </div>
    </div>
  );
};

export default ShopRequiredNotice;
