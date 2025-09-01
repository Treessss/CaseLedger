import { PageContainer } from '@ant-design/pro-components';
import { Card, Col, Row, DatePicker, Select, Button, Table, Divider, Spin, message } from 'antd';
import { DownloadOutlined, PrinterOutlined } from '@ant-design/icons';
import React, { useState, useEffect } from 'react';
import type { ColumnsType } from 'antd/es/table';
import { getFinancialReport, exportReport, type FinancialData, type ReportParams } from '@/services/reports';
import dayjs from 'dayjs';

const { RangePicker } = DatePicker;
const { Option } = Select;



const Reports: React.FC = () => {
  const [reportType, setReportType] = useState<string>('monthly');
  const [dateRange, setDateRange] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<FinancialData[]>([]);
  const [summary, setSummary] = useState<any>(null);

  const columns: ColumnsType<FinancialData> = [
    {
      title: '日期',
      dataIndex: 'date',
      key: 'date',
    },
    {
      title: '收入 (¥)',
      dataIndex: 'income',
      key: 'income',
      render: (value: number) => value.toLocaleString('zh-CN', { minimumFractionDigits: 2 }),
      align: 'right',
    },
    {
      title: '支出 (¥)',
      dataIndex: 'expense',
      key: 'expense',
      render: (value: number) => value.toLocaleString('zh-CN', { minimumFractionDigits: 2 }),
      align: 'right',
    },
    {
      title: '利润 (¥)',
      dataIndex: 'profit',
      key: 'profit',
      render: (value: number) => (
        <span style={{ color: value >= 0 ? '#52c41a' : '#ff4d4f' }}>
          {value.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
        </span>
      ),
      align: 'right',
    },
  ];

  useEffect(() => {
    fetchReportData();
  }, [reportType, dateRange]);

  const fetchReportData = async () => {
    try {
      setLoading(true);
      const params: ReportParams = {
        report_type: reportType as any,
        start_date: dateRange?.[0]?.format('YYYY-MM-DD'),
        end_date: dateRange?.[1]?.format('YYYY-MM-DD'),
      };
      
      const response = await getFinancialReport(params);
      if (response.success) {
        setData(response.data.financial_data || []);
        setSummary(response.data.summary || {});
      } else {
        message.error(response.message || '获取报表数据失败');
      }
    } catch (error) {
      message.error('获取报表数据失败');
      console.error('Failed to fetch report data:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async (format: 'excel' | 'pdf') => {
    try {
      const params = {
        report_type: reportType as any,
        start_date: dateRange?.[0]?.format('YYYY-MM-DD'),
        end_date: dateRange?.[1]?.format('YYYY-MM-DD'),
        format,
      };
      
      const response = await exportReport(params);
      
      // 创建下载链接
      const url = window.URL.createObjectURL(new Blob([response]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `财务报表_${dayjs().format('YYYY-MM-DD')}.${format === 'excel' ? 'xlsx' : 'pdf'}`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      
      message.success('导出成功');
    } catch (error) {
      message.error('导出失败');
      console.error('Failed to export report:', error);
    }
  };

  const handlePrint = () => {
    console.log('打印报表');
    // 这里添加打印逻辑
    window.print();
  };

  const handleGenerateReport = () => {
    console.log('生成报表:', { reportType, dateRange });
    // 这里添加生成报表的逻辑
  };

  return (
    <PageContainer
      title="财务报表"
      subTitle="查看和分析财务数据"
    >
      {/* 报表筛选条件 */}
      <Card title="报表筛选" style={{ marginBottom: 16 }}>
        <Row gutter={[16, 16]} align="middle">
          <Col xs={24} sm={8} md={6}>
            <label>报表类型：</label>
            <Select
              value={reportType}
              onChange={setReportType}
              style={{ width: '100%', marginTop: 4 }}
            >
              <Option value="daily">日报表</Option>
              <Option value="weekly">周报表</Option>
              <Option value="monthly">月报表</Option>
              <Option value="quarterly">季度报表</Option>
              <Option value="yearly">年度报表</Option>
            </Select>
          </Col>
          <Col xs={24} sm={10} md={8}>
            <label>时间范围：</label>
            <RangePicker
              value={dateRange}
              onChange={setDateRange}
              style={{ width: '100%', marginTop: 4 }}
              placeholder={['开始日期', '结束日期']}
            />
          </Col>
          <Col xs={24} sm={6} md={4}>
            <Button 
              type="primary" 
              onClick={handleGenerateReport}
              style={{ marginTop: 20 }}
              block
            >
              生成报表
            </Button>
          </Col>
        </Row>
      </Card>

      {/* 财务概览 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Card>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 14, color: '#666' }}>总收入</div>
              <div style={{ fontSize: 24, fontWeight: 'bold', color: '#52c41a' }}>
                ¥{(summary?.total_income || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
              </div>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 14, color: '#666' }}>总支出</div>
              <div style={{ fontSize: 24, fontWeight: 'bold', color: '#ff4d4f' }}>
                ¥{(summary?.total_expense || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
              </div>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 14, color: '#666' }}>净利润</div>
              <div style={{ 
                fontSize: 24, 
                fontWeight: 'bold', 
                color: (summary?.total_profit || 0) >= 0 ? '#52c41a' : '#ff4d4f' 
              }}>
                ¥{(summary?.total_profit || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
              </div>
            </div>
          </Card>
        </Col>
      </Row>

      {/* 详细报表 */}
      <Card 
        title="财务明细报表"
        extra={[
          <Button key="print" icon={<PrinterOutlined />} onClick={handlePrint}>
            打印
          </Button>,
          <Button key="export-excel" type="primary" icon={<DownloadOutlined />} onClick={() => handleExport('excel')}>
            导出Excel
          </Button>,
          <Button key="export-pdf" icon={<DownloadOutlined />} onClick={() => handleExport('pdf')}>
            导出PDF
          </Button>,
        ]}
      >
        <Spin spinning={loading}>
          <Table
            columns={columns}
            dataSource={data.map((item, index) => ({ ...item, key: index.toString() }))}
            pagination={false}
            size="middle"
            summary={() => (
              <Table.Summary>
                <Table.Summary.Row>
                  <Table.Summary.Cell index={0}>
                    <strong>总计</strong>
                  </Table.Summary.Cell>
                  <Table.Summary.Cell index={1}>
                    <strong>¥{(summary?.total_income || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</strong>
                  </Table.Summary.Cell>
                  <Table.Summary.Cell index={2}>
                    <strong>¥{(summary?.total_expense || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</strong>
                  </Table.Summary.Cell>
                  <Table.Summary.Cell index={3}>
                    <strong style={{ color: (summary?.total_profit || 0) >= 0 ? '#52c41a' : '#ff4d4f' }}>
                      ¥{(summary?.total_profit || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
                    </strong>
                  </Table.Summary.Cell>
                </Table.Summary.Row>
              </Table.Summary>
            )}
          />
        </Spin>
        
        <Divider />
        
        <div style={{ textAlign: 'center', color: '#666', fontSize: 12 }}>
          报表生成时间：{new Date().toLocaleString('zh-CN')}
        </div>
      </Card>

      <style>{`
        .summary-row {
          background-color: #fafafa !important;
          font-weight: bold;
        }
        .summary-row td {
          border-top: 2px solid #d9d9d9 !important;
        }
      `}</style>
    </PageContainer>
  );
};

export default Reports;