import ReactDOM from 'react-dom/client';
import App from './App';
import './tailwind.css';
import './styles.css';
import './branding.css';
import './enterprise.css';
document.body.classList.add('pak-enterprise');
ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
if (import.meta.env.PROD && 'serviceWorker' in navigator) window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
