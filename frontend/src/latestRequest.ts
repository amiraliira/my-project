/** Only the latest selection may update a view, even if a transport ignores abort. */
export function createLatestRequest() {
 let active: AbortController | undefined;
 return {
  async run<T>(load: (signal: AbortSignal) => Promise<T>): Promise<{ current: true; value: T } | { current: false }> {
   active?.abort();
   const request = new AbortController(); active = request;
   try {
    const value = await load(request.signal);
    return active === request && !request.signal.aborted ? { current: true, value } : { current: false };
   } catch (error) {
    if (active !== request || request.signal.aborted) return { current: false };
    throw error;
   } finally { if (active === request) active = undefined; }
  },
  cancel() { active?.abort(); active = undefined; },
 };
}
