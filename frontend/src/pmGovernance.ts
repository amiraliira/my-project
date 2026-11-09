import { useRef } from 'react';
import { requestId } from './ui';

export interface PmChange {
 id: string; operation: string; entityId: string | null; baseVersion?: number | null;
 status: 'pending' | 'approved' | 'rejected'; version: number; requesterId: string;
 requesterName: string; createdAt: string; payload: Record<string, unknown>; reason?: string;
 before?: Record<string, unknown> | null;
 decisionReason?: string; decidedByName?: string; decidedAt?: string;
 result?: { id?: string; title?: string } | null;
}
export interface PendingPmChange { pendingApproval: true; changeRequest: PmChange; message: string; }
export type PmMutation<T> = T | PendingPmChange;
export const isPendingPmChange = <T,>(result: PmMutation<T>): result is PendingPmChange => !!result && typeof result === 'object' && 'pendingApproval' in result && result.pendingApproval === true;

/** A retry reuses its key; edited content receives a new key. */
export function usePmRequestKey() {
 const last = useRef({ serialized: '', key: '' });
 return (payload: Record<string, unknown>) => {
  const serialized = JSON.stringify(payload);
  if (last.current.serialized !== serialized) last.current = { serialized, key: requestId() };
  return { ...payload, requestKey: last.current.key };
 };
}

export const pmChangeNames: Record<string, string> = {
 'create-template': 'افزودن برگه', 'edit-template': 'اصلاح فعالیت‌ها',
 'design-template': 'ویرایش برگه', 'retire-template': 'حذف برگه از برنامه',
 'restore-template': 'بازگرداندن برگه', 'edit-plan': 'اصلاح زمان‌بندی',
};
export const pmChangeStatuses: Record<string, string> = { pending: 'در انتظار تأیید رئیس', approved: 'تأییدشده', rejected: 'ردشده' };
