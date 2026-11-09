import type { Role } from './types';

export const isChief = (role: string) => role === 'head';
export const mayViewGrades = (role: Role) => role === 'head' || role === 'supervisor';
export const mayViewPersonnelReports = (role: Role) => role === 'head';
export const mayViewPmArchive = (role: Role) => role === 'planner';
export const mayProposePm = (role: string) => isChief(role) || ['planner', 'supervisor', 'manager'].includes(role);
