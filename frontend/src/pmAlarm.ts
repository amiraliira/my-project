import type { WorkOrder } from './types';

/** Server flags are authoritative; the fallback supports an older list response. */
export function pmIsOverdue(order: Pick<WorkOrder, 'status' | 'pmOverdue' | 'pmExecutionState' | 'pmWindow' | 'dueDate'>, now = Date.now()) {
 if (!['draft', 'returned'].includes(order.status)) return false;
 if (typeof order.pmOverdue === 'boolean') return order.pmOverdue;
 if (['unperformed', 'unassigned-overdue'].includes(order.pmExecutionState || '')) return true;
 const deadline = Date.parse(order.pmWindow?.endUtc || '');
 if (Number.isFinite(deadline)) return deadline <= now;
 if (!order.dueDate) return false;
 const date = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tehran', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(now));
 const today = ['year', 'month', 'day'].map(key => date.find(part => part.type === key)?.value).join('-');
 return order.dueDate.slice(0, 10) < today;
}
