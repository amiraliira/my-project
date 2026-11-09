export function entryNumber(value:string|number|null|undefined):number|null {
 if(value==null||String(value).trim()==='')return null;
 let normalized=String(value).replace(/[۰-۹]/g,c=>String(c.charCodeAt(0)-1776)).replace(/[٠-٩]/g,c=>String(c.charCodeAt(0)-1632)).replace(/٫/g,'.').trim();
 if(/[,٬]/.test(normalized)&&!/^\d{1,3}(?:[,٬]\d{3})+(?:\.\d+)?$/.test(normalized))return Number.NaN;
 normalized=normalized.replace(/[,٬]/g,'');
 if(!/^[0-9]+(?:\.[0-9]+)?$/.test(normalized))return Number.NaN;
 const parsed=Number(normalized);return Number.isFinite(parsed)?parsed:Number.NaN;
}
export function durationClock(minutes:number):string {
 if(!Number.isSafeInteger(minutes)||minutes<0)return '—';
 return String(Math.floor(minutes/60)).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0');
}
export function firstJalaliMonthDay(iso:string):string {
 const d=new Date(iso+'T12:00:00Z');
 const day=Number(new Intl.DateTimeFormat('en-US-u-ca-persian',{timeZone:'UTC',day:'numeric'}).format(d));
 d.setUTCDate(d.getUTCDate()-day+1);return d.toISOString().slice(0,10);
}
export function entryClock(value:string):string {
 let s=value.trim().replace(/[۰-۹]/g,c=>String(c.charCodeAt(0)-1776)).replace(/[٠-٩]/g,c=>String(c.charCodeAt(0)-1632));
 if(/^[0-9]{3,4}$/.test(s)){s=s.padStart(4,'0');s=s.slice(0,2)+':'+s.slice(2);}
 if(/^[0-9]:[0-9]{2}$/.test(s))s='0'+s;
 return s;
}
