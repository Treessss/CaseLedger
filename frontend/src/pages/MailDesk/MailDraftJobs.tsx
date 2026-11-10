import React, {useEffect, useState} from 'react';
import {Alert, Button, Collapse, message, Popconfirm, Progress, Space, Typography} from 'antd';
import {cancelDraftJob, getDraftJob, startDraftJob, type MailDraftJob} from '@/services/mail/api';
import {getErrorMessage} from '@/utils/error';
import {getSelectedShopId} from '@/utils/shop';

export default function MailDraftJobs({ids, enabled, onOpen}: {
  ids: string[]; enabled: boolean; onOpen: (id: string) => void;
}) {
  const [job, setJob] = useState<MailDraftJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const shop = getSelectedShopId();
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    setJob(null);
    const poll = async () => {
      try {
        const result = await getDraftJob();
        if (!disposed && getSelectedShopId() === shop) setJob(result.data);
      } catch { /* A temporary polling outage must not erase durable progress. */ }
      if (!disposed) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {disposed = true; clearTimeout(timer);};
  }, [shop]);
  const active = job && ['queued', 'running'].includes(job.status);
  const submit = async (data: Parameters<typeof startDraftJob>[0]) => {
    setSubmitting(true);
    try {
      const result = await startDraftJob(data);
      if (getSelectedShopId() !== shop) return;
      setJob(result.data);
      message.success('已提交后台任务，可以离开页面；仅生成草稿，不发送');
    } catch (error) {message.error(getErrorMessage(error, '提交后台任务失败'));}
    finally {setSubmitting(false);}
  };
  const stop = async () => {
    if (!job) return;
    try {const result = await cancelDraftJob(job.id); setJob(result.data);}
    catch (error) {message.error(getErrorMessage(error, '停止任务失败'));}
  };
  return <Space direction="vertical" style={{width: '100%'}}>
    <Space wrap>
      <Popconfirm title="后台生成本页缺失草稿？" description="不覆盖已有草稿、不创建优惠、不发送。" onConfirm={() => {void submit({message_ids: ids});}}>
        <Button disabled={!enabled || Boolean(active) || submitting}>生成本页缺失草稿</Button>
      </Popconfirm>
      <Popconfirm title="后台生成全部缺失草稿？" description="离开或刷新页面仍继续；会消耗 AI 额度。" onConfirm={() => {void submit({all: true});}}>
        <Button disabled={!enabled || Boolean(active) || submitting}>生成全部缺失草稿</Button>
      </Popconfirm>
    </Space>
    {job && <Alert type={job.status === 'failed' ? 'error' : 'info'} message={
      {queued: '后台排队中', running: '后台生成中', completed: '后台任务已完成', cancelled: '已停止后续生成', failed: '后台任务暂停，请查看原因'}[job.status] || job.status
    } description={<Space direction="vertical" style={{width: '100%'}}>
      <Progress percent={Math.round(job.done / Math.max(job.total, 1) * 100)} status={job.status === 'failed' ? 'exception' : active ? 'active' : 'normal'}/>
      <Typography.Text>已处理 {job.done}/{job.total}：成功 {job.succeeded}，失败 {job.failed}，跳过 {job.skipped}。未发送邮件。</Typography.Text>
      {job.error && <Typography.Text type="danger">{job.error}</Typography.Text>}
      {active && <Button onClick={() => {void stop();}}>停止后续生成</Button>}
      {!active && (job.failed > 0 || job.status === 'failed') && <Button disabled={submitting} onClick={() => {void submit({retry_job_id: job.id});}}>只重试失败和未完成项</Button>}
      {job.status === 'cancelled' && <Typography.Text type="secondary">正在生成的一封会完成；其余停止</Typography.Text>}
      {job.failures.length > 0 && <Collapse items={[{key: 'errors', label: `查看 ${job.failures.length} 封失败原因`, children: <Space direction="vertical">
        {job.failures.map(item => <div key={item.message_id}><Button type="link" onClick={() => onOpen(item.message_id)}>打开邮件</Button><Typography.Text>{item.error}</Typography.Text></div>)}
      </Space>}]} />}
    </Space>}/>}
  </Space>;
}
