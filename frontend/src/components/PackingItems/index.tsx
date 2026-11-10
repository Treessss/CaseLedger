import { Empty, Image, Typography } from 'antd';
import React from 'react';
import type { PackingLine } from '@/services/shipping/api';

/**
 * 变体里的手机型号。
 *
 * 打包时必须一眼确认机型（发错机型是最常见的错发），所以从变体名里把型号
 * 抠出来高亮。没有型号的配件（挂绳、支架等）就原样显示。
 */
const PHONE_MODEL_PATTERN =
  /(iPhone\s*(?:SE\s*)?\d{1,2}(?:\s*(?:Pro\s*Max|ProMax|Pro|Plus|Max|Mini|Air))?|Galaxy\s*[A-Z]\d{1,3}(?:\s*(?:Ultra|Plus|\+))?|Pixel\s*\d{1,2}(?:\s*(?:Pro\s*XL|ProMax|Pro|XL|a))?)/i;

const MODEL_HIGHLIGHT: React.CSSProperties = {
  background: '#fef3c7',
  color: '#b45309',
  padding: '0 4px',
  borderRadius: 4,
};

/** 把文本里的手机型号高亮；找不到型号就原样返回 */
export function highlightPhoneModel(text: string): React.ReactNode {
  const match = text.match(PHONE_MODEL_PATTERN);
  if (!match || match.index === undefined) return text;

  const start = match.index;
  const model = match[0];
  return (
    <>
      {text.slice(0, start)}
      <span style={MODEL_HIGHLIGHT}>{model}</span>
      {text.slice(start + model.length)}
    </>
  );
}

const NO_IMAGE =
  'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMjAiIGhlaWdodD0iMTIwIj48cmVjdCB3aWR0aD0iMTIwIiBoZWlnaHQ9IjEyMCIgZmlsbD0iI2UyZThmMCIvPjx0ZXh0IHg9IjYwIiB5PSI2NSIgZm9udC1zaXplPSIxMiIgZmlsbD0iIzk0YTNiOCIgdGV4dC1hbmNob3I9Im1pZGRsZSI+5peg5Zu-54mHPC90ZXh0Pjwvc3ZnPg==';

interface PackingItemsProps {
  items: PackingLine[];
}

/**
 * 应装商品卡片墙：装箱核对和扫码出库共用。
 *
 * 等宽网格用 auto-fill（不是 auto-fit）：只按能放下的列数切分，
 * 最后一行不会把卡片拉宽，所以每张卡片尺寸完全一致。
 */
const PackingItems: React.FC<PackingItemsProps> = ({ items }) => (
  <div
    className="packing-items"
    style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
      gap: 12,
    }}
  >
    {items.length === 0 && (
      <div style={{ gridColumn: '1 / -1' }}>
        <Empty description="该清单没有可装商品" />
      </div>
    )}
    {items.map((line, index) => (
      <div key={`${line.sku}-${line.variant_title}-${index}`} style={{ minWidth: 0 }}>
        <div
          className="packing-item"
          style={{ height: '100%', border: '1px solid var(--cl-border)', borderRadius: 8, background: '#fff' }}
        >
          <div className="packing-item__layout" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div
              className="packing-item__image"
              style={{
                width: '100%',
                // 图片区强制 1:1，图片撑满，现场看得最清楚
                aspectRatio: '1 / 1',
                background: '#f8fafc',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: 10,
                overflow: 'hidden',
              }}
            >
              <Image
                src={line.image_url || undefined}
                style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                preview={Boolean(line.image_url)}
                fallback={NO_IMAGE}
              />
            </div>
            <div className="packing-item__description" style={{ padding: 12, display: 'flex', flexDirection: 'column', flex: 1 }}>
              {/*
                固定两行高度：标题 1 行还是 2 行都不影响卡片高度。
                这里用 CSS 截断而不是 antd 的 ellipsis，因为要往里塞高亮的型号节点。
              */}
              <div
                title={line.variant_title || line.title}
                style={{
                  height: 44,
                  lineHeight: '22px',
                  fontSize: 16,
                  fontWeight: 600,
                  color: '#0f172a',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}
              >
                {highlightPhoneModel(line.variant_title || line.title)}
              </div>
              <div style={{ height: 20, overflow: 'hidden' }}>
                {line.variant_title && (
                  <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }} ellipsis={{ tooltip: line.title }}>
                    {line.title}
                  </Typography.Text>
                )}
              </div>
              <div
                className="packing-item__quantity"
                style={{
                  marginTop: 'auto',
                  paddingTop: 10,
                  display: 'flex',
                  alignItems: 'baseline',
                  justifyContent: 'flex-end',
                  gap: 6,
                }}
              >
                <span style={{ fontSize: 12, color: '#64748b' }}>应装</span>
                <span style={{ fontSize: 36, fontWeight: 700, color: '#1e40af', lineHeight: 1 }}>
                  {line.quantity}
                </span>
                <span style={{ fontSize: 13, color: '#64748b' }}>件</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    ))}
  </div>
);

export default PackingItems;
