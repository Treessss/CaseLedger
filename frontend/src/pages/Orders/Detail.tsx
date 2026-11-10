import {
  ArrowLeftOutlined,
  CopyOutlined,
  EditOutlined,
  LeftOutlined,
  PrinterOutlined,
  RightOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { PageContainer, ProDescriptions } from '@ant-design/pro-components';
import { history, useParams, useAccess } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Grid,
  Image,
  Input,
  InputNumber,
  Alert,
  message,
  Modal,
  Popconfirm,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import React, { useEffect, useRef, useState } from 'react';
import {
  getOrderDetail,
  getOrderNeighbors,
  getOrderPurchaseLists,
  addOrderParcel,
  deleteOrderParcel,
  fetchFourPxLabel,
  saveShippingLabel,
  scanOrderOutbound,
  setOrderParcelCount,
  type OrderNeighbor,
  type ShippingParcel,
  refreshOrderContact,
  syncFourPxFreight,
  writebackShopifyTracking,
  type FourPxFreight,
} from '@/services/orders';
import { getErrorMessage } from '@/utils/error';
import { getExchangeRates } from '@/services/exchange';
import {
  formatShippingDeclaration,
  getShippingContact,
  type ShippingAddress,
} from '@/utils/shippingDeclaration';
import { formatBeijingTime } from '@/utils/timezone';

interface OrderDetailItem {
  key: string;
  imageUrl?: string;
  productName: string;
  quantity: number;
  unitPrice: number;
  totalPrice: number;
}

interface OrderData {
  id: number;
  order_number: string;
  customer_name?: string;
  customer_phone?: string;
  customer_email?: string;
  shipping_address?: ShippingAddress | null;
  total_price: number;
  currency?: string;
  actual_received?: number;
  actual_received_source?: 'gateway' | 'estimated' | 'refund_pending';
  settlement_amount?: number | null;
  settlement_currency?: string | null;
  settlement_amount_cny?: number | null;
  settlement_exchange_rate?: number | null;
  exchange_rate_refresh_currencies?: string[];
  gross_profit_cny?: number;
  after_sale_cost_cny?: number;
  after_sales?: Array<{ id: number; customer_reference: string; kind: string; status: string }>;
  product_cost?: number;
  shipping_cost?: number;
  shipping_cost_cny?: number | null;
  shipping_cost_source?: string | null;
  financial_status: string;
  fulfillment_status: string;
  warehouse_status: 'pending' | 'shipped' | 'legacy_unverified' | 'legacy_partial_unverified';
  shipping_label_number?: string | null;
  shipping_label_source?: string | null;
  expected_parcel_count?: number;
  parcels?: ShippingParcel[];
  shipped_at?: string | null;
  procurement_status?: 'not_started' | 'purchasing' | 'shortage' | 'partially_stocked' | 'ready_to_ship' | 'stocked';
  procurement_needed_quantity?: number;
  created_at: string;
  updated_at: string;
  shopify_details_synced_at?: string | null;
  note?: string;
  line_items?: any[];
  payments?: any[];
  presentment_currency?: string;
  fulfillments?: any[];
  fourpx_freight?: FourPxFreight | null;
}

const OrderDetail: React.FC = () => {
  const access = useAccess();
  const screens = Grid.useBreakpoint();
  const descriptionColumns = screens.md ? 2 : 1;
  const { id } = useParams<{ id: string }>();
  const [orderData, setOrderData] = useState<OrderData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshingContact, setRefreshingContact] = useState(false);
  // 该订单出现在哪些采购清单里（只有具备履约权限的人才需要看）
  const [purchaseLists, setPurchaseLists] = useState<
    Array<{ id: number; name: string; status: string; order_count: number }>
  >([]);
  const [labelOpen, setLabelOpen] = useState(false);
  const [parcelCountOpen, setParcelCountOpen] = useState(false);
  const [parcelCountDraft, setParcelCountDraft] = useState(1);
  const [scanOpen, setScanOpen] = useState(false);
  const [labelDraft, setLabelDraft] = useState('');
  const [scanDraft, setScanDraft] = useState('');
  const [shippingBusy, setShippingBusy] = useState(false);
  const [shopifyWritebackBusy, setShopifyWritebackBusy] = useState(false);
  const [fetchingLabelId, setFetchingLabelId] = useState<number | null>(null);
  const [syncingFreight, setSyncingFreight] = useState(false);
  const [labelPdfUrls, setLabelPdfUrls] = useState<Record<number, string>>({});
  const [neighbors, setNeighbors] = useState<{
    previous: OrderNeighbor | null;
    next: OrderNeighbor | null;
  }>({ previous: null, next: null });
  const autoRefreshAttempted = useRef(false);
  const rateRefreshStarted = useRef<Set<number>>(new Set());
  const activeOrderId = useRef<number | null>(null);

  // 从 Shopify 回源拉客户信息与收货地址（REST 同步常常拿不到门牌地址）
  const handleRefreshContact = async (silent = false) => {
    if (!id) return;
    try {
      setRefreshingContact(true);
      const res = await refreshOrderContact(Number(id));
      if (res.success) {
        setOrderData(res.data as unknown as OrderData);
        if (!silent)
          message.success('已从 Shopify 更新订单详情、商品图和物流信息');
      } else {
        if (!silent) message.warning(res.message || '没能拉到新的订单详情');
      }
    } catch (error) {
      if (!silent)
        message.error(getErrorMessage(error, '更新 Shopify 订单详情失败'));
    } finally {
      setRefreshingContact(false);
    }
  };

  // 获取订单详情
  const fetchOrderDetail = async () => {
    if (!id) {
      message.error('订单ID不存在');
      return;
    }

    try {
      setLoading(true);
      const data = await getOrderDetail(Number(id));

      if (data.success) {
        setOrderData(data.data as unknown as OrderData);
        const orderId = Number(id);
        const staleCurrencies = data.data.exchange_rate_refresh_currencies || [];
        if (staleCurrencies.length && !rateRefreshStarted.current.has(orderId)) {
          rateRefreshStarted.current.add(orderId);
          void Promise.all(staleCurrencies.map((from_currency) =>
            getExchangeRates({ from_currency, to_currency: 'CNY' }),
          )).then(async (results) => {
            if (!results.some((result) => result.success) || activeOrderId.current !== orderId) return;
            const refreshed = await getOrderDetail(orderId);
            if (refreshed.success && activeOrderId.current === orderId) {
              setOrderData(refreshed.data as unknown as OrderData);
            }
          });
        }
      } else {
        message.error(data.message || '获取订单详情失败');
      }
    } catch (error) {
      console.error('获取订单详情失败:', error);
      message.error('获取订单详情失败');
    } finally {
      setLoading(false);
    }
  };

  const fetchNeighbors = async () => {
    if (!id) return;
    try {
      const data = await getOrderNeighbors(Number(id));
      if (data.success) setNeighbors(data.data);
    } catch {
      setNeighbors({ previous: null, next: null });
    }
  };

  useEffect(() => {
    activeOrderId.current = Number(id);
    autoRefreshAttempted.current = false;
    fetchOrderDetail();
    fetchNeighbors();
    // 反查采购清单：只有具备履约权限的人才会用到（无权限时接口也会 403，直接忽略）
    getOrderPurchaseLists(Number(id))
      .then((response) => setPurchaseLists(response.data || []))
      .catch(() => setPurchaseLists([]));
  }, [id]);

  // 历史订单尚未保存商品图时，首次进入详情自动从 Shopify 补一次。
  useEffect(() => {
    const items = orderData?.line_items || [];
    if (
      !autoRefreshAttempted.current &&
      items.length > 0 &&
      items.every((item: any) => !item.image_url)
    ) {
      autoRefreshAttempted.current = true;
      handleRefreshContact(true);
    }
  }, [orderData]);

  // 订单商品明细
  const orderItems: OrderDetailItem[] =
    orderData?.line_items?.map((item: any, index: number) => ({
      key: index.toString(),
      imageUrl: item.image_url,
      productName: [item.title || item.name || '未知商品', item.variant_title]
        .filter(Boolean)
        .join(' / '),
      quantity: item.quantity || 0,
      unitPrice: parseFloat(item.price || '0'),
      totalPrice: parseFloat(item.price || '0') * (item.quantity || 0),
    })) || [];

  // 收货地址：Shopify 只对已获批的店铺/应用返回姓名、详细地址，其余只给国家省份
  const shippingContact = getShippingContact(orderData?.shipping_address, {
    name: orderData?.customer_name,
    phone: orderData?.customer_phone,
    email: orderData?.customer_email,
  });
  const shippingIncomplete = !shippingContact.name || !shippingContact.phone || !shippingContact.address1;
  const shippingFields = [
    { label: '收件人', value: shippingContact.name },
    { label: '联系电话', value: shippingContact.phone },
    { label: '邮箱', value: shippingContact.email },
    { label: '地址 1', value: shippingContact.address1 },
    { label: '地址 2', value: shippingContact.address2 },
    { label: '城市', value: shippingContact.city },
    { label: '省 / 州', value: shippingContact.province },
    { label: '邮编', value: shippingContact.postalCode },
    { label: '国家 / 地区', value: shippingContact.country },
  ];

  const handleCopyDeclaration = async () => {
    if (!orderData) return;
    const text = formatShippingDeclaration(orderData.order_number, shippingContact);
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const field = document.createElement('textarea');
        field.value = text;
        field.style.position = 'fixed';
        field.style.left = '-9999px';
        document.body.appendChild(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        if (!copied) throw new Error('clipboard unavailable');
      }
      message.success(shippingIncomplete ? '已复制已有资料，请补全缺失项后再申报' : '物流申报资料已复制');
    } catch {
      message.error('复制失败，请逐项点击复制图标');
    }
  };

  // 订单状态历史
  const statusHistory = orderData?.payments?.map((payment: any) => {
    const status = String(payment.status || '').toLowerCase();
    const succeeded = ['success', 'completed', 'paid'].includes(status);
    const failed = ['failed', 'voided', 'error'].includes(status);
    return {
      time: formatBeijingTime(payment.payment_date || payment.created_at || ''),
      status: `支付${succeeded ? '成功' : failed ? '失败' : '处理中'}`,
      description: `${payment.payment_method || '未知方式'} - ${payment.amount} ${payment.currency || 'USD'}`,
      color: succeeded ? 'green' : failed ? 'red' : 'blue',
    };
  }) || [
    {
      time: formatBeijingTime(orderData?.created_at || ''),
      status: '订单创建',
      description: '订单已创建',
      color: 'orange',
    },
  ];

  const itemColumns: ColumnsType<OrderDetailItem> = [
    {
      title: '商品图',
      dataIndex: 'imageUrl',
      key: 'imageUrl',
      width: 88,
      render: (value?: string) =>
        value ? (
          <Image
            src={value}
            width={56}
            height={56}
            style={{ objectFit: 'cover', borderRadius: 6 }}
          />
        ) : (
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: 6,
              background: '#f5f5f5',
              color: '#aaa',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 12,
            }}
          >
            暂无图片
          </div>
        ),
    },
    {
      title: '商品名称',
      dataIndex: 'productName',
      key: 'productName',
    },
    {
      title: '数量',
      dataIndex: 'quantity',
      key: 'quantity',
      align: 'center',
      width: 80,
    },
    {
      title: '单价',
      dataIndex: 'unitPrice',
      key: 'unitPrice',
      render: (value: number) =>
        `${value.toFixed(2)} ${orderData?.currency || ''}`,
      align: 'right',
      width: 130,
    },
    {
      title: '小计',
      dataIndex: 'totalPrice',
      key: 'totalPrice',
      render: (value: number) => value.toFixed(2),
      align: 'right',
      width: 120,
    },
  ];

  // 支付与到账：有网关真实数据（回执/结算流水）时优先展示真实值，并标明来源
  const paymentColumns: ColumnsType<any> = [
    {
      title: '支付方式',
      dataIndex: 'payment_method',
      key: 'payment_method',
      render: (value: string) => {
        const map: Record<string, string> = {
          shopify_payment: 'Shopify Payments',
          paypal: 'PayPal',
          stripe: 'Stripe',
          manual: '手动',
        };
        return map[value] || value || '-';
      },
    },
    {
      title: '买家实付',
      key: 'paid',
      align: 'right',
      render: (_, record) =>
        `${Number(record.amount || 0).toFixed(2)} ${record.currency || ''}`,
    },
    {
      title: `折合${orderData?.currency || '订单币种'}`,
      dataIndex: 'amount_in_order_currency',
      key: 'amount_in_order_currency',
      align: 'right',
      render: (value: number | null) =>
        value == null ? '-' : Number(value).toFixed(2),
    },
    {
      title: '手续费',
      key: 'fee',
      align: 'right',
      render: (_, record) => {
        const isReal = record.fee_source === 'gateway';
        const raw = isReal ? record.real_fee : record.total_fee;
        const currency = isReal ? record.fee_currency : orderData?.currency;
        return (
          <span>
            {raw == null ? '-' : `${Number(raw).toFixed(2)} ${currency || ''}`}
            {isReal ? (
              <Tag color="green" style={{ marginLeft: 6 }}>
                真实
              </Tag>
            ) : (
              <Tag style={{ marginLeft: 6 }}>估算</Tag>
            )}
          </span>
        );
      },
    },
    {
      title: '手续费(折合)',
      key: 'fee_in_order_currency',
      align: 'right',
      render: (_, record) =>
        record.total_fee == null ? '-' : Number(record.total_fee).toFixed(2),
    },
    {
      title: '实际到账',
      key: 'settlement',
      align: 'right',
      render: (_, record) => {
        if (record.settlement_amount != null) {
          return `${Number(record.settlement_amount).toFixed(2)} ${record.settlement_currency || ''}`;
        }
        return record.net_amount == null
          ? '-'
          : `${Number(record.net_amount).toFixed(2)} ${orderData?.currency || ''}`;
      },
    },
  ];

  const getStatusTag = (
    status: string,
    type: 'financial' | 'fulfillment' = 'financial',
  ) => {
    if (type === 'financial') {
      const statusMap = {
        pending: { color: 'orange', text: '待付款' },
        paid: { color: 'green', text: '已付款' },
        partially_paid: { color: 'blue', text: '部分付款' },
        partially_refunded: { color: 'volcano', text: '部分退款' },
        refunded: { color: 'red', text: '已退款' },
        voided: { color: 'gray', text: '已作废' },
        completed: { color: 'green', text: '已完成' },
        processing: { color: 'blue', text: '处理中' },
        cancelled: { color: 'red', text: '已取消' },
      };
      const config = statusMap[status as keyof typeof statusMap] || {
        color: 'default',
        text: status,
      };
      return <Tag color={config.color}>{config.text}</Tag>;
    } else {
      const statusMap = {
        fulfilled: { color: 'green', text: '已发货' },
        partial: { color: 'blue', text: '部分发货' },
        unfulfilled: { color: 'orange', text: '未发货' },
        cancelled: { color: 'red', text: '已取消' },
      };
      const config = statusMap[status as keyof typeof statusMap] || {
        color: 'default',
        text: status,
      };
      return <Tag color={config.color}>{config.text}</Tag>;
    }
  };

  const handleBack = () => {
    history.back();
  };

  const openNeighbor = (neighbor: OrderNeighbor | null) => {
    if (neighbor) history.push(`/orders/detail/${neighbor.id}`);
  };

  const handleEdit = () => {
    if (orderData) history.push(`/orders/edit/${orderData.id}`);
  };

  const handlePrint = () => {
    console.log('打印订单:', orderData?.id);
    window.print();
  };

  const refreshLocalOrder = async () => {
    if (!id) return;
    const response = await getOrderDetail(Number(id));
    if (response.success) setOrderData(response.data as unknown as OrderData);
  };

  const handleSaveLabel = async () => {
    if (!id || !labelDraft.trim()) return;
    setShippingBusy(true);
    try {
      if ((orderData?.expected_parcel_count || 1) > 1) {
        await addOrderParcel(Number(id), labelDraft.trim());
      } else {
        await saveShippingLabel(Number(id), labelDraft.trim());
      }
      await refreshLocalOrder();
      setLabelOpen(false);
      message.success('包裹标签已保存，订单尚未出库');
    } catch (error) {
      message.error(getErrorMessage(error, '保存物流标签单号失败'));
    } finally {
      setShippingBusy(false);
    }
  };

  const handleSetParcelCount = async () => {
    if (!id) return;
    setShippingBusy(true);
    try {
      await setOrderParcelCount(Number(id), parcelCountDraft);
      await refreshLocalOrder();
      setParcelCountOpen(false);
      message.success('应有包裹数已更新');
    } catch (error) {
      message.error(getErrorMessage(error, '设置包裹数失败'));
    } finally {
      setShippingBusy(false);
    }
  };

  const handleDeleteParcel = async (parcelId: number) => {
    if (!id || !access.fulfillment) return;
    setShippingBusy(true);
    try {
      await deleteOrderParcel(Number(id), parcelId);
      await refreshLocalOrder();
      message.success('未扫码标签已移除');
    } catch (error) {
      message.error(getErrorMessage(error, '移除包裹标签失败'));
    } finally {
      setShippingBusy(false);
    }
  };

  const handleScanOutbound = async () => {
    if (!id || !scanDraft.trim()) return;
    setShippingBusy(true);
    try {
      const response = await scanOrderOutbound(Number(id), scanDraft.trim());
      await refreshLocalOrder();
      setScanOpen(false);
      message.success(response.data.warehouse_status === 'shipped'
        ? '全部包裹已扫码，订单确认出库'
        : `已扫描 ${response.data.scanned_parcel_count}/${response.data.expected_parcel_count} 个包裹，订单尚未完成出库`);
    } catch (error) {
      message.error(getErrorMessage(error, '扫码出库失败'));
    } finally {
      setShippingBusy(false);
    }
  };

  if (loading) {
    return (
      <PageContainer title="订单详情" subTitle="查看订单的详细信息">
        <div style={{ textAlign: 'center', padding: '50px' }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!orderData) {
    return (
      <PageContainer title="订单详情" subTitle="查看订单的详细信息">
        <div style={{ textAlign: 'center', padding: '50px' }}>订单不存在</div>
      </PageContainer>
    );
  }

  return (
    <PageContainer
      title={`订单详情 - ${orderData.order_number}`}
      subTitle="查看订单的详细信息"
      extra={
        <Space wrap className="order-detail-actions">
          <Button icon={<ArrowLeftOutlined />} onClick={handleBack}>
            返回
          </Button>
          <Button
            icon={<LeftOutlined />}
            disabled={!neighbors.previous}
            title={
              neighbors.previous
                ? `上一单：${neighbors.previous.order_number}`
                : '已经是第一单'
            }
            onClick={() => openNeighbor(neighbors.previous)}
          >
            上一单
          </Button>
          <Button
            icon={<RightOutlined />}
            disabled={!neighbors.next}
            title={
              neighbors.next
                ? `下一单：${neighbors.next.order_number}`
                : '已经是最后一单'
            }
            onClick={() => openNeighbor(neighbors.next)}
          >
            下一单
          </Button>
          <Button type="primary" icon={<EditOutlined />} onClick={handleEdit}>
            编辑
          </Button>
          {['shipped', 'legacy_unverified'].includes(orderData.warehouse_status) && <Button
            onClick={() => history.push(`/after-sales?order_id=${orderData.id}`)}>
            发起售后补发
          </Button>}
          <Button icon={<PrinterOutlined />} onClick={handlePrint}>
            打印
          </Button>
        </Space>
      }
    >
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card
            title="物流申报资料"
            className="order-shipping-card"
            extra={
              <Button type="primary" icon={<CopyOutlined />} onClick={handleCopyDeclaration}>
                复制全部资料
              </Button>
            }
          >
            {shippingIncomplete && (
              <Alert
                type="warning"
                showIcon
                message="收件人、电话或地址不完整，请先核对"
                style={{ marginBottom: 16 }}
              />
            )}
            <div className="order-shipping-fields">
              {shippingFields.map(({ label, value }) => (
                <div className="order-shipping-field" key={label}>
                  <span className="order-shipping-field__label">{label}</span>
                  <span className="order-shipping-field__value">{value || '未获取'}</span>
                  {value && (
                    <Typography.Text
                      className="order-shipping-field__copy"
                      copyable={{ text: value, tooltips: [`复制${label}`, '已复制'] }}
                    />
                  )}
                </div>
              ))}
            </div>
          </Card>
          <Card title="物流标签与仓库出库" style={{ marginTop: 16 }}>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap>
                <Typography.Text>仓库状态：</Typography.Text>
                {orderData.warehouse_status === 'shipped' ? <Tag color="green">已扫码出库</Tag>
                  : orderData.warehouse_status?.startsWith('legacy_') ? <Tag color="orange">历史状态待核实</Tag>
                    : <Tag color="blue">待扫码出库</Tag>}
                {orderData.shipped_at && <Typography.Text type="secondary">{formatBeijingTime(orderData.shipped_at)}</Typography.Text>}
              </Space>
              <Space wrap>
                <Typography.Text>包裹：</Typography.Text>
                <Tag color="blue">已登记 {orderData.parcels?.length || 0}/{orderData.expected_parcel_count || 1}</Tag>
                <Tag color={(orderData.parcels || []).filter((parcel) => parcel.scanned_at).length === (orderData.expected_parcel_count || 1) ? 'green' : 'orange'}>
                  已扫码 {(orderData.parcels || []).filter((parcel) => parcel.scanned_at).length}/{orderData.expected_parcel_count || 1}
                </Tag>
              </Space>
              {(orderData.parcels || []).map((parcel, index) => (
                // 信息在左、操作在右：标签长的时候「移除」不会被挤到下一行
                <div className="parcel-row" key={parcel.id}>
                  <div className="parcel-row__main">
                    <Typography.Text>包裹 {index + 1}：</Typography.Text>
                    <Typography.Text type="secondary">4PX 运单</Typography.Text>
                    <Typography.Text copyable>{parcel.label_number}</Typography.Text>
                    <Tag>{parcel.source === 'manual' ? '手动录入' : parcel.source}</Tag>
                    {/*
                      条码和运单号经常是同一串（手动录入、多数 4PX 面单），
                      这种情况不再重复显示一遍，否则这一行会被挤到换行。
                    */}
                    {parcel.label_barcode && parcel.label_barcode !== parcel.label_number
                      ? <Typography.Text copyable>面单条码：{parcel.label_barcode}</Typography.Text>
                      : parcel.label_barcode
                        ? null
                        : parcel.source === '4px' ? <Tag color="orange">待取实体面单</Tag> : null}
                    {parcel.scanned_at
                      ? <Tag color="green">已扫码 {formatBeijingTime(parcel.scanned_at)}</Tag>
                      : <Tag color="orange">待扫码</Tag>}
                  </div>
                  <div className="parcel-row__actions">
                    {parcel.source === '4px' && <Button size="small" loading={fetchingLabelId === parcel.id}
                      onClick={async () => {
                        setFetchingLabelId(parcel.id);
                        try {
                          const result = (await fetchFourPxLabel(orderData.id, parcel.id)).data;
                          setLabelPdfUrls((urls) => ({ ...urls, [parcel.id]: result.pdf_url }));
                          message.success('已获取 PDF 面单；请打开打印，并扫描打印件上的条码出库');
                          await fetchOrderDetail();
                        } catch (error) {
                          message.error(getErrorMessage(error, '获取 4PX 面单失败'));
                        } finally { setFetchingLabelId(null); }
                      }}>获取面单</Button>}
                    {labelPdfUrls[parcel.id] && <a href={labelPdfUrls[parcel.id]} target="_blank" rel="noopener noreferrer">打开 PDF 打印</a>}
                    {access.fulfillment && !parcel.scanned_at && orderData.warehouse_status === 'pending' && <Popconfirm
                      title="移除这张未扫码标签？" onConfirm={() => handleDeleteParcel(parcel.id)}
                    ><Button type="link" danger loading={shippingBusy}>移除</Button></Popconfirm>}
                  </div>
                </div>
              ))}
              <Alert type="info" showIcon message="录入单号不代表出库，仍需扫码" />
              {/*
                所有动作收在一行：先是登记类操作，再是 Shopify 回填，最后是主动作「扫码确认出库」。
                原来回填按钮单独占一行（而且常年禁用），看起来像坏掉的按钮。
              */}
              <Space wrap>
                {orderData.warehouse_status === 'pending' && <>
                  <Button disabled={(orderData.parcels || []).some((parcel) => Boolean(parcel.scanned_at))}
                    onClick={() => { setParcelCountDraft(orderData.expected_parcel_count || 1); setParcelCountOpen(true); }}>
                    设置包裹数
                  </Button>
                  <Button disabled={(orderData.expected_parcel_count || 1) > 1 && (orderData.parcels?.length || 0) >= (orderData.expected_parcel_count || 1)}
                    onClick={() => { setLabelDraft((orderData.expected_parcel_count || 1) === 1 ? (orderData.shipping_label_number || '') : ''); setLabelOpen(true); }}>
                    {(orderData.expected_parcel_count || 1) > 1 ? '新增包裹标签' : orderData.shipping_label_number ? '修改物流标签' : '录入物流标签'}
                  </Button>
                </>}
                <Popconfirm title="确认向 Shopify 回填这些物流单号并标记发货？" description="会向客户发送发货通知邮件"
                  onConfirm={async () => {
                    setShopifyWritebackBusy(true);
                    try {
                      const result = (await writebackShopifyTracking(orderData.id)).data;
                      message.success(result.customer_notification_requested
                        ? 'Shopify 单号已回填，已请求发送客户通知；仓库仍需扫码出库'
                        : 'Shopify 已有相同物流单号，本次未重复发送通知；仓库仍需扫码出库');
                      await fetchOrderDetail();
                    } catch (error) {
                      message.error(getErrorMessage(error, 'Shopify 回填失败，请核对 Shopify 后台后再重试'));
                    } finally { setShopifyWritebackBusy(false); }
                  }}>
                  <Button loading={shopifyWritebackBusy} disabled={
                    orderData.warehouse_status?.startsWith('legacy_') ||
                    (orderData.parcels || []).some((parcel) => parcel.source !== '4px') ||
                    (orderData.parcels?.length || 0) !== (orderData.expected_parcel_count || 1)}>
                    回填 Shopify 单号并标记发货
                  </Button>
                </Popconfirm>
                {orderData.warehouse_status === 'pending' && (
                  <Button type="primary" disabled={(orderData.parcels?.length || 0) !== (orderData.expected_parcel_count || 1) || orderData.procurement_status !== 'ready_to_ship'}
                    onClick={() => { setScanDraft(''); setScanOpen(true); }}>扫码确认出库</Button>
                )}
              </Space>
              {orderData.warehouse_status?.startsWith('legacy_') && <Alert type="warning" showIcon message="历史迁移订单，请核对实物出库记录" />}
            </Space>
          </Card>
          <Card
            title="4PX 真实费用"
            style={{ marginTop: 16 }}
            extra={(
              <Button loading={syncingFreight} onClick={async () => {
                setSyncingFreight(true);
                try {
                  const result = (await syncFourPxFreight([orderData.id])).data;
                  const row = result.results?.[0];
                  if (row?.status === 'synced') {
                    message.success(`已同步：计费重 ${row.charge_weight_kg ?? '-'}kg，总费用 ${row.currency || 'CNY'} ${row.total_fee ?? '-'}`);
                  } else {
                    message.warning(row?.error || '4PX 还没有出账（可能未揽收/称重），请稍后再试');
                  }
                  await fetchOrderDetail();
                } catch (error) {
                  message.error(getErrorMessage(error, '同步 4PX 费用失败'));
                } finally {
                  setSyncingFreight(false);
                }
              }}>同步 4PX 费用</Button>
            )}
          >
            {orderData.fourpx_freight ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Space wrap>
                  <Tag color="blue">4PX 单号 {orderData.fourpx_freight.tracking_number || '—'}</Tag>
                  <Tag color="purple">计费重 {orderData.fourpx_freight.charge_weight_kg ?? '—'} kg</Tag>
                  <Tag color="gold">总费用 {orderData.fourpx_freight.currency || 'CNY'} {orderData.fourpx_freight.total_fee ?? '—'}</Tag>
                </Space>
                <Space wrap>
                  {(orderData.fourpx_freight.subs || []).map((row) => (
                    <Tag key={`${row.fee_name}-${row.fee_amount}`}>{row.fee_name} {row.currency} {row.fee_amount}</Tag>
                  ))}
                </Space>
                <Typography.Text type="secondary">
                  出账时间 {orderData.fourpx_freight.billed_at ? formatBeijingTime(orderData.fourpx_freight.billed_at) : '—'}
                  {' · '}同步时间 {orderData.fourpx_freight.synced_at ? formatBeijingTime(orderData.fourpx_freight.synced_at) : '—'}
                </Typography.Text>
              </Space>
            ) : (
              <Typography.Text type="secondary">尚未同步；4PX 揽收称重、出账后可同步真实计费重与费用。</Typography.Text>
            )}
          </Card>
          {!!purchaseLists.length && (
            <Card title="关联采购清单" style={{ marginTop: 16 }}>
              <Space wrap>
                {purchaseLists.map((purchase) => (
                  <Button
                    key={purchase.id}
                    type="link"
                    onClick={() => history.push(`/purchases?purchase_id=${purchase.id}`)}
                  >
                    {purchase.name}
                  </Button>
                ))}
                <Tag>{purchaseLists.length} 份</Tag>
              </Space>
            </Card>
          )}
          {!!orderData.after_sales?.length && <Card title="售后补发记录" style={{ marginTop: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Typography.Text>累计售后成本：¥{(orderData.after_sale_cost_cny || 0).toFixed(2)}（已计入人民币利润）</Typography.Text>
              {orderData.after_sales.map((record) => <Space key={record.id} wrap>
                <Button type="link" onClick={() => history.push(`/after-sales?after_sale_id=${record.id}`)}>{record.customer_reference}</Button>
                <Tag>{record.kind === 'exchange' ? '换品补发' : record.kind === 'missing' ? '漏发补发' : '原品补发'}</Tag>
                <Tag color={record.status === 'shipped' ? 'blue' : record.status === 'ready' ? 'green' : 'orange'}>{record.status}</Tag>
              </Space>)}
            </Space>
          </Card>}
          <Card
            title="基本信息"
            style={{ marginTop: 16 }}
            extra={
              <Button
                icon={<SyncOutlined />}
                loading={refreshingContact}
                onClick={() => handleRefreshContact(false)}
              >
                从 Shopify 更新详情
              </Button>
            }
          >
            <ProDescriptions
              column={descriptionColumns}
              dataSource={orderData as Record<string, any>}
              columns={[
                {
                  title: '订单号',
                  dataIndex: 'order_number',
                  copyable: true,
                },
                {
                  title: '财务状态',
                  dataIndex: 'financial_status',
                  render: () =>
                    orderData?.actual_received_source === 'refund_pending' ? (
                      <Space size={2}>
                        {getStatusTag(
                          orderData?.financial_status || '',
                          'financial',
                        )}
                        <Tag color="orange">退款处理中</Tag>
                      </Space>
                    ) : (
                      getStatusTag(
                        orderData?.financial_status || '',
                        'financial',
                      )
                    ),
                },
                {
                  title: 'Shopify 发货状态',
                  dataIndex: 'fulfillment_status',
                  render: () =>
                    getStatusTag(
                      orderData?.fulfillment_status || '',
                      'fulfillment',
                    ),
                },
                {
                  title: '采购备货',
                  dataIndex: 'procurement_status',
                  render: () => orderData?.procurement_status === 'ready_to_ship'
                    ? <Tag color="green">可发货</Tag>
                    : orderData?.procurement_status === 'stocked'
                      ? <Tag color="default">已备齐</Tag>
                    : orderData?.procurement_status === 'purchasing'
                      ? <Tag color="blue">已发起采购</Tag>
                      : orderData?.procurement_status === 'shortage'
                        ? <Tag color="orange">已采购 · 待补采 {orderData?.procurement_needed_quantity || 0} 件</Tag>
                        : orderData?.procurement_status === 'partially_stocked'
                          ? <Tag color="blue">部分备货 · 还需 {orderData?.procurement_needed_quantity || 0} 件</Tag>
                        : <Tag>未采购</Tag>,
                },
                {
                  title: '订单金额',
                  dataIndex: 'total_price',
                  render: (_, record) =>
                    `${record.total_price?.toFixed(2) || '0.00'} ${record.currency || ''}`,
                },
                {
                  title: '实际到账',
                  dataIndex: 'settlement_amount',
                  render: (_, record) =>
                    record.settlement_amount != null ? (
                      <Space direction="vertical" size={0}>
                        <strong>
                          {record.settlement_amount.toFixed(2)}{' '}
                          {record.settlement_currency || ''}
                        </strong>
                        <Typography.Text type="secondary">
                          ≈ ¥
                          {Number(record.settlement_amount_cny || 0).toFixed(2)}
                          （{record.exchange_rate_refresh_currencies?.length ? '最近已验证汇率' : '当前汇率'}）
                        </Typography.Text>
                        {record.actual_received_source === 'refund_pending' && (
                          <Tag color="orange">退款处理中，到账为估算</Tag>
                        )}
                      </Space>
                    ) : record.actual_received_source === 'gateway' ? (
                      // PayPal 等渠道在 Shopify 侧没有结算流水，但净额来自网关回执，
                      // 仍然是真实值，不能标成「估算」。
                      <Space size={6}>
                        <strong>
                          {Number(record.actual_received || 0).toFixed(2)}{' '}
                          {record.currency || ''}
                        </strong>
                        <Tag color="green">真实</Tag>
                      </Space>
                    ) : (
                      <Typography.Text type="secondary">
                        {Number(record.actual_received || 0).toFixed(2)}{' '}
                        {record.currency || ''}（估算）
                      </Typography.Text>
                    ),
                },
                {
                  title: '成本费用',
                  dataIndex: 'product_cost',
                  render: (_, record) => {
                    const cost = record.product_cost || 0;
                    return cost > 0 ? `$${cost.toFixed(2)}` : '未添加';
                  },
                },
                {
                  title: '物流费用',
                  dataIndex: 'shipping_cost_cny',
                  render: (_, record) => {
                    const cost = record.shipping_cost_cny ?? record.shipping_cost ?? 0;
                    return cost > 0 ? (
                      <Space size={6}>
                        <span>¥{cost.toFixed(2)}</span>
                        {record.shipping_cost_source === '4px' && <Tag color="blue">4PX</Tag>}
                      </Space>
                    ) : '未添加';
                  },
                },
                {
                  title: '实际利润',
                  key: 'net_profit',
                  render: (_, record) => {
                    return `¥${Number(record.gross_profit_cny || 0).toFixed(2)}`;
                  },
                },
                {
                  title: '创建时间',
                  dataIndex: 'created_at',
                  render: (_, record) =>
                    formatBeijingTime(record.created_at || ''),
                },
                {
                  title: '更新时间',
                  dataIndex: 'updated_at',
                  render: (_, record) =>
                    formatBeijingTime(record.updated_at || ''),
                },
                {
                  title: '备注',
                  dataIndex: 'note',
                  render: (_, record) => record.note || '-',
                },
                {
                  title: 'Shopify 详情更新',
                  dataIndex: 'shopify_details_synced_at',
                  render: (_, record) => record.shopify_details_synced_at
                    ? formatBeijingTime(record.shopify_details_synced_at) : '尚未完整更新',
                },
              ]}
            />
          </Card>

          {/* 商品明细 */}
          <Card title="商品明细">
            <Table
              columns={itemColumns}
              dataSource={orderItems}
              pagination={false}
              summary={() => (
                <Table.Summary>
                  <Table.Summary.Row>
                    <Table.Summary.Cell index={0} colSpan={4}>
                      <strong>总计</strong>
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={4} align="right">
                      <strong>
                        {orderData?.total_price?.toFixed(2) || '0.00'}{' '}
                        {orderData?.currency || ''}
                      </strong>
                    </Table.Summary.Cell>
                  </Table.Summary.Row>
                </Table.Summary>
              )}
            />
          </Card>

          <Card title="物流信息" style={{ marginTop: 16 }}>
            <Table
              rowKey={(record) =>
                String(
                  record.id ||
                    record.tracking_numbers?.join('-') ||
                    record.updated_at ||
                    'fulfillment',
                )
              }
              dataSource={orderData?.fulfillments || []}
              pagination={false}
              size="small"
              locale={{ emptyText: '暂无物流信息' }}
              columns={[
                {
                  title: '状态',
                  dataIndex: 'status',
                  render: (value) => (
                    <Tag
                      color={
                        value === 'SUCCESS' || value === 'success'
                          ? 'green'
                          : 'blue'
                      }
                    >
                      {value || '-'}
                    </Tag>
                  ),
                },
                {
                  title: '物流商',
                  dataIndex: 'tracking_company',
                  render: (value) => value || '-',
                },
                {
                  title: '运单号',
                  dataIndex: 'tracking_numbers',
                  render: (values: string[] = []) =>
                    values.length
                      ? values.map((value) => (
                          <Typography.Text
                            key={value}
                            copyable
                            style={{ display: 'block' }}
                          >
                            {value}
                          </Typography.Text>
                        ))
                      : '-',
                },
                {
                  title: '查询',
                  dataIndex: 'tracking_urls',
                  render: (urls: string[] = []) =>
                    urls.length
                      ? urls.map((url, index) => (
                          <a
                            key={url}
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            style={{ display: 'block' }}
                          >
                            查看物流{urls.length > 1 ? ` ${index + 1}` : ''}
                          </a>
                        ))
                      : '-',
                },
                {
                  title: '更新时间',
                  dataIndex: 'updated_at',
                  render: (value) => (value ? formatBeijingTime(value) : '-'),
                },
              ]}
            />
          </Card>

          {/* 支付与到账 */}
          <Card
            title="支付与到账"
            style={{ marginTop: 16 }}
            extra={
              orderData?.presentment_currency &&
              orderData.presentment_currency !== orderData?.currency ? (
                <Tag color="blue">
                  买家结算币种 {orderData.presentment_currency}
                </Tag>
              ) : null
            }
          >
            <Table
              columns={paymentColumns}
              dataSource={(orderData?.payments || []).map((payment: any) => ({
                ...payment,
                key: String(payment.id ?? payment.transaction_id),
              }))}
              pagination={false}
              size="small"
              locale={{ emptyText: '暂无支付记录' }}
            />
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          {/* 订单状态历史 */}
          <Card title="状态历史">
            <Timeline
              items={statusHistory.map((item) => ({
                color: item.color,
                children: (
                  <div>
                    <div style={{ fontWeight: 'bold', marginBottom: 4 }}>
                      {item.status}
                    </div>
                    <div
                      style={{ color: '#666', fontSize: 12, marginBottom: 4 }}
                    >
                      {item.time}
                    </div>
                    <div style={{ fontSize: 14 }}>{item.description}</div>
                  </div>
                ),
              }))}
            />
          </Card>
        </Col>
      </Row>
      <Modal title={(orderData?.expected_parcel_count || 1) > 1 ? '新增包裹标签' : '录入物流标签单号'} open={labelOpen} onCancel={() => setLabelOpen(false)}
        onOk={handleSaveLabel} okText="保存标签（不出库）" okButtonProps={{ disabled: !labelDraft.trim() }} confirmLoading={shippingBusy}>
        <Typography.Paragraph type="secondary">保存标签不会出库，也不扣库存</Typography.Paragraph>
        <Input value={labelDraft} maxLength={120} onChange={(event) => setLabelDraft(event.target.value)}
          onPressEnter={handleSaveLabel} placeholder="输入物流标签单号" />
      </Modal>
      <Modal title="设置应有包裹数" open={parcelCountOpen} onCancel={() => setParcelCountOpen(false)}
        onOk={handleSetParcelCount} okText="保存" confirmLoading={shippingBusy}>
        <Typography.Paragraph type="secondary">开始扫码后不可修改包裹数</Typography.Paragraph>
        <InputNumber min={1} max={10} precision={0} value={parcelCountDraft}
          onChange={(value) => setParcelCountDraft(value || 1)} aria-label="应有包裹数" />
      </Modal>
      <Modal title="扫码确认实物出库" open={scanOpen} onCancel={() => setScanOpen(false)}
        onOk={handleScanOutbound} okText="确认本包裹扫码" okButtonProps={{ disabled: !scanDraft.trim() }} confirmLoading={shippingBusy}>
        <Alert type="warning" showIcon message="请扫描当前包裹，错扫或重复扫码会报错" style={{ marginBottom: 16 }} />
        <Input autoFocus value={scanDraft} onChange={(event) => setScanDraft(event.target.value)}
          onPressEnter={handleScanOutbound} placeholder="扫描打印面单上的实体条码" />
      </Modal>
    </PageContainer>
  );
};

export default OrderDetail;
