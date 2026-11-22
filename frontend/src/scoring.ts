import type { Participant, WorkOrder } from './types';
import { time24Digits } from './clock24';

export const isHeadOnly = (person: Participant) => person.gradingPolicy === 'head-only-v1';
export const headGradeMaximum = (person: Participant) => isHeadOnly(person) ? 9 : 3;

export function supervisorGrade(order: WorkOrder, personId: string) {
 const score = order.participantScores?.find(entry => entry.personId === personId);
 if (score) return score;
 const participant = order.participants.find(person => person.personId === personId);
 if (participant?.supervisorCoefficient != null && participant.supervisorScoreSource) return { personId, specialty: participant.specialty || '', coefficient: participant.supervisorCoefficient, actorName: '', createdAt: '', source: participant.supervisorScoreSource };
 // The server explicitly marks historical specialty grades. New records never inherit a shared grade.
 return null;
}

export function participantWorkScore(order: WorkOrder, personId: string) {
 if (order.status !== 'completed') return 0;
 const participant = order.participants.find(person => person.personId === personId);
 if (participant && isHeadOnly(participant)) return participant.finalScore ?? 0;
 const grade = supervisorGrade(order, personId);
 // Individual scores are rounded per person by the server; sum the canonical values.
 if (participant && grade?.source === 'individual') return participant.finalScore ?? 0;
 const head = headGrade(order, personId);
 return participant && grade && head != null ? participant.minutes / 60 * grade.coefficient * head.coefficient : 0;
}

export function headGrade(order: WorkOrder, personId: string) {
 const score = order.participantHeadScores?.find(entry => entry.personId === personId);
 if (score) return score;
 const participant = order.participants.find(person => person.personId === personId);
 if (participant?.headCoefficient != null && participant.headScoreSource) return { personId, specialty: participant.specialty || '', coefficient: participant.headCoefficient, actorName: '', createdAt: '', source: participant.headScoreSource };
 // Only historical orders carry an order-wide head grade. New approvals send every participant.
 const legacy = order.headCoefficient ?? order.approvals?.find(approval => approval.stage === 'head' && approval.decision === 'approve')?.coefficient;
 return legacy == null ? null : { personId, specialty: participant?.specialty || '', coefficient: legacy, actorName: '', createdAt: '', source: 'legacy-order' as const };
}

export function approvalParticipants(order: WorkOrder, stage: 'supervisor' | 'head') {
 return stage === 'head' ? order.participants : order.participants.filter(person =>
  !!order.decisionSpecialty && person.specialty === order.decisionSpecialty && !isHeadOnly(person));
}

export function participantGradePayload(order: WorkOrder, values: Record<string, string>, stage: 'supervisor' | 'head', actorId?: string) {
 const people = approvalParticipants(order, stage);
 if (!people.length || actorId && people.some(person => person.personId === actorId)) return null;
 const scores: {personId: string; coefficient: string}[] = [];
 for (const person of people) {
  const coefficient = stage === 'head'
   ? normalizeCoefficient(values[person.personId] ?? '', headGradeMaximum(person), 28)
   : normalizeCoefficient(values[person.personId] ?? '');
  if (coefficient === null) return null;
  scores.push({ personId: person.personId, coefficient });
 }
 return stage === 'head' ? { participantScores: scores } : { grades: scores };
}

export function normalizeCoefficient(value: string, maximum = 3, fractionalLimit = 64): string | null {
 const text = time24Digits(value).trim().replace(/[٫،,]/g, '.');
 if (text.length > 64 || !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) return null;
 const [whole, fractional = ''] = text.split('.');
 const digits = fractional.replace(/0+$/, '');
 // Compare the fractional tail exactly at the upper bound without floating-point rounding.
 const integer = Number(whole);
 if (integer > maximum || (integer === maximum && digits.length > 0) || digits.length > fractionalLimit) return null;
 const canonicalWhole = whole.replace(/^0+/, '') || '0';
 return digits ? canonicalWhole + '.' + digits : canonicalWhole;
}

