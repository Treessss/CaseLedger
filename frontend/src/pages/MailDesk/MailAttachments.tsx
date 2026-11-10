import { DownloadOutlined, EyeOutlined, PaperClipOutlined } from '@ant-design/icons';
import { Alert, Button, Image, List, Modal, Space, Spin, Tag, Typography, message } from 'antd';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { downloadMailAttachment, listMailAttachments } from '@/services/mail/api';
import type { MailAttachment } from '@/services/mail/api';
import { getErrorMessage } from '@/utils/error';

export default function MailAttachments({messageId, autoLoad = true}: {messageId: string; autoLoad?: boolean}) {
  const [items, setItems] = useState<MailAttachment[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [preview, setPreview] = useState<{url: string; name: string} | null>(null);
  const alive = useRef(true);
  const pending = useRef(false);
  const urls = useRef(new Set<string>());
  const previewUrl = useRef<string | null>(null);
  const timers = useRef(new Set<number>());
  const release = (url: string) => {URL.revokeObjectURL(url); urls.current.delete(url);};
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await listMailAttachments(messageId);
      if (alive.current) setItems(response.data);
    } catch (failure) {
      if (alive.current) setError(getErrorMessage(failure, '附件加载失败，请重试'));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [messageId]);
  useEffect(() => {
    alive.current = true;
    if (autoLoad) void load();
    return () => {alive.current = false; for (const timer of timers.current) window.clearTimeout(timer); timers.current.clear();
      for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear();};
  }, [autoLoad, load]);
  const open = async (item: MailAttachment, asPreview: boolean) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(item.id);
    try {
      const blob = await downloadMailAttachment(messageId, item.id);
      if (!alive.current) return;
      const url = URL.createObjectURL(blob);
      urls.current.add(url);
      if (asPreview && ['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(blob.type)) {
        if (previewUrl.current) release(previewUrl.current);
        previewUrl.current = url;
        setPreview({url, name: item.name});
      } else {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = item.name;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        const timer = window.setTimeout(() => {release(url); timers.current.delete(timer);}, 5000);
        timers.current.add(timer);
        if (asPreview) message.info('该文件不支持安全图片预览，已改为下载');
      }
    } catch (failure) {
      if (alive.current) message.error(getErrorMessage(failure, '附件读取失败，请重试'));
    } finally {
      pending.current = false;
      if (alive.current) setBusy('');
    }
  };
  return <div style={{marginTop: 12}}>
    <Space wrap>
      <Typography.Text strong><PaperClipOutlined /> 附件／内嵌图片</Typography.Text>
      {!autoLoad && items === null && <Button loading={loading} onClick={() => void load()}>查看本封附件</Button>}
      {loading && <Spin size="small" />}
    </Space>
    {error && <Alert type="warning" showIcon message={error} action={<Button onClick={() => void load()} loading={loading}>重试</Button>} />}
    {items?.length === 0 && <div><Typography.Text type="secondary">此邮件没有附件或内嵌图片</Typography.Text></div>}
    {!!items?.length && <List size="small" dataSource={items} renderItem={(item) => <List.Item>
      <Space wrap style={{width: '100%', justifyContent: 'space-between'}}>
        <div style={{minWidth: 0, maxWidth: '100%', wordBreak: 'break-word'}}><Typography.Text>{item.name}</Typography.Text>
          <div><Typography.Text type="secondary">{item.size ? `${(item.size / 1024).toFixed(1)} KB` : '大小未知'}</Typography.Text>
            {item.inline && <Tag style={{marginLeft: 8}}>正文内嵌</Tag>}</div></div>
        <Space wrap>{item.image && <Button style={{minHeight: 44}} icon={<EyeOutlined />} disabled={!!busy} loading={busy === item.id} onClick={() => void open(item, true)}>预览图片</Button>}
          <Button style={{minHeight: 44}} icon={<DownloadOutlined />} disabled={!!busy} loading={busy === item.id} onClick={() => void open(item, false)}>下载</Button></Space>
      </Space>
    </List.Item>} />}
    <Modal open={!!preview} title={preview?.name} footer={null} onCancel={() => {
      if (previewUrl.current) release(previewUrl.current); previewUrl.current = null; setPreview(null);
    }} width={900} destroyOnHidden>
      {preview && <Image src={preview.url} alt={preview.name} preview={false} width="100%" style={{maxHeight:'70vh',objectFit:'contain'}} />}
    </Modal>
  </div>;
}
