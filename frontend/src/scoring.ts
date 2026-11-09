import type { Participant, WorkOrder } from './types';

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

export function participantGradePayload(order: WorkOrder, values: Record<string, string>, stage: 'supervisor' | 'head') {
 const people = stage === 'head' ? order.participants : order.participants.filter(person => !!order.decisionSpecialty && person.specialty === order.decisionSpecialty && !isHeadOnly(person));
 if (!people.length || !people.every(person => validCoefficient(values[person.personId] ?? '', stage === 'head' ? headGradeMaximum(person) : 3))) return null;
 // Keep decimal text intact; converting through a JavaScript Number would round long fractions.
 const scores = people.map(person => ({ personId: person.personId, coefficient: values[person.personId].trim() }));
 return stage === 'head' ? { participantScores: scores } : { grades: scores };
}

export const validCoefficient = (value: string, maximum = 3) => value.trim() !== '' && Number.isFinite(Number(value)) && Number(value) >= 0 && Number(value) <= maximum;
