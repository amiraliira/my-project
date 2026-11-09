export const clientVersion = '2.0.0-rc.12';
export const requiredCapabilities = ['em-downtime-drilldown','workflow-inbox', 'participant-grading', 'technician-registration', 'performance-reports', 'scalable-lists', 'paged-catalogs','versioned-pm-sheets','persian-weekly-pm','equipment-pm-calendar','pm-form-library','pm-template-designer','pm-activation-setup','pm-technician-queue','approved-parts-consumption','internal-network-only','individual-head-grading','chief-personnel-privacy','pm-change-approval','postgresql17-native-backups','pm-submission-archive','daily-line-oee-count','oee-validated-counts-v2','supervisor-self-work-head-only','pm-activity-structure','pm-working-pdf','parts-xlsx-import','pm-responsibility-transfer','own-performance','supervisor-review-correction','supervisor-correction-approve'] as const;
export interface BackendHealth { status?: string; version?: string; apiVersion?: number; schemaVersion?: number; databaseProvider?: string; databaseVersionNum?: number; databaseVersion?: string; capabilities?: string[]; productionAccepted?: boolean; releaseStage?: string; localTestMode?: boolean; localTestInitialized?: boolean; }

export function compatibleBackend(health: BackendHealth | null | undefined) {
 return !!health && health.status === 'ok' && health.apiVersion === 7 && health.databaseProvider === 'postgresql' && typeof health.databaseVersionNum === 'number' && Number.isInteger(health.databaseVersionNum) && health.databaseVersionNum >= 170000 && typeof health.schemaVersion === 'number' && Number.isInteger(health.schemaVersion) && health.schemaVersion >= 10 && Array.isArray(health.capabilities) && requiredCapabilities.every(capability => health.capabilities!.includes(capability));
}

export function candidateRelease(health: BackendHealth | null | undefined) {
 return !!health && (health.productionAccepted === false || health.releaseStage === 'candidate' && health.productionAccepted !== true);
}

export async function startWithCompatibleBackend(fetchHealth: () => Promise<BackendHealth>, whenCompatible: (health: BackendHealth) => Promise<void>) {
 const health = await fetchHealth();
 const compatible = compatibleBackend(health);
 if (compatible) await whenCompatible(health);
 return { health, compatible };
}
