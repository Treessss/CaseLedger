import {
  CustomerServiceOutlined,
  MailOutlined,
  PlusOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  SettingOutlined,
  TranslationOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import ShopRequiredNotice from '@/components/ShopRequiredNotice';
import MailAttachments from './MailAttachments';
import MailSupportReview from './MailSupportReview';
import MailDraftJobs from './MailDraftJobs';
import { ALL_SHOPS, getSelectedShopId } from '@/utils/shop';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Empty,
  Input,
  List,
  message,
  Modal,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  connectMailbox,
  getMail,
  getMailStatus,
  hydrateMailThread,
  ignoreMail,
  listMails,
  saveGuidance,
  saveMailSettings,
  saveReply,
  sendMailReply,
  sendMailReplyWithAttachments,
  syncMails,
  translateMail,
  verifyMails,
  type MailDetail,
  type MailItem,
  type MailStatusInfo,
} from '@/services/mail/api';
import { getErrorMessage } from '@/utils/error';
import { formatBeijingTime } from '@/utils/timezone';

const STATUS_TEXT: Record<string, string> = {
  pending: '尚未回复',
  followup: '客户追加',
  replied: '已回复',
  verify: '待核查',
  uncertain: '发送待核查',
  ignored: '无需回复',
};

const STATUS_COLOR: Record<string, string> = {
  pending: 'orange',
  followup: 'gold',
  replied: 'green',
  verify: 'blue',
  uncertain: 'red',
  ignored: 'default',
};

/** epoch 毫秒 → 北京时间显示 */
const beijingTime = (ms?: number, withDate = true) =>
  ms ? formatBeijingTime(new Date(ms).toISOString(), withDate ? 'YYYY-MM-DD HH:mm' : 'MM-DD HH:mm') : '—';

