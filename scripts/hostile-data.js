/* Escaping test for the page. Everything Pilothouse shows comes from the machine (container names, process
   names, folder names, commit subjects, session titles, log lines), so every one of those strings may be hostile.

   How to use:
     1. python3 server.py --demo --port 7792
     2. open http://localhost:7792, open the browser console, paste this whole file, press Enter
     3. wait about 30 seconds for the result object

   It appends an HTML/JS breakout payload to every string the API returns, walks every tab, drawer and menu,
   and reports where the payload turned into a real element or escaped a string inside an inline handler.
   A clean run prints  fired: 0, sinks: {}, badHandlers: [].  Anything else is a bug: escape that value with
   esc() (text and attributes) or jq() (inside an inline handler). */
(async () => {
  window.__xss = 0; const sinks = {};
  const P = 'x"\'`><img src=x onerror="window.__xss++">';
  const desc = el => { const p = []; let e = el; for (let i = 0; i < 4 && e && e !== document.body; i++) {
    p.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : '')); e = e.parentElement; } return p.join(' < '); };
  const note = img => { const k = desc(img.parentElement) + ' || ' + (img.parentElement.textContent || '').split('x"')[0].slice(-40); sinks[k] = (sinks[k] || 0) + 1; };
  new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { if (n.nodeType !== 1) return;
    if (n.matches('img[src="x"]')) note(n); n.querySelectorAll('img[src="x"]').forEach(note); }))).observe(document.body, { childList: true, subtree: true });
  // values the page uses as identifiers or switches keep their meaning; everything else gets the payload
  const KEEP = new Set(['id', 'kind', 'state', 'level', 'http', 'range', 'platform', 'actions']);
  const mut = (v, k) => typeof v === 'string' ? (KEEP.has(k) ? v : v + P)
    : Array.isArray(v) ? (k === 'actions' ? v : k === 'edges' ? v.map(e => [e[0], e[1], e[2] + P, e[3]]) : v.map(x => mut(x, k)))
    : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([a, b]) => [a, mut(b, a)])) : v;
  const f0 = window.fetch;
  window.fetch = async (u, o) => { const r = await f0(u, o); if (!String(u).includes('/api/') || (o && o.method === 'POST')) return r;
    return new Response(JSON.stringify(mut(await r.json(), '')), { status: r.status, headers: { 'Content-Type': 'application/json' } }); };
  const sleep = ms => new Promise(r => setTimeout(r, ms)), errs = [];
  const go = async (what, fn, wait = 400) => { try { await fn(); } catch (e) { errs.push(what + ': ' + e); } await sleep(wait); };
  window.addEventListener('error', e => errs.push(String(e.message).slice(0, 120)));
  await pollStatus(); await pollMetrics(); await loadInv(true);
  for (const t of TABS) await go('tab ' + t, () => showTab(t), 700);
  await go('usage', loadUsage); await go('now', pollNow);
  for (const r of ['today', '30d']) await go('range ' + r, () => { usRange = r; return loadUsage(); });
  await go('usage tab', () => showTab('usage'), 600);
  await go('session', () => openUsageSession(US.sessions[0].id), 500);
  showTab('services');
  for (const s of S.services.filter((s, i, a) => a.findIndex(x => x.kind === s.kind && x.up === s.up) === i)) {
    await go('drawer ' + s.id, () => openDrawer(s.id), 600); await go('logs ' + s.id, () => openDrawer(s.id, 'logs'), 500); }
  await go('menu', () => { document.querySelector('[onclick*="menu(event"]').click(); }, 300); await go('menu close', () => closeMenu(), 100);
  showTab('models'); for (const i of [0, 5, 7]) await go('model ' + i, () => openModel(i), 350);
  showTab('agents'); for (const a of INV.agents.slice(0, 3)) await go('agent ' + a.id, () => selectAgent(a.id), 300);
  await go('away strip', () => showSince(0)); await go('toast', () => toast('ok', 't' + P, 'm' + P));
  for (const t of ['repos', 'docker', 'activity', 'overview']) await go('tab ' + t, () => showTab(t), 500);
  await go('app rows', () => { document.querySelector('[onclick^="toggleApp"]').click(); }, 700);
  // inside an inline handler the payload must sit within a double-quoted JS string and nowhere else
  const strip = a => a.replace(/"(?:[^"\\]|\\.)*"/g, '""');
  const badHandlers = [...document.querySelectorAll('*')].flatMap(el => [...el.attributes]
    .filter(a => a.name.startsWith('on') && /onerror|<img/.test(strip(a.value))).map(a => a.name + '=' + a.value.slice(0, 140)));
  const result = { fired: window.__xss, sinks, badHandlers, handlersCarryingPayloadSafely: [...document.querySelectorAll('[onclick]')].filter(e => e.getAttribute('onclick').includes('onerror')).length, scriptErrors: errs };
  console.log(result); window.__hostileResult = result; return result;
})();
