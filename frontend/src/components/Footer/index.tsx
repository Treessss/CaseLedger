import React from 'react';

/**
 * 应用页脚：只保留产品自身信息，去掉模板自带的 Ant Design Pro 外链。
 */
const Footer: React.FC = () => {
  return (
    <div
      style={{
        padding: '16px 24px 24px',
        textAlign: 'center',
        color: '#94A3B8',
        fontSize: 12,
        background: 'none',
      }}
    >
      CaseLedger · 跨境电商订单成本管理系统
    </div>
  );
};

export default Footer;