const MailDesk: React.FC = () => {
  const access = useAccess();
  const [status, setStatus] = useState<MailStatusInfo | null>(null);
  const [items, setItems] = useState<MailItem[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [filter, setFilter] = useState('todo');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(false);

  const [selectedId, setSelectedId] = useState<string>();
  const [detail, setDetail] = useState<MailDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [guidance, setGuidance] = useState('');
  const [reply, setReply] = useState('');
  const [attachments, setAttachments] = useState<UploadFile[]>([]);
  const [translated, setTranslated] = useState<Record<string, string>>({});
  // 往来记录默认折叠成一行，点开看全文（和 Zoho 的会话视图一致）
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState('');

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [dc, setDc] = useState('com');
  const [code, setCode] = useState('');
  const [mailbox, setMailbox] = useState('');
  const [deepseek, setDeepseek] = useState('');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [model, setModel] = useState('deepseek-flash');
  const [consent, setConsent] = useState(false);

  const loadStatus = useCallback(async () => {
    const response = await getMailStatus();
    const data = response.data;
    setStatus(data);
    setDc(data.dc || 'com');
    setMailbox(data.email || '');
    setModel(data.model || 'deepseek-flash');
    setConsent(Boolean(data.consent));
    setClientId(data.client_id || '');
    return data;
  }, []);

  const loadList = useCallback(
    async (nextPage = page) => {
      setListLoading(true);
      try {
        const response = await listMails({
          status: filter,
          search: search.trim() || undefined,
          page: nextPage,
          per_page: 20,
        });
        setItems(response.data.items || []);
        setCounts(response.data.counts || {});
        setTotal(response.data.pagination?.total || 0);
      } catch (error) {
        message.error(getErrorMessage(error, '加载邮件列表失败'));
      } finally {
        setListLoading(false);
      }
    },
    [filter, page, search],
  );

  useEffect(() => {
    void loadStatus().catch((error) =>
      message.error(getErrorMessage(error, '读取邮箱状态失败')),
    );
  }, [loadStatus]);

  useEffect(() => {
    void loadList(page);
  }, [loadList, page]);

  const clearAttachments = () => {
    setAttachments((current) => {
      current.forEach((file) => {
        if (file.thumbUrl) URL.revokeObjectURL(file.thumbUrl);
      });
      return [];
    });
  };

  const openMail = async (id: string) => {
    setSelectedId(id);
    clearAttachments();
    setDetailLoading(true);
    setTranslated({});
    setExpanded({});
    try {
      const response = await getMail(id);
      setDetail(response.data);
      setGuidance(response.data.draft?.guidance || '');
      setReply(response.data.draft?.reply || '');
      // 默认展开当前这封，其余折叠
      setExpanded({ [id]: true });
      // 后台把这次会话里还没取过正文的补上（不挡着先看内容）
      const missing = (response.data.thread || []).filter((item) => !item.hydrated).length;
      if (missing > 0) {
        void hydrateMailThread(id, 5)
          .then((result) => {
            // 用户已经切到别的邮件了就别覆盖
            setDetail((prev) => (prev && prev.mail.id === id ? result.data : prev));
          })
          .catch(() => {
            // 补加载失败不影响已经看到的内容
          });
      }
    } catch (error) {
      setDetail(null);
      message.error(getErrorMessage(error, '打开邮件失败'));
    } finally {
      setDetailLoading(false);
    }
  };

  const runBusy = async (name: string, task: () => Promise<void>) => {
    if (busy) return;
    setBusy(name);
    try {
      await task();
    } catch (error) {
      message.error(getErrorMessage(error, '操作失败'), 6);
    } finally {
      setBusy('');
    }
  };

  const handleSync = () =>
    runBusy('同步邮件', async () => {
      let more = true;
      let guard = 0;
      while (more && guard < 60) {
        const result = await syncMails(guard > 0);
        more = result.data.more;
        guard += 1;
      }
      await loadStatus();
      await loadList(1);
      setPage(1);
      message.success('邮件已同步');
    });

  const handleVerify = () =>
    runBusy('核查往来', async () => {
      let remaining = 1;
      let guard = 0;
      while (remaining > 0 && guard < 40) {
        const result = await verifyMails();
        remaining = result.data.remaining;
        guard += 1;
      }
      await loadList(page);
      message.success('往来核查完成');
    });

  const handleSaveReply = () =>
    runBusy('保存回复', async () => {
      if (!selectedId) return;
      const response = await saveReply(selectedId, reply);
      setDetail(prev => prev?.mail.id === selectedId ? { ...prev, draft: response.data } : prev);
      await loadList(page);
      message.success('回复草稿已保存，下次打开可继续编辑');
    });

  const handleSend = () =>
    runBusy('发送', async () => {
      if (!selectedId) return;
      const files = attachments
        .map((file) => file.originFileObj)
        .filter((file): file is NonNullable<typeof file> => Boolean(file));
      if (files.length) {
        await sendMailReplyWithAttachments(selectedId, reply, files as File[]);
      } else {
        await saveReply(selectedId, reply);
        await sendMailReply(selectedId);
      }
      clearAttachments();
      message.success(files.length ? `回复已发送（含 ${files.length} 个图片/视频附件）` : '回复已发送');
      await openMail(selectedId);
      await loadList(page);
    });

  /**
   * 翻译。
   *
   * ``key`` 是"结果显示在哪一格"（往来记录里按邮件 id 存），``messageId`` 才是
   * "要翻译哪一封"。这两个必须分开传：以前只传了 key，结果点第二封时后端拿到的
   * 还是当前选中那封的 id，翻出来的内容就跟第一封一模一样。
   */
  const handleTranslate = (
    kind: 'mail' | 'reply',
    key: string,
    text?: string,
    messageId?: string,
  ) =>
    runBusy(`翻译-${key}`, async () => {
      const result = await translateMail({
        id: kind === 'mail' ? messageId || selectedId : selectedId,
        kind,
        reply: kind === 'reply' ? text : undefined,
      });
      setTranslated((prev) => ({ ...prev, [key]: result.data.text }));
    });

  const handleSaveSettings = () =>
    runBusy('保存设置', async () => {
      await saveMailSettings({
        client_id: clientId.trim() || undefined,
        client_secret: clientSecret.trim() || undefined,
        deepseek: deepseek.trim() || undefined,
        model,
        consent,
      });
      setDeepseek('');
      setClientSecret('');
      await loadStatus();
      message.success('设置已保存');
    });

  const handleConnect = () =>
    runBusy('连接邮箱', async () => {
      await connectMailbox({
        dc,
        code: code.trim() || undefined,
        email: mailbox.trim() || undefined,
      });
      setCode('');
      await loadStatus();
      message.success('邮箱已连接');
      setSettingsOpen(false);
    });

  const filterOptions = useMemo(
    () => [
      { label: `待处理 ${counts.todo ?? 0}`, value: 'todo' },
      { label: `尚未回复 ${counts.pending ?? 0}`, value: 'pending' },
      { label: `客户追加 ${counts.followup ?? 0}`, value: 'followup' },
      { label: `待核查 ${(counts.verify ?? 0) + (counts.uncertain ?? 0)}`, value: 'verify' },
      { label: `已回复 ${counts.replied ?? 0}`, value: 'replied' },
      { label: '全部', value: 'all' },
    ],
    [counts],
  );

  const canSend = Boolean(reply.trim()) && Boolean(detail) && !detail?.draft?.stale && !['replied', 'ignored', 'uncertain'].includes(detail?.mail.status || '');
  const unloaded = (detail?.thread || []).filter(
    (item) => !item.hydrated && !item.text,
  ).length;

  return (
    <PageContainer
      title="客服邮件"
      subTitle="生成客户语言草稿，人工确认后发送"
      extra={[
        <Button key="sync" icon={<ReloadOutlined />} loading={busy === '同步邮件'} onClick={handleSync}>
          同步邮件
        </Button>,
        <Button key="verify" icon={<SafetyCertificateOutlined />} loading={busy === '核查往来'} onClick={handleVerify}>
          核查往来
        </Button>,
        <Button key="settings" disabled={!access.canAdmin} icon={<SettingOutlined />} onClick={() => setSettingsOpen(true)}>
          连接与设置
        </Button>,
      ]}
    >
      {status && !status.connected && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="还没有连接 Zoho 邮箱"
          action={
            <Button size="small" onClick={() => setSettingsOpen(true)}>
              去连接
            </Button>
          }
        />
      )}
      {status?.connected && !status.consent && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="还没允许把邮件交给 DeepSeek 处理"
        />
      )}

      <div className="mail-desk">
        <Card
          size="small"
          className="mail-desk__list"
          title={
            <Space size={6}>
              <MailOutlined />
              收件箱
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {status?.message_count ? `共 ${status.message_count} 封` : ''}
              </Typography.Text>
            </Space>
          }
        >
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <Input.Search
              allowClear
              placeholder="搜索客户邮箱 / 主题"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onSearch={() => {
                setPage(1);
                void loadList(1);
              }}
            />
            <MailDraftJobs ids={items.map(item => item.id)} enabled={Boolean(status?.consent)} onOpen={id => {void openMail(id);}} />
            {/* 用下拉而不是分段按钮：6 个状态在 360px 的列表栏里会被截成「待处…」 */}
            <Select
              value={filter}
              style={{ width: '100%' }}
              options={filterOptions}
              onChange={(value) => {
                setFilter(String(value));
                setPage(1);
              }}
            />
            <Spin spinning={listLoading}>
              <List
                size="small"
                dataSource={items}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的邮件" /> }}
                renderItem={(item) => (
                  <List.Item
                    className={`mail-row${item.id === selectedId ? ' mail-row--active' : ''}`}
                    onClick={() => void openMail(item.id)}
                  >
                    <div className="mail-row__body">
                      <div className="mail-row__top">
                        <Typography.Text strong ellipsis style={{ maxWidth: 190 }}>
                          {item.customer || item.from || '(未知客户)'}
                        </Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {beijingTime(item.time, false)}
                        </Typography.Text>
                      </div>
                      <Typography.Text ellipsis style={{ fontSize: 13 }}>
                        {item.subject || '(无主题)'}
                      </Typography.Text>
                      <Space size={6} wrap>
                        <Tag color={STATUS_COLOR[item.status]}>{STATUS_TEXT[item.status] || item.status}</Tag>
                        {item.attachment && <Tag>附件</Tag>}
                        {item.has_draft && <Badge status="processing" text="有草稿" />}
                      </Space>
                    </div>
                  </List.Item>
                )}
              />
            </Spin>
            {total > 20 && (
              <Pagination
                size="small"
                align="center"
                current={page}
                pageSize={20}
                total={total}
                showSizeChanger={false}
                onChange={(next) => setPage(next)}
              />
            )}
          </Space>
        </Card>

        <div className="mail-desk__detail">
          {!selectedId && (
            <Card>
              <Empty
                image={<CustomerServiceOutlined style={{ fontSize: 56, color: '#cbd5e1' }} />}
                description="从左边选一封邮件开始处理"
              />
            </Card>
          )}
          {selectedId && detailLoading && (
            <Card>
              <div style={{ padding: 48, textAlign: 'center', color: '#94a3b8' }}>
                <Spin /> 正在打开…
              </div>
            </Card>
          )}
          {selectedId && !detailLoading && detail && (
            <>
              <Card size="small" style={{ marginBottom: 16 }}>
                {!detail.mail.system && (
                  <Popconfirm
                    title={detail.mail.ignored ? '恢复处理这封来信？' : '忽略这封来信？客户后续来信仍会进入待处理。'}
                    onConfirm={() => runBusy('忽略邮件', async () => {
                      await ignoreMail(detail.mail.id, !detail.mail.ignored);
                      await openMail(detail.mail.id);
                      await loadList();
                    })}
                  >
                    <Button loading={busy === '忽略邮件'}>{detail.mail.ignored ? '恢复处理' : '忽略本封邮件'}</Button>
                  </Popconfirm>
                )}
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  <Space size={8} wrap>
                    <Typography.Text strong style={{ fontSize: 16 }}>
                      {detail.mail.subject || '(无主题)'}
                    </Typography.Text>
                    <Tag color={STATUS_COLOR[detail.mail.status]}>
                      {STATUS_TEXT[detail.mail.status] || detail.mail.status}
                    </Tag>
                    {detail.mail.attachment && <Tag>含附件</Tag>}
                  </Space>
                  <Space size={16} wrap>
                    <Typography.Text type="secondary">
                      客户：{detail.mail.customer || detail.mail.from || '—'}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      收到：{beijingTime(detail.mail.time)}
                    </Typography.Text>
                    {detail.mail.evidence && (
                      <Typography.Text type="secondary">{detail.mail.evidence}</Typography.Text>
                    )}
                  </Space>
                </Space>
                <MailAttachments key={detail.mail.id} messageId={detail.mail.id} />
              </Card>

              <Card size="small" title="客户订单与物流" style={{ marginBottom: 16 }}>
                {!detail.orders?.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此邮箱暂未匹配到订单，请核对客户购买时使用的邮箱" />}
                <List dataSource={detail.orders || []} renderItem={(order) => (
                  <List.Item>
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Space wrap>
                        {access.orders ? <Button type="link" onClick={() => history.push(`/orders/detail/${order.id}`)}>{order.order_number}</Button> : <Typography.Text strong>{order.order_number}</Typography.Text>}
                        <span>{order.customer_name}</span>
                        <span>{formatBeijingTime(order.order_date)}</span>
                        <Tag>{order.financial_status}</Tag>
                        <Tag>{order.warehouse_status === 'shipped' ? '已扫码出库' : order.warehouse_status === 'legacy_unverified' ? '历史发货待核实' : '未确认出库'}</Tag>
                        {access.after_sales && <Button size="small" disabled={!order.can_after_sale} onClick={() => history.push(`/after-sales?order_id=${order.id}&mail_id=${encodeURIComponent(detail.mail.id)}`)}>发起售后</Button>}
                      </Space>
                      {order.tracking.map((tracking, index) => (
                        <Space key={`${tracking.number}-${index}`} wrap>
                          <Typography.Text copyable>{tracking.number}</Typography.Text>
                          <span>{tracking.company}</span>
                          {tracking.scanned_at && <span>出库：{formatBeijingTime(tracking.scanned_at)}</span>}
                          {tracking.url && /^https?:\/\//i.test(tracking.url) && <a href={tracking.url} target="_blank" rel="noreferrer">查看物流追踪</a>}
                        </Space>
                      ))}
                      {!order.tracking.length && <Typography.Text type="secondary">暂未登记物流单号</Typography.Text>}
                    </Space>
                  </List.Item>
                )} />
              </Card>

              <Card
                size="small"
                title="往来记录"
                style={{ marginBottom: 16 }}
                extra={
                  unloaded > 0 ? (
                    <Button
                      size="small"
                      loading={busy === '加载往来'}
                      onClick={() =>
                        runBusy('加载往来', async () => {
                          if (!selectedId) return;
                          const result = await hydrateMailThread(selectedId, 6);
                          setDetail(result.data);
                          message.success('已加载本次往来');
                        })
                      }
                    >
                      加载完整往来（还有 {unloaded} 封）
                    </Button>
                  ) : null
                }
              >
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {(detail.thread.length ? detail.thread : [detail.mail]).map((item) => (
                    <div
                      key={item.id}
                      className={`mail-bubble mail-bubble--${item.direction === 'out' ? 'out' : 'in'}`}
                    >
                      {/* 点标题行展开/收起，默认只显示一行预览——一次往来可能十几封，
                          全展开会变成一屏刷不完的长文 */}
                      <div
                        className="mail-bubble__head"
                        onClick={() =>
                          setExpanded((prev) => ({ ...prev, [item.id]: !prev[item.id] }))
                        }
                        style={{ cursor: 'pointer' }}
                      >
                        <Typography.Text strong>
                          {item.direction === 'out' ? '我方回复' : '客户来信'}
                        </Typography.Text>
                        <Space size={8}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {beijingTime(item.time)}
                          </Typography.Text>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {expanded[item.id] ? '收起 ▲' : '展开 ▼'}
                          </Typography.Text>
                        </Space>
                      </div>
                      {expanded[item.id] ? (
                        <Typography.Paragraph
                          style={{ whiteSpace: 'pre-wrap', marginBottom: 8, overflowWrap: 'anywhere' }}
                        >
                          {item.text || (item.id === detail.mail.id ? detail.mail.text : '') || '（正文未加载）'}
                        </Typography.Paragraph>
                      ) : (
                        <Typography.Text
                          type="secondary"
                          ellipsis
                          style={{ display: 'block', fontSize: 13 }}
                        >
                          {(
                            item.text ||
                            (item.id === detail.mail.id ? detail.mail.text : '') ||
                            (item.hydrated ? '（这封没有正文，客户直接回复了上一封）' : '（正文未加载）')
                          ).replace(/\s+/g, ' ')}
                        </Typography.Text>
                      )}
                      {expanded[item.id] && item.id !== detail.mail.id && <MailAttachments key={item.id} messageId={item.id} autoLoad={false} />}
                      {translated[item.id] && (
                        <Alert
                          type="success"
                          message="中文译文"
                          description={
                            <span style={{ whiteSpace: 'pre-wrap' }}>{translated[item.id]}</span>
                          }
                        />
                      )}
                      {expanded[item.id] && (
                        <Button
                          size="small"
                          type="link"
                          icon={<TranslationOutlined />}
                          loading={busy === `翻译-${item.id}`}
                          onClick={() =>
                            handleTranslate('mail', item.id, item.text, item.id)
                          }
                        >
                          中文译文
                        </Button>
                      )}
                    </div>
                  ))}
                </Space>
              </Card>

              <MailSupportReview key={detail.mail.id} detail={detail} guidance={guidance} onDraft={draft => {
                setReply(draft.reply || '');
                setDetail(prev => prev?.mail.id === detail.mail.id ? {...prev, draft} : prev);
              }} />
              <Card
                size="small"
                title="回复草稿（客户语言）"
                extra={
                  <Space>
                    {detail.send?.state === 'sent' && <Tag color="green">已发送</Tag>}
                    {detail.send?.state === 'uncertain' && <Tag color="red">发送待核查</Tag>}
                  </Space>
                }
              >
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <div className="mail-guidance">
                    <Typography.Text strong>回复关键点</Typography.Text>
                    <Input.TextArea
                      rows={3}
                      maxLength={3000}
                      value={guidance}
                      placeholder="例：客户已提供照片、实际收到日期、待核实事项"
                      onChange={(event) => setGuidance(event.target.value)}
                      style={{ marginTop: 8 }}
                    />
                  </div>
                  <Space wrap>
                    <Button
                      loading={busy === '保存关键点'}
                      onClick={() =>
                        runBusy('保存关键点', async () => {
                          if (!selectedId) return;
                          await saveGuidance(selectedId, guidance);
                          message.success('回复关键点已保存');
                        })
                      }
                    >
                      只保存关键点
                    </Button>
                  </Space>
                  <Input.TextArea
                    rows={10}
                    value={reply}
                    placeholder="客户语言的回复草稿会出现在这里；人工核对、保存后才可确认发送。"
                    onChange={(event) => setReply(event.target.value)}
                  />
                  <div className="mail-attachment-upload">
                    <Upload
                      accept="image/*,video/*,.mkv,.avi,.mov,.m4v,.3gp,.3g2,.mpeg,.mpg,.mts,.m2ts,.ts,.flv,.wmv,.asf,.vob,.f4v,.ogv"
                      listType="picture-card"
                      fileList={attachments}
                      maxCount={5}
                      beforeUpload={(file) => {
                        const isImage = file.type.startsWith('image/');
                        const isVideo = file.type.startsWith('video/') ||
                          /\.(mp4|m4v|mov|webm|avi|mkv|3gp|3g2|mpeg|mpg|mts|m2ts|ts|flv|wmv|asf|vob|f4v|ogv)$/i.test(file.name);
                        if (!isImage && !isVideo) {
                          message.error('只能添加图片或视频附件');
                          return Upload.LIST_IGNORE;
                        }
                        if (file.size > 25 * 1024 * 1024) {
                          message.error('单个附件不能超过 25MB，请压缩或改用网盘链接');
                          return Upload.LIST_IGNORE;
                        }
                        const item: UploadFile = {
                          uid: file.uid,
                          name: file.name,
                          status: 'done',
                          originFileObj: file,
                          type: file.type,
                          thumbUrl: isImage ? URL.createObjectURL(file) : undefined,
                        };
                        setAttachments((current) => [...current, item]);
                        return false;
                      }}
                      itemRender={(originNode, file) => {
                        const isVideo = file.type?.startsWith('video/') ||
                          /\.(mp4|m4v|mov|webm|avi|mkv|3gp|3g2|mpeg|mpg|mts|m2ts|ts|flv|wmv|asf|vob|f4v|ogv)$/i.test(file.name);
                        if (!isVideo) return originNode;
                        return <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center',
                          justifyContent: 'center', height: '100%', padding: 4, overflow: 'hidden' }}>
                          <VideoCameraOutlined style={{ fontSize: 26 }} />
                          <div style={{ fontSize: 11, marginTop: 4, maxWidth: '100%', overflow: 'hidden',
                            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</div>
                        </div>;
                      }}
                      onRemove={(file) => {
                        if (file.thumbUrl) URL.revokeObjectURL(file.thumbUrl);
                        setAttachments((current) => current.filter((item) => item.uid !== file.uid));
                      }}
                    >
                      {attachments.length >= 5 ? null : (
                        <div>
                          <PlusOutlined />
                          <div style={{ marginTop: 4 }}>添加图片/视频</div>
                        </div>
                      )}
                    </Upload>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      最多 5 个，单个不超过 25MB
                    </Typography.Text>
                  </div>
                  <Space wrap>
                    <Button
                      loading={busy === '保存回复'}
                      disabled={!reply.trim()}
                      onClick={handleSaveReply}
                    >
                      保存草稿
                    </Button>
                    <Button
                      icon={<TranslationOutlined />}
                      loading={busy === '翻译-reply'}
                      disabled={!reply.trim()}
                      onClick={() => handleTranslate('reply', 'reply', reply)}
                    >
                      译文核对
                    </Button>
                    <Popconfirm
                      title="确认发送这封回复？"
                      description={`收件人：${detail.mail.customer || '—'}；发送后记录到 Zoho。`}
                      okText="发送"
                      cancelText="取消"
                      onConfirm={handleSend}
                    >
                      <Button
                        type="primary"
                        danger
                        icon={<SendOutlined />}
                        loading={busy === '发送'}
                        disabled={!canSend}
                      >
                        发送回复
                      </Button>
                    </Popconfirm>
                    <Tooltip title="只校验这一封，不全量同步">
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        只校验这一封
                      </Typography.Text>
                    </Tooltip>
                  </Space>
                  {translated.reply && (
                    <Alert
                      type="info"
                      message="回复中文核对"
                      description={<span style={{ whiteSpace: 'pre-wrap' }}>{translated.reply}</span>}
                    />
                  )}
                </Space>
              </Card>
            </>
          )}
        </div>
      </div>

      <Modal
        title="连接与设置"
        open={settingsOpen}
        footer={null}
        onCancel={() => setSettingsOpen(false)}
        width={620}
      >
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="Zoho 邮箱"
            description={
              status?.connected
                ? `已连接：${status.email || status.account}`
                : undefined
            }
          />
          <Alert
            type="warning"
            showIcon
            message="授权码需要勾选这些权限"
            description={
              <Typography.Text code copyable style={{ fontSize: 12 }}>
                ZohoMail.accounts.READ,ZohoMail.folders.READ,ZohoMail.messages.READ,ZohoMail.messages.CREATE
              </Typography.Text>
            }
          />
          <Space wrap>
            <Input
              style={{ width: 300 }}
              placeholder="Zoho Client ID"
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
            />
            <Input.Password
              style={{ width: 300 }}
              placeholder={status?.credentials ? 'Client Secret（已配置，留空不改）' : 'Zoho Client Secret'}
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
            />
          </Space>
          <Button
            loading={busy === '保存设置'}
            onClick={() =>
              runBusy('保存设置', async () => {
                await saveMailSettings({
                  client_id: clientId.trim() || undefined,
                  client_secret: clientSecret.trim() || undefined,
                });
                setClientSecret('');
                await loadStatus();
                message.success('Client 凭据已保存');
              })
            }
          >
            保存 Client 凭据
          </Button>
          <Space wrap>
            <Select
              value={dc}
              style={{ width: 150 }}
              onChange={setDc}
              options={[
                { label: 'zoho.com', value: 'com' },
                { label: 'zoho.eu', value: 'eu' },
                { label: 'zoho.in', value: 'in' },
                { label: 'zoho.com.au', value: 'comau' },
                { label: 'zoho.jp', value: 'jp' },
              ]}
            />
            <Input
              style={{ width: 260 }}
              placeholder="客服邮箱 address"
              value={mailbox}
              onChange={(event) => setMailbox(event.target.value)}
            />
          </Space>
          <Input.TextArea
            rows={2}
            placeholder="Zoho 授权码（Self Client 生成的 grant code）"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <Button type="primary" loading={busy === '连接邮箱'} onClick={handleConnect}>
            连接 / 重新连接邮箱
          </Button>

          <Alert
            type="info"
            showIcon
            message="DeepSeek（智能分类、客户语言草稿与中文译文）"
            description="Key 加密保存在本地数据库"
          />
          <Space wrap>
            <Input.Password
              style={{ width: 320 }}
              placeholder={status?.deepseek ? '已配置，留空表示不修改' : 'DeepSeek API Key'}
              value={deepseek}
              onChange={(event) => setDeepseek(event.target.value)}
            />
            <Select
              value={model}
              style={{ width: 190 }}
              onChange={setModel}
              options={[
                { label: 'deepseek-flash', value: 'deepseek-flash' },
                { label: 'deepseek-v4-pro', value: 'deepseek-v4-pro' },
              ]}
            />
          </Space>
          <Checkbox checked={consent} onChange={(event) => setConsent(event.target.checked)}>
            允许将邮件与订单信息交给 DeepSeek 分析（不自动发送）
          </Checkbox>
          <Button type="primary" loading={busy === '保存设置'} onClick={handleSaveSettings}>
            保存设置
          </Button>
        </Space>
      </Modal>
    </PageContainer>
  );
};

const ScopedMailDesk: React.FC = () => getSelectedShopId() === ALL_SHOPS
  ? <PageContainer title="客服邮件"><ShopRequiredNotice reason="每个店铺独立绑定邮箱，请先选定店铺查看和处理邮件。" /></PageContainer>
  : <MailDesk />;

export default ScopedMailDesk;
