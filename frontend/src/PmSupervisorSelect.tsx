import { useState } from 'react';
import { CatalogSelect } from './Catalog';
import type { User } from './types';
import { Field } from './ui';

/** The execution owner is independent of the PM form's technical classification. */
export const pmSupervisorFilters = { role: 'supervisor', active: 'true' } as const;
export const pmSupervisorLabel = (person: Pick<User, 'name' | 'code' | 'specialty'>) =>
 [person.name, person.specialty, person.code].filter(Boolean).join(' · ');

export default function PmSupervisorSelect({ value, onChange, onSelected, fallbackLabel = '', disabled = false, label = 'سرپرست مسئول انجام *' }: {
 value: string; onChange: (id: string) => void; onSelected?: (person: User) => void;
 fallbackLabel?: string; disabled?: boolean; label?: string;
}) {
 const [selected, setSelected] = useState<User | null>(null);
 const person = selected?.id === value ? selected : null;
 return <Field label={label} hint="مسئول انجام را از سرپرستان فعال انتخاب کنید؛ تخصص برگه تغییر نمی‌کند.">
  <CatalogSelect kind="people" value={value} fallbackLabel={fallbackLabel} filters={pmSupervisorFilters}
   placeholder="انتخاب سرپرست مسئول انجام" disabled={disabled}
   onChange={id => { if (!id) setSelected(null); onChange(id); }}
   onSelected={item => { if ('role' in item && item.role === 'supervisor') { setSelected(item); onSelected?.(item); } }}/>
  {person && <span className="pm-section-note">واحد انجام‌دهنده: {person.specialty || 'تعیین نشده'} · کد {person.code}</span>}
 </Field>;
}
