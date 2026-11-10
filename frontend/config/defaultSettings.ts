import type { ProLayoutProps } from '@ant-design/pro-components';

/**
 * @name
 */
const Settings: ProLayoutProps & {
  pwa?: boolean;
  logo?: string;
} = {
  navTheme: 'light',
  // 主色：专业深蓝（金融/数据类后台常用），配合琥珀色做强调
  colorPrimary: '#1E40AF',
  layout: 'side',
  contentWidth: 'Fluid',
  fixedHeader: true,
  fixSiderbar: true,
  siderWidth: 208,
  colorWeak: false,
  title: 'CaseLedger',
  pwa: true,
  logo: '/logo.svg',
  iconfontUrl: '',
  token: {
    // 布局令牌：https://procomponents.ant.design/components/layout#token
    bgLayout: '#F5F7FB',
    header: {
      colorBgHeader: '#FFFFFF',
      colorTextMenu: '#475569',
      colorTextMenuSelected: '#1E40AF',
      colorTextRightActionsItem: '#64748B',
    },
    sider: {
      colorMenuBackground: '#FFFFFF',
      colorTextMenuTitle: '#0F172A',
      colorTextMenu: '#475569',
      colorTextMenuSelected: '#1E40AF',
      colorTextMenuItemHover: '#1E40AF',
      colorBgMenuItemHover: '#F5F8FF',
      colorBgMenuItemSelected: '#EEF4FF',
      colorMenuItemDivider: 'transparent',
    },
    pageContainer: {
      colorBgPageContainer: 'transparent',
      paddingInlinePageContainerContent: 24,
      paddingBlockPageContainerContent: 20,
    },
  },
};

export default Settings;
