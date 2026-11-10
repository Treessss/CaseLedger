import { createConfig } from '@umijs/max/test.js';

export default async (): Promise<any> => {
  const config = createConfig({
    target: 'browser',
  });
  return {
    ...config,
    moduleNameMapper: {
      ...config.moduleNameMapper,
      // Browser tests need generated runtime exports, not Umi's Node CLI entry.
      '^umi$': '<rootDir>/src/.umi-test/exports.ts',
      '^@umijs/max$': '<rootDir>/src/.umi-test/exports.ts',
      '^@/(.*)$': '<rootDir>/src/$1',
      '^@@/(.*)$': '<rootDir>/src/.umi-test/$1',
    },
    testEnvironmentOptions: {
      ...(config?.testEnvironmentOptions || {}),
      url: 'http://localhost:8000',
    },
    setupFiles: [...(config.setupFiles || []), './tests/setupTests.jsx'],
    globals: {
      ...config.globals,
      localStorage: null,
    },
  };
};
