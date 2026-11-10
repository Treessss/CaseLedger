import {
  CreditCardOutlined,
  LineChartOutlined,
  ShoppingCartOutlined,
  WalletOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { Card, Col, DatePicker, Row, Spin, Statistic, Table } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import React, { useEffect, useState } from 'react';
import {
  getDashboardStats,
  getRecentOrders,
  getWorkload,
  type WorkloadSummary,
} from '@/services/dashboard';
import { history, useModel } from '@umijs/max';
import type { OrderItem } from '@/services/orders';
import { getCurrentBeijingTime } from '@/utils/timezone';

interface DashboardStats {
  order_count: number;
  total_revenue: number;
  total_expenses: number;
  total_profit: number;
  profit_margin: number;
}

const kpiHeaderStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: 6,
};

const kpiTitleStyle: React.CSSProperties = {
  color: '#64748B',
  fontSize: 13,
  fontWeight: 500,
};

const Dashboard: React.FC = () => {
  // 仪表板按账号实际拿到的权限拼装：能看到什么就只请求什么，
  // 避免只有「仪表板 + 订单」的账号去调财务报表接口拿 403。
  const { initialState } = useModel('@@initialState');
  const currentUser = initialState?.currentUser;
  const isAdmin = currentUser?.access === 'admin';
  const granted = new Set(currentUser?.permissions ?? []);
  const can = (key: string) => Boolean(isAdmin || granted.has(key));
  const canSeeWorkload = can('orders');
  const canSeeFinance = can('reports');

  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentOrders, setRecentOrders] = useState<OrderItem[]>([]);
  const [workload, setWorkload] = useState<WorkloadSummary | null>(null);

  const [selectedMonth, setSelectedMonth] = useState<Dayjs>(
    dayjs(getCurrentBeijingTime('YYYY-MM-DD')),
  );

  useEffect(() => {
    fetchDashboardData();
  }, [selectedMonth]);

  const fetchDashboardData = async () => {
    try {
      setLoading(true);
      // 计算选择月份的开始和结束日期
      const startDate = selectedMonth.startOf('month').format('YYYY-MM-DD');
      const endDate = selectedMonth.endOf('month').format('YYYY-MM-DD');

      const [statsRes, ordersRes, workloadRes] = await Promise.all([
        canSeeFinance
          ? getDashboardStats(startDate, endDate)
          : Promise.resolve({ success: false } as const),
        getRecentOrders(),
        canSeeWorkload
          ? getWorkload()
          : Promise.resolve({ success: false } as const),
      ]);

      if (statsRes.success) {
        setStats(statsRes.data);
      }

      if (ordersRes.success) {
        setRecentOrders(ordersRes.data || []);
      }

      if (workloadRes.success) {
        setWorkload(workloadRes.data);
      }
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleMonthChange = (date: Dayjs | null) => {
    if (date) {
      setSelectedMonth(date);
    }
  };

  if (loading) {
    return (
      <PageContainer>
        <div style={{ textAlign: 'center', padding: '50px' }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }
  return (
    <PageContainer
      title="仪表板"
      subTitle={
        canSeeFinance ? 'CaseLedger 数据概览' : '你的账号主要看作业进度'
      }
      extra={
        // 月份选择只影响财务统计，没报表权限就不显示
        canSeeFinance
          ? [
              <DatePicker
                key="month-picker"
                picker="month"
                value={selectedMonth}
                onChange={handleMonthChange}
                format="YYYY年MM月"
                placeholder="选择月份"
              />,
            ]
          : undefined
      }
    >
      {/* 今日作业：先告诉现场今天还剩多少活，点数字直达对应页面 */}
      {canSeeWorkload && (
      <Card
        size="small"
        title="今日作业"
        style={{ marginBottom: 16 }}
        extra={<span style={kpiTitleStyle}>点数字直接进入对应页面</span>}
      >
        <Row gutter={[16, 12]}>
          {[
            { key: 'pending_ship', label: '待发货', to: '/shipping', color: '#F59E0B' },
            { key: 'ready_to_ship', label: '可出库', to: '/shipping', color: '#16A34A' },
            { key: 'awaiting_purchase', label: '待采购', to: '/purchases', color: '#DC2626' },
            { key: 'shipped_today', label: '今日已出库', to: '/shipping', color: '#1E40AF' },
          ].map((item) => (
            <Col xs={12} md={6} key={item.key}>
              <button
                type="button"
                onClick={() => history.push(item.to)}
                style={{
                  cursor: 'pointer',
                  padding: '2px 0',
                  border: 'none',
                  background: 'transparent',
                  textAlign: 'left',
                  width: '100%',
                }}
              >
                <Statistic
                  title={item.label}
                  value={workload?.[item.key as keyof WorkloadSummary] ?? 0}
                  valueStyle={{ color: item.color, fontSize: 26 }}
                  suffix={<span style={{ fontSize: 13, color: '#94a3b8' }}>件</span>}
                />
              </button>
            </Col>
          ))}
        </Row>
      </Card>
      )}

      {canSeeFinance && (
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <Card
            styles={{ body: { padding: '18px 20px' } }}
            style={{ borderLeft: '3px solid #1E40AF' }}
          >
            <div style={kpiHeaderStyle}>
              <span style={kpiTitleStyle}>总订单数</span>
              <ShoppingCartOutlined
                style={{ color: '#1E40AF', fontSize: 18 }}
              />
            </div>
            <Statistic
              value={stats?.order_count || 0}
              precision={0}
              suffix="单"
              valueStyle={{
                fontSize: 28,
                fontWeight: 600,
                whiteSpace: 'nowrap',
              }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card
            styles={{ body: { padding: '18px 20px' } }}
            style={{ borderLeft: '3px solid #16A34A' }}
          >
            <div style={kpiHeaderStyle}>
              <span style={kpiTitleStyle}>总收入</span>
              <WalletOutlined style={{ color: '#16A34A', fontSize: 18 }} />
            </div>
            <Statistic
              value={stats?.total_revenue || 0}
              precision={2}
              prefix="¥"
              valueStyle={{
                fontSize: 28,
                fontWeight: 600,
                color: '#15803D',
                whiteSpace: 'nowrap',
              }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card
            styles={{ body: { padding: '18px 20px' } }}
            style={{ borderLeft: '3px solid #DC2626' }}
          >
            <div style={kpiHeaderStyle}>
              <span style={kpiTitleStyle}>总费用</span>
              <CreditCardOutlined style={{ color: '#DC2626', fontSize: 18 }} />
            </div>
            <Statistic
              value={stats?.total_expenses || 0}
              precision={2}
              prefix="¥"
              valueStyle={{
                fontSize: 28,
                fontWeight: 600,
                color: '#B91C1C',
                whiteSpace: 'nowrap',
              }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card
            styles={{ body: { padding: '18px 20px' } }}
            style={{
              borderLeft: `3px solid ${(stats?.total_profit || 0) >= 0 ? '#16A34A' : '#DC2626'}`,
            }}
          >
            <div style={kpiHeaderStyle}>
              <span style={kpiTitleStyle}>净利润</span>
              <LineChartOutlined
                style={{
                  color:
                    (stats?.total_profit || 0) >= 0 ? '#16A34A' : '#DC2626',
                  fontSize: 18,
                }}
              />
            </div>
            <Statistic
              value={stats?.total_profit || 0}
              precision={2}
              prefix="¥"
              valueStyle={{
                fontSize: 28,
                fontWeight: 600,
                whiteSpace: 'nowrap',
                color: (stats?.total_profit || 0) >= 0 ? '#15803D' : '#B91C1C',
              }}
            />
          </Card>
        </Col>
      </Row>
      )}

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24}>
          <Card title="最近订单" size="small">
            <Table
              dataSource={recentOrders}
              columns={[
                {
                  title: '订单号',
                  dataIndex: 'order_number',
                  key: 'order_number',
                },
                {
                  title: '客户',
                  dataIndex: 'customer_name',
                  key: 'customer_name',
                },
                {
                  title: '金额',
                  dataIndex: 'total_price_cny',
                  key: 'total_price_cny',
                  render: (value: number) => `¥${(value || 0).toFixed(2)}`,
                },
              ]}
              pagination={false}
              size="small"
              scroll={{ x: 520 }}
            />
          </Card>
        </Col>
      </Row>
    </PageContainer>
  );
};

export default Dashboard;
