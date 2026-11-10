import React, { useRef, useState } from 'react';
import { Alert, Checkbox, Form, Input, Modal } from 'antd';
import { bindAfterSaleTracking } from '@/services/afterSales';
import type { AfterSale } from '@/services/afterSales';
import { getErrorMessage } from '@/utils/error';

type Props = {record: AfterSale; onClose: () => void; onSaved: (record: AfterSale) => void};

const TrackingModal: React.FC<Props> = ({record, onClose, onSaved}) => {
  const [form] = Form.useForm<{tracking: string; barcode: string}>();
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [error, setError] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const tracking = Form.useWatch('tracking', form) ?? record.tracking_number ?? '';
  const barcode = Form.useWatch('barcode', form) ?? record.label_barcode ?? '';
  const changedTracking = Boolean(record.tracking_number && tracking.trim().toUpperCase() !== record.tracking_number.toUpperCase());
  const replacing = changedTracking || Boolean(record.label_barcode && barcode.trim() && barcode.trim().toUpperCase() !== record.label_barcode.toUpperCase());
  const save = async () => {
    if (submitting.current) return;
    submitting.current = true;
    let values: {tracking: string; barcode: string};
    try { values = await form.validateFields(); } catch { submitting.current = false; return; }
    setError('');
    if (replacing && !confirmed) { submitting.current = false; setError('请核对现有绑定，并勾选确认替换'); return; }
    setBusy(true);
    try {
      const response = await bindAfterSaleTracking(record.id, {
        tracking_number: values.tracking.trim(), label_barcode: values.barcode?.trim() || undefined,
        expected_tracking_number: record.tracking_number || null,
        expected_label_barcode: record.label_barcode || null, confirm_replace: confirmed,
      });
      onSaved(response.data);
    } catch (err) { setError(getErrorMessage(err, '手动绑定物流失败')); }
    finally { submitting.current = false; setBusy(false); }
  };
  return <Modal open title={`手动绑定物流 · ${record.customer_reference}`} onCancel={() => {if (!submitting.current) onClose();}}
    onOk={() => void save()} okText="保存绑定" cancelText="取消" confirmLoading={busy}
    closable={!busy} maskClosable={!busy} keyboard={!busy} cancelButtonProps={{disabled: busy}}>
    <Alert type="info" showIcon message="仅绑定到这张售后单"
      description="不改原单物流、不通知客户、不出库。" style={{marginBottom: 16}} />
    <Form form={form} layout="vertical" initialValues={{tracking: record.tracking_number || '', barcode: record.label_barcode || ''}}
      onFinish={() => void save()}
      onValuesChange={(changed, values) => {
        setConfirmed(false); setError('');
        if ('tracking' in changed && record.tracking_number &&
            values.tracking.trim().toUpperCase() !== record.tracking_number.toUpperCase() &&
            record.label_barcode && values.barcode?.trim().toUpperCase() === record.label_barcode.toUpperCase()) {
          form.setFieldValue('barcode', '');
        }
      }}>
      <Form.Item label="补发物流单号" name="tracking" rules={[{required: true, whitespace: true, message: '请输入补发物流单号'},
        {pattern: /^\s*[A-Za-z0-9-]{1,120}\s*$/, message: '只能填写字母、数字和短横线'}]}>
        <Input autoComplete="off" maxLength={120} placeholder="例如 4PX…CN、OLS… 或其他运单号" disabled={busy} />
      </Form.Item>
      <Form.Item label="实体面单条码（选填）" name="barcode" extra="面单另有条码时填写；单号可留空。"
        rules={[{pattern: /^\s*[A-Za-z0-9-]{1,120}\s*$|^\s*$/, message: '只能填写字母、数字和短横线'}]}>
        <Input autoComplete="off" maxLength={120} placeholder="例如 GV26CAA…；与物流单号相同可不填" disabled={busy} />
      </Form.Item>
    </Form>
    {replacing && <Alert type="warning" showIcon message="正在替换现有绑定"
      description={<><p>原物流单号：{record.tracking_number || '未绑定'}<br />原面单条码：{record.label_barcode || '未填写'}<br />更换运单会清空原条码，请核对新面单后填写。</p>
        <Checkbox checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)}>
          我已核对面单，确认替换现有绑定
        </Checkbox></>} style={{marginBottom: 12}} />}
    {error && <Alert type="error" showIcon message={error} />}
  </Modal>;
};

export default TrackingModal;
