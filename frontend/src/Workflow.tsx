import type { WorkOrder } from './types';
import { Icon, statuses } from './ui';
import { isHeadOnly } from './scoring';

export function WorkflowProgress({ order }: { order: WorkOrder }) {
 const direct = order.participants.length > 0 && order.participants.every(isHeadOnly);
 const stages = [{ id: 'draft', title: 'گزارش انجام کار' }, { id: 'supervisor', title: 'نمره و تأیید سرپرستان' }, { id: 'head', title: 'نمره و تأیید رئیس' }, { id: 'completed', title: 'بایگانی نهایی' }].filter(stage => !direct || stage.id !== 'supervisor');
 const current = stages.findIndex(stage => stage.id === order.status);
 const pending = order.pendingSupervisorSpecialties || [];
 const returnReason = order.approvals?.filter(approval => approval.decision === 'return').at(-1)?.reason;
 return <section className="workflow-summary" aria-label="مراحل گردش تأیید"><div className="workflow-stages">{stages.map((stage, index) => <div key={stage.id} className={'workflow-stage ' + (order.status === 'completed' || current > index ? 'done' : current === index ? 'current' : '')}><span>{order.status === 'completed' || current > index ? <Icon name="check" size={14}/> : index + 1}</span><b>{stage.title}</b></div>)}</div>{order.status === 'supervisor' && pending.length > 0 && <p>تأیید تخصص‌های باقی‌مانده: <b>{pending.join('، ')}</b></p>}{order.status === 'returned' && <p className="workflow-returned">برگشت برای اصلاح{ returnReason ? ': ' + returnReason : ''}. پس از ارسال مجدد، تأییدها برای نسخه جدید انجام می‌شوند.</p>}{order.status === 'cancelled' && <p className="workflow-returned">این کار لغو شده است.</p>}{order.canDecide && <p className="workflow-your-turn"><Icon name="check" size={15}/>نوبت تصمیم شما: {order.decisionSpecialty || statuses[order.decisionStage || ''] || 'تأیید نهایی'}</p>}</section>;
}
