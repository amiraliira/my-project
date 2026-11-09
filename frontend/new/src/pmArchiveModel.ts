import type { PmTemplate } from './PmLibrary';
import type { OrderPart, PageResult } from './types';

export interface PmArchiveEntry {
 id: string; orderId: string; number: string; title: string; status: string; currentStatus: string; workflowRevision: number;
 templateId: string; templateVersion: number; planId: string; planVersion: number; hall: string;
 assetId: string; assetCode: string; assetName: string; performerId: string; performerName: string; specialty: string;
 workDate: string; startTime: string; endTime: string; durationMinutes: number; executedAt: string | null;
 submittedAt: string; submittedByName: string; approvedAt: string | null; lateExecution: boolean; evidenceSource: string;
}
export interface PmArchiveApproval { id: string; stage: string; specialty?: string; decision: string; reason?: string; actorId: string; actorName: string; createdAt: string; }
export interface PmArchiveDetail {
 entry: PmArchiveEntry; sheet: PmTemplate | null; results: Record<string, string>;
 correction?: { baseArchiveId: string; baseRevision: number; reason: string };
 responsibility?: { originalSupervisorId: string; originalSupervisorName: string; effectiveSupervisorId: string; effectiveSupervisorName: string; transferIds: string[] };
 execution: { description: string; workDate: string; startTime: string; endTime: string; durationMinutes: number;
  participants: { personId: string; personName: string; specialty: string; minutes: number }[]; parts: OrderPart[] };
 identityCapturedAt?: string; revisionsTotal?: number; revisionsHasMore?: boolean;
 approvals: PmArchiveApproval[];
 revisions: { id: string; workflowRevision: number; submittedAt: string; status: string; evidenceSource?: string }[];
}
export type PmArchivePage = PageResult<PmArchiveEntry>;
export interface PmArchiveFilters { q: string; hall: string; assetId: string; personId: string; status: string; from: string; to: string; }
export const emptyArchiveFilters = (): PmArchiveFilters => ({ q: '', hall: '', assetId: '', personId: '', status: '', from: '', to: '' });
export const archiveStatuses: Readonly<Record<string, string>> = {
 'awaiting-approval': 'در انتظار تأیید', supervisor: 'در انتظار تأیید سرپرست', head: 'در انتظار تأیید رئیس',
 completed: 'تأیید نهایی', returned: 'برگشت برای اصلاح', cancelled: 'لغوشده', superseded: 'نسخهٔ قبلی برگه',
};
export function archiveStatus(status: string) { return archiveStatuses[status] || (['submitted', 'planner'].includes(status) ? 'در انتظار تأیید' : 'وضعیت نیازمند بررسی'); }
export function archivePath(filters: PmArchiveFilters, page: number) {
 const params = new URLSearchParams({ page: String(Math.max(1, Math.trunc(page) || 1)), pageSize: '25' });
 for (const [key, value] of Object.entries(filters)) if (value.trim()) params.set(key, value.trim());
 return '/pm/archive?' + params.toString();
}
