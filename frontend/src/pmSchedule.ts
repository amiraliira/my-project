export const pmMonths = ['فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند'];
export interface ScheduleSlot { month: number; week: number; }
export interface PmRecurrence { mode: 'annual-slots' | 'source-period'; unit?: 'week' | 'month'; interval?: number; anchorYear?: number; anchorMonth?: number; week?: number; meterPolicy?: 'calendar-only' | 'meter-only' | 'whichever-first'; }
export function persianYear(iso?: string) {
 const value = iso ? new Date(iso + 'T12:00:00Z') : new Date();
 return Number(new Intl.DateTimeFormat('en-US-u-ca-persian', {timeZone:'Asia/Tehran',year:'numeric'}).formatToParts(value).find(p=>p.type==='year')?.value);
}
export function persianMonth(iso: string) { return Number(new Intl.DateTimeFormat('en-US-u-ca-persian',{timeZone:'UTC',month:'numeric'}).formatToParts(new Date(iso+'T12:00:00Z')).find(p=>p.type==='month')?.value); }
/** A preview of the company's four complete weeks per Persian month. Actual issuance stays on the server. */
export function recurrenceSlots(recurrence: PmRecurrence | undefined, manual: ScheduleSlot[], year: number): ScheduleSlot[] {
 if (!recurrence || recurrence.mode === 'annual-slots') return manual;
 const {unit,interval,anchorYear,anchorMonth,week}=recurrence;
 if ((unit!=='week'&&unit!=='month') || !interval || !Number.isInteger(interval) || interval<1 || interval>(unit==='week'?48:120) || !anchorYear || !Number.isInteger(anchorYear) || anchorYear<1 || !Number.isInteger(year) || year<1 || !anchorMonth || !Number.isInteger(anchorMonth) || anchorMonth<1 || anchorMonth>12 || !week || !Number.isInteger(week) || week<1 || week>4) return [];
 const anchorMonthKey=anchorYear*12+anchorMonth-1, anchorWeekKey=anchorMonthKey*4+week-1;
 const result: ScheduleSlot[]=[];
 for(let month=1;month<=12;month++) for(let w=1;w<=4;w++) {
  const key=year*12+month-1, delta=unit==='week' ? key*4+w-1-anchorWeekKey : key-anchorMonthKey;
  if(delta>=0 && delta%interval===0 && (unit==='week'||w===week)) result.push({month,week:w});
 }
 return result;
}
export function recurrenceLabel(value?: PmRecurrence) {
 if (!value || value.mode==='annual-slots') return 'انتخاب هفته‌ها از تقویم';
 const interval=value.interval||1;
 if(value.unit==='week')return interval===1?'هفتگی':`هر ${interval.toLocaleString('fa-IR')} هفتهٔ برنامه`;
 return interval===1?'ماهانه':interval===12?'سالانه':`هر ${interval.toLocaleString('fa-IR')} ماه`;
}
/** Existing source calendar slots and approved chief changes take precedence over an initial period suggestion. */
export function initialRecurrence(plan:{recurrence?:PmRecurrence;slots:ScheduleSlot[];sourcePeriodSuggestion?:Partial<PmRecurrence>|null;startDate?:string;version?:number;mappingConfirmed?:boolean}, fallbackDate:string):PmRecurrence {
 const existing=plan.recurrence||{mode:'annual-slots'};
 if(existing.mode==='source-period'||plan.slots.length||(plan.version||1)>1||plan.mappingConfirmed)return {...existing};
 const suggestion=plan.sourcePeriodSuggestion;
 if(!suggestion||(suggestion.unit!=='week'&&suggestion.unit!=='month')||!Number.isInteger(suggestion.interval)||(suggestion.interval||0)<1)return {...existing};
 const start=plan.startDate||fallbackDate;
 return {mode:'source-period',unit:suggestion.unit,interval:suggestion.interval,anchorYear:suggestion.anchorYear||persianYear(start),anchorMonth:suggestion.anchorMonth||persianMonth(start),week:suggestion.week||1,meterPolicy:'calendar-only'};
}
export const periodLabels: Record<string,string>={weekly:'هفتگی',monthly:'ماهانه',bimonthly:'دوماهه',quarterly:'سه‌ماهه','six-monthly':'شش‌ماهه','ten-monthly':'ده‌ماهه',annual:'سالانه','day-interval':'دوره برحسب روز','multi-year':'چندساله','other-monthly':'سایر دوره‌های ماهیانه',other:'سایر دوره‌ها',unknown:'دوره نامشخص'};
export function formPeriodGroup(form:{periodClass?:string;periodLabel?:string;period?:string;intervalMonths?:number;intervalDays?:number}) {
 const kind=form.periodClass||'unknown',months=form.intervalMonths,days=form.intervalDays;
 const format=(value:number)=>value.toLocaleString('fa-IR');
 if(Number.isInteger(days)&&(days||0)>0)return {key:kind+':days:'+days,label:`هر ${format(days!)} روز`};
 if(Number.isInteger(months)&&(months||0)>0){const named:Record<number,string>={1:'ماهانه',2:'دوماهه',3:'سه‌ماهه',6:'شش‌ماهه',10:'ده‌ماهه',12:'سالانه'};return {key:kind+':months:'+months,label:named[months!]||(months!%12===0?`هر ${format(months!/12)} سال`:`هر ${format(months!)} ماه`)};}
 if(kind==='weekly')return {key:kind,label:'هفتگی'};
 const label=form.periodLabel||form.period||periodLabels[kind]||'دوره در برگه مشخص نشده';return {key:kind+':'+label,label};
}
