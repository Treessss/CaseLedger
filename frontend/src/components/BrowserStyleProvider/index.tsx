import { StyleProvider, legacyLogicalPropertiesTransformer } from '@ant-design/cssinjs';
import React from 'react';

type Supports = (property: string, value?: string) => boolean;

export function getStyleCompatibility(supports?: Supports) {
  return {
    legacySelectors: !supports?.('selector(:where(*))'),
    legacyLogicalProperties: !supports?.('margin-inline-start', '1px'),
  };
}

// CSS.supports('gap', '1px') detects grid gap, not flex gap, on Safari 13.
function supportsFlexGap() {
  const flex = document.createElement('div');
  flex.style.cssText = 'display:flex;flex-direction:column;row-gap:1px;position:absolute;visibility:hidden;';
  for (let index = 0; index < 2; index += 1) {
    const child = document.createElement('div');
    child.style.height = '1px';
    flex.appendChild(child);
  }
  document.body.appendChild(flex);
  const supported = flex.scrollHeight === 3;
  flex.remove();
  return supported;
}

const BrowserStyleProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [compatibility] = React.useState(() => {
    const supports = typeof CSS !== 'undefined' && typeof CSS.supports === 'function'
      ? (property: string, value?: string) => value === undefined ? CSS.supports(property) : CSS.supports(property, value)
      : undefined;
    const result = getStyleCompatibility(supports);
    if (typeof document !== 'undefined') {
      document.documentElement.classList.toggle('cl-legacy-selectors', result.legacySelectors);
      document.documentElement.classList.toggle('cl-legacy-flex-gap', !supportsFlexGap());
    }
    return result;
  });
  return <StyleProvider
    hashPriority={compatibility.legacySelectors ? 'high' : 'low'}
    transformers={compatibility.legacyLogicalProperties ? [legacyLogicalPropertiesTransformer] : []}
  >{children}</StyleProvider>;
};

export default BrowserStyleProvider;
