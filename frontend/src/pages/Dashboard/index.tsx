import { PageContainer } from '@ant-design/pro-components';
import { Card, Col, Row, Statistic, Table, Typography, Spin, DatePicker } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined } from '@ant-design/icons';
import React, { useEffect, useState } from 'react';

import { getDashboardStats, getRecentOrders } from '@/services/dashboard';
import { OrderItem } from '@/services/orders';
import dayjs, { Dayjs } from 'dayjs';

const { Title } = Typography;

interface DashboardStats {
  order_count: number;
  total_revenue: number;
  total_expenses: number;
  total_profit: number;
  profit_margin: number;
}

const Dashboard: React.FC = () => {
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recentOrders, setRecentOrders] = useState<OrderItem[]>([]);

  const [selectedMonth, setSelectedMonth] = useState<Dayjs>(dayjs());

  useEffect(() => {
    fetchDashboardData();
  }, [selectedMonth]);

  const fetchDashboardData = async () => {
    try {
      setLoading(true);
      // 计算选择月份的开始和结束日期
      const startDate = selectedMonth.startOf('month').format('YYYY-MM-DD');
      const endDate = selectedMonth.endOf('month').format('YYYY-MM-DD');
      
      const [statsRes, ordersRes] = await Promise.all([
        getDashboardStats(startDate, endDate),
        getRecentOrders()
      ]);
      
      if (statsRes.success) {
        setStats(statsRes.data);
      }
      
      if (ordersRes.success) {
        setRecentOrders(ordersRes.data || []);
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
      subTitle="CaseLedger 数据概览"
      extra={[
        <DatePicker
          key="month-picker"
          picker="month"
          value={selectedMonth}
          onChange={handleMonthChange}
          format="YYYY年MM月"
          placeholder="选择月份"
        />
      ]}
    >
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic
              title="总订单数"
              value={stats?.order_count || 0}
              precision={0}
              valueStyle={{ color: '#3f8600' }}
              prefix={<ArrowUpOutlined />}
              suffix="个"
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic
              title="总收入"
              value={stats?.total_revenue || 0}
              precision={2}
              valueStyle={{ color: '#3f8600' }}
              prefix={<ArrowUpOutlined />}
              suffix="元"
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic
              title="总费用"
              value={stats?.total_expenses || 0}
              precision={2}
              valueStyle={{ color: '#cf1322' }}
              prefix={<ArrowDownOutlined />}
              suffix="元"
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Card>
            <Statistic
              title="净利润"
              value={stats?.total_profit || 0}
              precision={2}
              valueStyle={{ color: stats?.total_profit && stats.total_profit > 0 ? '#3f8600' : '#cf1322' }}
              prefix={stats?.total_profit && stats.total_profit > 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />}
              suffix="元"
            />
          </Card>
        </Col>
      </Row>
      
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
            />
          </Card>
        </Col>

      </Row>
    </PageContainer>
  );
};

export default Dashboard;