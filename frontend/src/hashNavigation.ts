/** Keep form guards active for browser history as well as navigation buttons. */
const marker = '__pakHashPosition';
function position() {
 const value = window.history.state?.[marker];
 return Number.isSafeInteger(value) ? value as number : null;
}
function stateAt(index: number) {
 const state = window.history.state;
 return { ...(state && typeof state === 'object' ? state : {}), [marker]: index };
}
const pageAtLocation = () => window.location.hash.slice(1) || 'inbox';

export function installHashNavigation(changed: (page: string) => void) {
 let index = position() ?? 0, acceptedUrl = window.location.href, restoring = false;
 window.history.replaceState(stateAt(index), '', acceptedUrl);
 const allowed = () => window.dispatchEvent(new Event('pak:before-navigation', { cancelable: true }));
 function accept(next: number) {
  index = next; acceptedUrl = window.location.href;
  window.history.replaceState(stateAt(index), '', acceptedUrl);
  changed(pageAtLocation());
 }
 function hashChanged() {
  const next = position();
  if (restoring) {
   if (next === index && window.location.href === acceptedUrl) restoring = false;
   else if (next !== null && next !== index) window.history.go(index - next);
   else { window.history.replaceState(stateAt(index), '', acceptedUrl); restoring = false; }
   return;
  }
  if (window.location.href === acceptedUrl) return;
  if (allowed()) { accept(next ?? index + 1); return; }
  // Known entries retain their original Back/Forward positions. Do not push a
  // replacement entry, which would destroy the forward branch or create loops.
  if (next !== null && next !== index) { restoring = true; window.history.go(index - next); }
  else {
   // A manually typed fragment or history entry predating this app has no
   // position. Restore its URL in place while keeping the unsaved form mounted.
   window.history.replaceState(stateAt(index), '', acceptedUrl);
  }
 }
 window.addEventListener('hashchange', hashChanged);
 return {
  navigate(page: string) {
   if (pageAtLocation() === page || restoring) return false;
   if (!allowed()) return false;
   index++;
   window.history.pushState(stateAt(index), '', '#' + page);
   acceptedUrl = window.location.href;
   changed(page);
   return true;
  },
  replace(page: string) {
   restoring = false;
   window.history.replaceState(stateAt(index), '', '#' + page);
   acceptedUrl = window.location.href;
   changed(page);
  },
  dispose() { window.removeEventListener('hashchange', hashChanged); }
 };
}
