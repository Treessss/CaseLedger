import React from 'react';
import { render, screen } from '@testing-library/react';
import BrowserStyleProvider, { getStyleCompatibility } from './index';

test('modern browsers keep the original selector priority and logical styles', () => {
  expect(getStyleCompatibility(() => true)).toEqual({legacySelectors: false, legacyLogicalProperties: false});
});

test('old Safari enables both selector and logical property conversion', () => {
  expect(getStyleCompatibility(() => false)).toEqual({legacySelectors: true, legacyLogicalProperties: true});
  expect(getStyleCompatibility()).toEqual({legacySelectors: true, legacyLogicalProperties: true});
});

test('selector conversion can be enabled independently of logical properties', () => {
  expect(getStyleCompatibility(property => !property.startsWith('selector')))
    .toEqual({legacySelectors: true, legacyLogicalProperties: false});
});

test('legacy provider preserves children and does not leave its flex probe behind', () => {
  render(<BrowserStyleProvider><div>兼容页面</div></BrowserStyleProvider>);
  expect(screen.getByText('兼容页面')).toBeTruthy();
  expect(document.querySelector('[style*="visibility: hidden"]')).toBeNull();
  expect(document.documentElement.classList.contains('cl-legacy-selectors')).toBe(true);
});
