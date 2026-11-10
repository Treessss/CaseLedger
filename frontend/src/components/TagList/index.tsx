import { Tag, Tooltip } from 'antd';
import React from 'react';

interface TagListProps {
  items: string[];
  color?: string;
  /** 最多展示几个，超出折叠成 +N */
  max?: number;
}

/**
 * 标签列表：列宽有限时只展示前 N 个，其余折叠成「+N」并用 Tooltip 展示全部。
 * 用于「已分配店铺」「功能权限」这类数量不定的列，避免撑坏表格布局。
 */
const TagList: React.FC<TagListProps> = ({ items, color = 'default', max = 3 }) => {
  if (!items.length) {
    return <span style={{ color: '#cbd5e1' }}>-</span>;
  }

  const visible = items.slice(0, max);
  const rest = items.slice(max);

  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
      {visible.map((item) => (
        <Tag key={item} color={color} style={{ marginInlineEnd: 0 }}>
          {item}
        </Tag>
      ))}
      {rest.length > 0 && (
        <Tooltip title={rest.join('、')}>
          <Tag style={{ marginInlineEnd: 0, cursor: 'pointer' }}>+{rest.length}</Tag>
        </Tooltip>
      )}
    </span>
  );
};

export default TagList;
