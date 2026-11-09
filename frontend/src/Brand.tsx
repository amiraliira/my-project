export const companySystemTitle = 'سیستم نگهداری و تعمیرات شرکت لبنیات پاک';

export default function Brand({ compact = false }: { compact?: boolean }) {
 return <span className={'pak-brand ' + (compact ? 'compact' : '')} aria-label={companySystemTitle}>
  <img className="pak-logo" src="/pak-logo.png" width={compact ? 46 : 88} height={compact ? 46 : 88} alt="لوگوی شرکت لبنیات پاک"/>
  <span className="pak-brand-copy"><strong>نگهداری و تعمیرات</strong><small>شرکت لبنیات پاک</small></span>
 </span>;
}
