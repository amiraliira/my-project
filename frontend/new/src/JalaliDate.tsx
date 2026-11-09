import {num,todayIso} from './ui';
const months = ['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'];
function jalaliParts(iso: string) {
 const parts = new Intl.DateTimeFormat('en-US-u-ca-persian', { timeZone: 'UTC', year: 'numeric', month: 'numeric', day: 'numeric' }).formatToParts(new Date(iso + 'T12:00:00Z'));
 return ['year', 'month', 'day'].map(key => Number(parts.find(part => part.type === key)?.value));
}
function jalaliIso(year: number, month: number, day: number) {
 let jy = year, gy = jy > 979 ? 1600 : 621;
 jy -= jy > 979 ? 979 : 0;
 let days = 365 * jy + Math.floor(jy / 33) * 8 + Math.floor(((jy % 33) + 3) / 4) + 78 + day + (month < 7 ? (month - 1) * 31 : (month - 7) * 30 + 186);
 gy += 400 * Math.floor(days / 146097); days %= 146097;
 if (days > 36524) { days--; gy += 100 * Math.floor(days / 36524); days %= 36524; if (days >= 365) days++; }
 gy += 4 * Math.floor(days / 1461); days %= 1461;
 if (days > 365) { gy += Math.floor((days - 1) / 365); days = (days - 1) % 365; }
 let gd = days + 1, gm = 1;
 const lengths = [31, gy % 4 === 0 && gy % 100 !== 0 || gy % 400 === 0 ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
 while (gm < 12 && gd > lengths[gm - 1]) { gd -= lengths[gm - 1]; gm++; }
 const iso = `${gy}-${String(gm).padStart(2, '0')}-${String(gd).padStart(2, '0')}`;
 const [actualYear, actualMonth, actualDay] = jalaliParts(iso);
 return actualYear === year && actualMonth === month && actualDay === day ? iso : '';
}
export function JalaliDate({ value, onChange, disabled = false }: { value: string; onChange: (iso: string) => void; disabled?: boolean }) {
 const [year, month, day] = jalaliParts(value);
 const currentYear = jalaliParts(todayIso())[0];
 const yearOptions = [...new Set([year, ...Array.from({ length: 11 }, (_, index) => currentYear - 5 + index)])].sort((a, b) => a - b);
 const dayCount = month <= 6 ? 31 : month <= 11 ? 30 : jalaliIso(year, 12, 30) ? 30 : 29;
 function change(nextYear: number, nextMonth: number, nextDay: number) { const max = nextMonth <= 6 ? 31 : nextMonth <= 11 ? 30 : jalaliIso(nextYear, 12, 30) ? 30 : 29; const next = jalaliIso(nextYear, nextMonth, Math.min(nextDay, max)); if (next) onChange(next); }
 return <div className="registration-date"><select disabled={disabled} aria-label="روز" value={day} onChange={e => change(year, month, Number(e.target.value))}>{Array.from({ length: dayCount }, (_, i) => <option key={i + 1} value={i + 1}>{num(i + 1, 0)}</option>)}</select><select disabled={disabled} aria-label="ماه" value={month} onChange={e => change(year, Number(e.target.value), day)}>{months.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}</select><select disabled={disabled} aria-label="سال" value={year} onChange={e => change(Number(e.target.value), month, day)}>{yearOptions.map(y => <option key={y} value={y}>{num(y, 0).replace(/٬/g, '')}</option>)}</select></div>;
}

