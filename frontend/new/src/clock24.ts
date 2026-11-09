/** Input helpers are deliberately independent of the browser and its locale. */
export function time24Digits(value: string) {
 return value.replace(/[۰-۹]/g, digit => String(digit.charCodeAt(0) - 1776)).replace(/[٠-٩]/g, digit => String(digit.charCodeAt(0) - 1632));
}

export function normalizeTime24(value: string): string | null {
 const text = time24Digits(value).trim().replace(/：/g, ':');
 const match = /^(\d{1,2}):(\d{1,2})$/.exec(text) || /^(\d{1,2})(\d{2})$/.exec(text);
 if (!match) return null;
 const hour = Number(match[1]), minute = Number(match[2]);
 return hour < 24 && minute < 60 ? `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}` : null;
}

export function clockNumber(value: number, pad = false) {
 return (pad ? String(value).padStart(2, '0') : String(value)).replace(/\d/g, digit => '۰۱۲۳۴۵۶۷۸۹'[Number(digit)]);
}

export function time24Part(value: string, maximum: number): number | null {
 const text = time24Digits(value).trim();
 if (!/^\d{1,2}$/.test(text)) return null;
 const parsed = Number(text);
 return parsed <= maximum ? parsed : null;
}

export function time24DialPoint(value: number, mode: 'hour' | 'minute') {
 const angle = (mode === 'hour' ? value % 12 / 12 : value / 60) * Math.PI * 2;
 const radius = mode === 'minute' || value === 0 || value > 12 ? 41 : 27;
 return { left: 50 + Math.sin(angle) * radius, top: 50 - Math.cos(angle) * radius };
}

export function time24DialValue(x: number, y: number, size: number, mode: 'hour' | 'minute') {
 const dx = x - size / 2, dy = y - size / 2;
 const angle = (Math.atan2(dx, -dy) + Math.PI * 2) % (Math.PI * 2);
 if (mode === 'minute') return Math.round(angle / (Math.PI * 2) * 60) % 60;
 const hour = Math.round(angle / (Math.PI * 2) * 12) % 12;
 return Math.hypot(dx, dy) >= size * .34 ? hour === 0 ? 0 : hour + 12 : hour || 12;
}
