import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('startup uses system fonts without external font stylesheets or font downloads', () => {
  const config = readFileSync(resolve(process.cwd(), 'config/config.ts'), 'utf8');
  const styles = readFileSync(resolve(process.cwd(), 'src/global.less'), 'utf8');
  expect(config).not.toMatch(/fonts\.(googleapis|gstatic)\.com/);
  expect(styles).not.toMatch(/mdn\.alipayobjects\.com/);
  expect(config).toContain('-apple-system');
  expect(styles).toContain('-apple-system');
});

test('build explicitly targets Safari 13 and audits generated JavaScript', () => {
  const config = readFileSync(resolve(process.cwd(), 'config/config.ts'), 'utf8');
  const pkg = JSON.parse(readFileSync(resolve(process.cwd(), 'package.json'), 'utf8'));
  expect(config).toMatch(/safari:\s*13/);
  expect(config).toMatch(/ios:\s*13/);
  expect(pkg.scripts.build).toContain('check-legacy-syntax.cjs');
});
