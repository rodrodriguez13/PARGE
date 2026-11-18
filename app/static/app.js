/* Site Deployment Kit — Altavia × Idóneos. JS puro, sin dependencias. */
const S = { projects: [], project: null, profiles: [], status: {}, devices: [], summary: null, racks: [],
            plan: null, view: 'inventory', tab: 'racks', itab: 'aps', instSel: null, instPage: 0, portmap: null, selMarker: null, planPage: 0, filter: { q: '', type: '', status: '' } };

const $ = (s, el = document) => el.querySelector(s);
function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag); let val;
  for (const [k, v] of Object.entries(props || {})) {
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k === 'value') val = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, '');
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  kids.flat(9).forEach(c => { if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(c)); });
  if (val !== undefined) e.value = val;
  return e;
}
function toast(msg, err = false) {
  const t = $('#toast'); t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.className = '', err ? 5000 : 2600);
}
async function api(method, url, body) {
  const opt = { method, headers: {} };
  const who = localStorage.getItem('dk_name'); if (who) opt.headers['X-User-Name'] = encodeURIComponent(who);
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  if (r.status === 401 && !url.startsWith('/api/login')) { showLogin(); throw new Error('Introduce el código de acceso'); }
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r;
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); console.error(e); } };
const pid = () => S.project.id;
const STATUS = { expected: 'Esperado', received: 'Recibido', confirmed: 'Confirmado', mismatch: 'No coincide', added: 'Añadido (no en plantilla)' };
const badge = s => h('span', { class: 'badge b-' + s }, STATUS[s] || s);
function nameCell(d) {
  const span = h('span', { class: 'name' });
  d.name.split(/(\?)/).forEach(p => span.append(p === '?' ? h('span', { class: 'miss', title: 'Falta: ' + d.name_missing.join(', ') }, '?') : p));
  return span;
}
function download(url) { const a = h('a', { href: url }); document.body.append(a); a.click(); a.remove(); }

/* ---------------------------------------------------------------- arranque */
function showLogin(msg) {
  const old = $('#login'); if (old) old.remove();
  const code = h('input', { type: 'password', placeholder: 'Código de acceso', autocomplete: 'current-password', style: 'width:100%;font-size:18px;padding:12px' });
  const name = h('input', { list: 'teamlist', placeholder: 'Tu nombre', value: localStorage.getItem('dk_name') || '', style: 'width:100%;font-size:18px;padding:12px' });
  const err = h('div', { class: 'bad-t', style: 'min-height:20px' }, msg || '');
  const go = async () => {
    if (!name.value.trim()) { err.textContent = 'Escribe tu nombre para saber quién instala cada equipo'; return; }
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: code.value }) });
    if (!r.ok) { let m = 'No se pudo entrar'; try { m = (await r.json()).detail || m; } catch (e) {} err.textContent = m; return; }
    localStorage.setItem('dk_name', name.value.trim()); location.reload();
  };
  code.onkeydown = e => { if (e.key === 'Enter') go(); };
  document.body.append(h('div', { id: 'login' }, h('div', { class: 'card logincard' },
    h('div', { class: 'logos', style: 'justify-content:center;margin-bottom:14px' }, h('img', { src: '/static/logo_idoneos.png', class: 'logo-id' }), h('img', { src: '/static/logo_altavia.png', class: 'logo-al' })),
    h('h2', { style: 'text-align:center' }, 'Site Deployment Kit'),
    h('datalist', { id: 'teamlist' }, (S.team || []).map(n => h('option', { value: n }))),
    name, h('div', { style: 'height:8px' }), code, err, h('button', { class: 'btn primary big', style: 'width:100%', onclick: go }, 'Entrar'))));
  code.focus();
}
function logout() { fetch('/api/logout', { method: 'POST' }).then(() => location.reload()); }
async function boot() {
  const cfg = await (await fetch('/api/config')).json(); S.team = cfg.team;
  if (cfg.auth && !cfg.logged_in) return showLogin();
  S.status = await api('GET', '/api/status');
  S.profiles = await api('GET', '/api/profiles');
  await loadProjects();
  $('#aiChip').className = 'chip ' + (S.status.ai ? 'ok' : 'warn');
  $('#aiChip').textContent = S.status.ai ? '● Lectura de etiquetas: IA activa' : '● Lectura de etiquetas: OCR local (sin clave de IA)';
  $('#newProject').onclick = guard(newProject);
  $('#projectSelect').onchange = e => selectProject(+e.target.value);
  const me = localStorage.getItem('dk_name') || '';
  $('#whoami').append(h('span', {}, me ? '👤 ' + me : ''), ...(cfg.auth ? [h('button', { class: 'link', onclick: logout }, 'Salir')] : []));
  setInterval(autoRefresh, 25000);
}
/* El PM (y los técnicos entre sí) ven el avance casi en vivo: refresco suave cada 25 s en la pantalla de instalación */
async function autoRefresh() {
  if (document.hidden || S.view !== 'install') return;
  const ae = document.activeElement; if (ae && /INPUT|SELECT|TEXTAREA/.test(ae.tagName)) return;
  try { await refreshAll(); await loadPortmap(); if (S.view === 'install') { render(); } } catch (e) {}
}
async function loadProjects(keep) {
  S.projects = await api('GET', '/api/projects');
  const sel = $('#projectSelect'); sel.innerHTML = '';
  S.projects.forEach(p => sel.append(h('option', { value: p.id }, `${p.name}${p.inn_code ? ' · ' + p.inn_code : ''}`)));
  await selectProject(keep || (S.project && S.projects.find(p => p.id === S.project.id) ? S.project.id : S.projects[0].id));
}
async function selectProject(id) {
  S.project = S.projects.find(p => p.id === id); $('#projectSelect').value = id;
  S.plan = null; S.selMarker = null; S.planPage = 0;
  await refreshAll(); renderNav(); render(); drawLogo();
}
function drawLogo() {
  const el = $('#siteLogo'); if (!el) return; const p = S.project;
  el.innerHTML = '';
  if (p && p.has_logo) el.append(h('img', { src: `/api/projects/${p.id}/logo?v=${p.logo_v || ''}`, alt: p.name }));
  else if (p && p.chain === 'Hilton') el.append(h('img', { src: '/static/logo_hilton.png', alt: 'Hilton' }));
}
async function refreshAll() {
  [S.devices, S.summary, S.racks] = await Promise.all([api('GET', `/api/projects/${pid()}/devices`),
    api('GET', `/api/projects/${pid()}/summary`), api('GET', `/api/projects/${pid()}/racks`)]);
}
async function newProject() {
  const name = prompt('Nombre del hotel / sitio:'); if (!name) return;
  const inn = prompt('Inn code (p. ej. PARGE):') || '';
  const chain = S.profiles.length > 1 ? prompt('Cadena (' + S.profiles.map(p => p.name).join(' / ') + '):', S.profiles[0].name) : S.profiles[0].name;
  const p = await api('POST', '/api/projects', { name, inn_code: inn, chain });
  await loadProjects(p.id); toast('Sitio creado');
}
const NAV = [['settings', 'Sitio y nombres', 'Datos del hotel · plantillas de nombre'], ['inventory', '1 · Inventario', 'Plantilla · fotos de cajas'],
             ['location', '2 · Ubicación', 'Racks · AP Plan'], ['install', '3 · Instalación', 'APs instalados · fotos · puertos de switch']];
function renderNav() {
  const nav = $('#nav'); nav.innerHTML = '';
  NAV.forEach(([k, t, s]) => nav.append(h('button', { class: S.view === k ? 'on' : '', onclick: () => { S.view = k; renderNav(); render(); } }, t, h('small', {}, s))));
}
function render() {
  const m = $('#main'); m.innerHTML = '';
  ({ settings: viewSettings, inventory: viewInventory, location: viewLocation, install: viewInstall })[S.view](m);
}
const header = (t, s) => [h('h1', {}, t), h('p', { class: 'sub' }, s)];

/* ---------------------------------------------------------------- SITIO Y NOMBRES */
const TOKENS = [['{INN}', 'Inn code'], ['{MODEL}', 'modelo corto'], ['{FLOOR}', 'planta'], ['{ROOM}', 'habitación'], ['{LOC}', 'rack (MDF/IDF)'],
                ['{ROLE}', 'rol: AP/AS/CS/MX'], ['{N:02}', 'número (2 dígitos)'], ['{N:03}', 'número (3 dígitos)'], ['{CODE}', 'código de caja'], ['{ZONE}', 'zona (CORR…) · solo AP+zona']];
function viewSettings(m) {
  const p = S.project, cfg = JSON.parse(JSON.stringify(p.config));
  m.append(...header('Sitio y nombres de dispositivos', 'Aquí defines una vez cómo se llaman los equipos de esta cadena. Todo lo demás se calcula solo.'));
  const f = { name: h('input', { value: p.name, style: 'width:100%' }), inn: h('input', { value: p.inn_code, style: 'width:140px;text-transform:uppercase' }),
              brand: h('input', { value: p.brand || '', style: 'width:100%' }),
              chain: h('select', {}, S.profiles.map(x => h('option', { value: x.name, selected: x.name === p.chain }, x.name))) };
  const logoIn = h('input', { type: 'file', accept: 'image/*', style: 'display:none', onchange: guard(async e => {
    const fd = new FormData(); fd.append('file', e.target.files[0]); const np = await api('POST', `/api/projects/${pid()}/logo`, fd);
    S.projects = S.projects.map(x => x.id === np.id ? np : x); S.project = np; drawLogo(); toast('Logo guardado'); render(); }) });
  m.append(h('div', { class: 'card' }, h('div', { class: 'row' }, p.has_logo ? h('img', { src: `/api/projects/${p.id}/logo?v=${p.logo_v || ''}`, style: 'height:56px;max-width:200px;object-fit:contain' }) : h('div', { class: 'hint' }, 'Sin logo propio del hotel: se muestra el de la cadena'),
    h('span', { class: 'grow' }), h('button', { class: 'btn', onclick: () => logoIn.click() }, p.has_logo ? 'Cambiar logo del hotel' : 'Subir logo del hotel'), logoIn),
    h('div', { class: 'hint' }, 'Aparece en la barra lateral. Usa el logo oficial que te dé el hotel o Hilton (PNG o JPG).')));
  m.append(h('div', { class: 'card' }, h('h2', {}, 'Datos del sitio'), h('div', { class: 'row' },
    h('div', { class: 'grow' }, h('label', { class: 'hint' }, 'Nombre del hotel'), f.name),
    h('div', {}, h('label', { class: 'hint' }, 'Inn code'), h('br'), f.inn),
    h('div', {}, h('label', { class: 'hint' }, 'Cadena'), h('br'), f.chain),
    h('div', { class: 'grow' }, h('label', { class: 'hint' }, 'Marca / colección'), f.brand))));

  const tpl = h('div', { class: 'card' }, h('h2', {}, 'Plantillas de nombre por tipo de equipo'),
    h('p', { class: 'hint' }, 'Escribe el patrón. Lo que no esté entre llaves es texto fijo (p. ej. HLT-). Pulsa un token para insertarlo en el campo activo.'),
    h('div', {}, TOKENS.map(([t, d]) => h('span', { class: 'tok', title: d, onmousedown: e => { e.preventDefault(); insertToken(t); } }, t))));
  const prevs = {};
  const sample = { AP: { model: 'CW9172H', role: 'AP', n: 23 }, SW: { model: 'C9300-48U', role: 'AS', n: 3 }, MX: { model: 'MX105', role: 'MX', n: 1 },
                   WLC: { model: 'C9800-L', role: 'WLC', n: 1 }, SRV: { model: 'SRV', role: 'SRV', n: 1 }, TV: { model: 'TV', role: 'TV', n: 1 }, OTHER: { model: 'XXX', role: 'DV', n: 1 } };
  const upd = guard(async t => {
    const sm = sample[t.replace('_ZONE', '')];
    const r = await api('GET', `/api/name-preview?template=${encodeURIComponent(cfg.templates[t] || '')}&inn=${encodeURIComponent(f.inn.value || 'INN')}&model=${sm.model}&role=${cfg.role_codes[t.replace('_ZONE', '')] || sm.role}&n=${sm.n}&zone=CORR`);
    prevs[t].textContent = r.name;
  });
  (S.status.device_types || []).flatMap(t => t === 'AP' ? ['AP', 'AP_ZONE'] : [t]).forEach(t => {
    const inp = h('input', { class: 'mono tplinput', value: cfg.templates[t] || '', oninput: e => { cfg.templates[t] = e.target.value; upd(t); } });
    prevs[t] = h('span', { class: 'name' });
    if (t === 'AP_ZONE') { cfg.templates[t] = cfg.templates[t] || ''; }
    const role = t === 'AP_ZONE' ? h('span') : h('input', { value: cfg.role_codes[t] || '', style: 'width:70px', title: 'Rol por defecto ({ROLE})', oninput: e => { cfg.role_codes[t] = e.target.value; upd(t); } });
    tpl.append(h('div', { class: 'tplrow' }, h('b', { title: t === 'AP_ZONE' ? 'Se usa en los modelos que tengan zona (ver más abajo)' : '' }, t === 'AP_ZONE' ? 'AP + zona' : t), inp, h('div', {}, prevs[t])));
    if (t !== 'AP_ZONE') tpl.lastChild.querySelector('div:last-child').append(h('span', { class: 'hint' }, '  rol '), role);
    upd(t);
  });
  m.append(tpl);

  cfg.floor_labels = cfg.floor_labels || {};
  m.append(h('div', { class: 'card' }, h('h2', {}, 'Cómo se escribe la planta en el nombre'),
    h('p', { class: 'hint' }, 'Formato «planta=texto» separado por comas. Ejemplo: -1=LL, 0=GF. Las plantas que no pongas se escriben tal cual.'),
    h('input', { style: 'width:100%', value: Object.entries(cfg.floor_labels).map(([k, v]) => `${k}=${v}`).join(', '), placeholder: '-1=LL',
      oninput: e => { cfg.floor_labels = {}; e.target.value.split(',').forEach(x => { const [k, v] = x.split('='); if (k && v && k.trim()) cfg.floor_labels[k.trim()] = v.trim(); }); } })));
  const models = [...new Set(S.devices.map(d => d.model).filter(Boolean))];
  const al = h('div', { class: 'card' }, h('h2', {}, 'Código corto y zona por modelo'),
    h('p', { class: 'hint' }, 'Código corto: se deduce solo (CW9172H → 9172H). Zona: para APs que no van a una habitación concreta (p. ej. CORR en pasillos); esos modelos usan la plantilla «AP + zona».'));
  cfg.model_zones = cfg.model_zones || {};
  if (!models.length) al.append(h('div', { class: 'hint' }, 'Aparecerá cuando haya modelos en el inventario.'));
  cfg.model_aliases = cfg.model_aliases || {};
  models.forEach(mod => {
    const auto = S.devices.find(d => d.model === mod).model_short;
    al.append(h('div', { class: 'row', style: 'margin:4px 0' }, h('span', { class: 'mono', style: 'width:200px' }, mod), '→',
      h('input', { value: cfg.model_aliases[mod] || '', placeholder: auto, style: 'width:120px', oninput: e => { if (e.target.value) cfg.model_aliases[mod] = e.target.value; else delete cfg.model_aliases[mod]; } }),
      h('span', { class: 'hint' }, '  zona'), h('input', { value: cfg.model_zones[mod] || '', placeholder: '(ninguna)', style: 'width:110px;text-transform:uppercase', oninput: e => { if (e.target.value) cfg.model_zones[mod] = e.target.value.toUpperCase(); else delete cfg.model_zones[mod]; } })));
  });
  m.append(al);
  m.append(h('div', { class: 'row' },
    h('button', { class: 'btn primary', onclick: guard(async () => {
      const upd2 = await api('PUT', `/api/projects/${pid()}`, { name: f.name.value, inn_code: f.inn.value, brand: f.brand.value, chain: f.chain.value, config: cfg });
      Object.assign(S.project, upd2); await loadProjects(pid()); toast('Guardado');
    }) }, 'Guardar cambios'),
    h('button', { class: 'btn', onclick: guard(async () => {
      const name = prompt('Guardar estas plantillas como perfil de cadena (p. ej. Marriott):', f.chain.value); if (!name) return;
      await api('POST', '/api/profiles', { name, config: cfg }); S.profiles = await api('GET', '/api/profiles'); toast(`Perfil «${name}» guardado: úsalo al crear otros sitios`);
    }) }, 'Guardar como perfil de cadena'),
    h('button', { class: 'btn danger', onclick: guard(async () => {
      if (!confirm('¿Borrar este sitio y todo su inventario?')) return;
      await api('DELETE', `/api/projects/${pid()}`); S.project = null; await loadProjects(); toast('Sitio borrado');
    }) }, 'Borrar sitio')));
  m.append(h('div', { class: 'card' }, h('h2', {}, 'Vaciar datos cargados'),
    h('p', { class: 'hint' }, 'Borra equipos, racks, escaneos, el AP Plan y las fotos de este sitio, y conserva los ajustes (nombres, plantillas, logo). Útil para pasar de las pruebas a la instalación real.'),
    h('button', { class: 'btn danger', onclick: guard(async () => {
      const t = prompt('Esto borra TODO lo cargado en este sitio y no se puede deshacer.\nEscribe VACIAR para confirmar:');
      if (t !== 'VACIAR') return;
      await api('POST', `/api/projects/${pid()}/reset`, { confirm: 'VACIAR' }); S.plan = null; S.portmap = null; S.instSel = null; await refreshAll(); toast('Datos vaciados'); render();
    }) }, 'Vaciar datos cargados')));
}
let lastTpl = null; let addMode = null;
document.addEventListener('focusin', e => { if (e.target.classList && e.target.classList.contains('tplinput')) lastTpl = e.target; });
function insertToken(t) {
  if (!lastTpl) return toast('Pulsa antes en el campo de la plantilla');
  const i = lastTpl.selectionStart ?? lastTpl.value.length;
  lastTpl.value = lastTpl.value.slice(0, i) + t + lastTpl.value.slice(lastTpl.selectionEnd ?? i);
  lastTpl.dispatchEvent(new Event('input')); lastTpl.focus();
}

/* ---------------------------------------------------------------- 1 · INVENTARIO */
function viewInventory(m) {
  m.append(...header('1 · Control de inventario', 'Sube la plantilla (si existe), fotografía las cajas al recibirlas y el sistema valida lo que llega contra lo esperado.'));
  const sm = S.summary, bs = sm.by_status, tot = S.devices.length;
  m.append(h('div', { class: 'stats' },
    stat(tot, 'Equipos en total'), stat(bs.expected || 0, 'Esperados (aún no llegan)'), stat((bs.received || 0) + (bs.confirmed || 0), 'Recibidos'),
    stat(bs.confirmed || 0, 'Confirmados vs plantilla'), stat(bs.mismatch || 0, 'No coinciden'), stat(bs.added || 0, 'Fuera de plantilla')));

  const up = h('input', { type: 'file', accept: '.xlsx,.xlsm,.csv', style: 'display:none', onchange: guard(async e => {
    const fd = new FormData(); fd.append('file', e.target.files[0]); const r = await api('POST', `/api/projects/${pid()}/inventory/import`, fd);
    S.lastImport = r.skipped || []; toast(`Leídos ${r.added} equipos activos nuevos, ${r.matched} validados contra lo ya recibido`); e.target.value = ''; await refreshAll(); render();
  }) });
  const cam = h('input', { type: 'file', accept: 'image/*', capture: 'environment', multiple: true, style: 'display:none', onchange: e => scanFiles(e.target.files, e.target) });
  const pick = h('input', { type: 'file', accept: 'image/*', multiple: true, style: 'display:none', onchange: e => scanFiles(e.target.files, e.target) });
  m.append(h('div', { class: 'card tint' }, h('div', { class: 'row' },
    h('button', { class: 'btn', onclick: () => up.click() }, '⬆ Subir plantilla de inventario'),
    h('button', { class: 'btn', onclick: () => download('/api/inventory/template.xlsx') }, 'Descargar plantilla vacía'),
    h('span', { class: 'grow' }),
    h('button', { class: 'btn primary', onclick: () => cam.click() }, '📷 Fotografiar cajas'),
    h('button', { class: 'btn', onclick: () => pick.click() }, 'Elegir fotos'),
    h('button', { class: 'btn', onclick: () => addManual() }, '+ Añadir manual'),
    up, cam, pick),
    h('p', { class: 'hint', style: 'margin:10px 0 0' }, 'Consejo para las fotos: 4–6 cajas por foto, con la pegatina de frente y sin reflejos; el código a rotulador (AP01, SW01…) visible junto a la pegatina.')));
  m.append(h('div', { id: 'scanzone' }));
  if (S.lastImport && S.lastImport.length) m.append(h('div', { class: 'card', style: 'background:var(--warn-bg)' }, h('div', { class: 'row' }, h('b', {}, 'No importado (no son equipos activos)'), h('span', { class: 'grow' }),
    h('button', { class: 'btn sm', onclick: () => { S.lastImport = null; render(); } }, 'Ocultar')),
    h('div', { class: 'hint', style: 'margin-top:6px' }, S.lastImport.map(x => `${x.model} ×${x.qty}`).join(' · '))));

  const numBox = h('div'); m.append(numBox); if (tot) renderNumbering(numBox);
  if (sm.models.length) m.append(h('div', { class: 'card' }, h('h2', {}, 'Resumen por modelo'),
    h('table', {}, h('thead', {}, h('tr', {}, ['Tipo', 'Modelo', 'Total', 'Recibidos', 'Con código de caja'].map(x => h('th', {}, x)))),
      h('tbody', {}, sm.models.map(r => h('tr', {}, h('td', {}, r.dtype), h('td', { class: 'mono' }, r.model), h('td', {}, r.total),
        h('td', { class: r.received === r.total ? 'ok-t' : r.received ? 'warn-t' : '' }, `${r.received}/${r.total}`), h('td', {}, r.with_box)))))));

  const q = h('input', { placeholder: 'Buscar nombre, serial, MAC, caja…', value: S.filter.q, oninput: e => { S.filter.q = e.target.value; fillRows(); } });
  const ft = h('select', { onchange: e => { S.filter.type = e.target.value; fillRows(); } }, h('option', { value: '' }, 'Todos los tipos'), (S.status.device_types || []).map(t => h('option', { value: t, selected: S.filter.type === t }, t)));
  const fs = h('select', { onchange: e => { S.filter.status = e.target.value; fillRows(); } }, h('option', { value: '' }, 'Todos los estados'), Object.entries(STATUS).map(([k, v]) => h('option', { value: k, selected: S.filter.status === k }, v)));
  const body = h('tbody');
  const card = h('div', { class: 'card' }, h('div', { class: 'row', style: 'margin-bottom:10px' }, h('h2', { style: 'margin:0' }, 'Inventario'), h('span', { class: 'grow' }), q, ft, fs,
    h('button', { class: 'btn', onclick: () => download(`/api/projects/${pid()}/export.xlsx`) }, '⬇ Excel'),
    h('button', { class: 'btn', onclick: () => download(`/api/projects/${pid()}/export.csv`) }, '⬇ CSV')),
    h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Estado', 'Tipo', 'Caja', 'Modelo', 'Serial (etiqueta)', 'Cloud ID', 'MAC', 'Nombre del equipo', 'Notas', ''].map(x => h('th', {}, x)))), body)));
  m.append(card);
  function fillRows() {
    body.innerHTML = ''; const k = S.filter.q.toLowerCase();
    const rows = S.devices.filter(d => (!S.filter.type || d.dtype === S.filter.type) && (!S.filter.status || d.status === S.filter.status) &&
      (!k || [d.name, d.serial, d.cloud_id, d.mac, d.box_code, d.model].join(' ').toLowerCase().includes(k)));
    if (!rows.length) body.append(h('tr', {}, h('td', { colspan: 9, class: 'empty' }, tot ? 'Nada coincide con el filtro.' : 'Aún no hay equipos. Sube la plantilla o fotografía las cajas.')));
    rows.slice(0, 600).forEach(d => body.append(devRow(d)));
    if (rows.length > 600) body.append(h('tr', {}, h('td', { colspan: 9, class: 'hint' }, `Mostrando 600 de ${rows.length}. Usa el filtro.`)));
  }
  fillRows();
}
const stat = (n, l) => h('div', { class: 'stat' }, h('b', {}, n), h('span', {}, l));
function cellInput(d, field, w) {
  return h('input', { value: d[field] || '', style: w ? `width:${w}` : '', class: field === 'serial' || field === 'cloud_id' || field === 'mac' ? 'mono' : '',
    onchange: guard(async e => {
      const row = e.target.closest('tr'); Object.assign(d, await api('PATCH', `/api/devices/${d.id}`, { [field]: e.target.value }));
      if (row && row._draw) row._draw();
    }) });
}
function devRow(d) {
  const row = h('tr'); 
  const draw = () => {
    row.innerHTML = '';
    row.append(h('td', {}, badge(d.status)),
      h('td', {}, h('select', { onchange: guard(async e => { Object.assign(d, await api('PATCH', `/api/devices/${d.id}`, { dtype: e.target.value })); draw(); }) },
        (S.status.device_types || []).map(t => h('option', { value: t, selected: t === d.dtype }, t)))),
      h('td', { style: 'width:100px' }, cellInput(d, 'box_code'), d.spare ? h('span', { class: 'badge b-added', style: 'margin-top:3px' }, 'SPARE') : null), h('td', { style: 'width:150px' }, cellInput(d, 'model')),
      h('td', { style: 'width:170px' }, cellInput(d, 'serial')), h('td', { style: 'width:170px' }, cellInput(d, 'cloud_id')), h('td', { style: 'width:175px' }, cellInput(d, 'mac')),
      h('td', {}, nameCell(d), d.sw_code ? h('div', { class: 'hint' }, `→ ${d.sw_code} · puerto ${d.sw_port}`) : null), h('td', {}, h('span', { class: 'flag' }, d.issues || '')),
      h('td', {}, h('button', { class: 'btn sm danger', title: 'Eliminar', onclick: guard(async () => { if (!confirm('¿Eliminar este equipo?')) return; await api('DELETE', `/api/devices/${d.id}`); S.devices = S.devices.filter(x => x !== d); await refreshAll(); render(); }) }, '✕')));
  };
  row._draw = draw; draw();
  return row;
}
async function addManual() {
  const model = prompt('Modelo (p. ej. CW9172H):'); if (!model) return;
  const box = prompt('Código de caja (p. ej. AP01):') || ''; const serial = prompt('Serial (opcional):') || ''; const mac = prompt('MAC (opcional):') || '';
  await guard(async () => { await api('POST', `/api/projects/${pid()}/devices`, { model, box_code: box, serial, mac }); await refreshAll(); render(); toast('Añadido'); })();
}

/* ---- numeración correlativa de cajas */
async function renderNumbering(host) {
  const N = await api('GET', `/api/projects/${pid()}/numbering`);
  host.innerHTML = '';
  const card = h('div', { class: 'card' }, h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, 'Numeración correlativa de cajas'), h('span', { class: 'grow' }),
    h('button', { class: 'btn', onclick: () => download(`/api/projects/${pid()}/numbering.xlsx`) }, '⬇ Hoja de etiquetas (Excel)')),
    h('p', { class: 'hint' }, 'Define en qué orden se numeran los modelos (el primero lleva los primeros números) y cuántos son spare: los últimos números de cada modelo. El técnico rotula cada caja según la hoja de etiquetas, buscando el serial de la etiqueta (no el Cloud ID).'));
  Object.entries(N).forEach(([t, info]) => {
    const order = info.models.map(x => x.model), spares = {}; info.models.forEach(x => spares[x.model] = x.spares);
    const tb = h('tbody');
    const draw = () => {
      tb.innerHTML = '';
      order.forEach((mod, i) => { const x = info.models.find(y => y.model === mod);
        tb.append(h('tr', {}, h('td', { style: 'width:70px' }, h('button', { class: 'btn sm', disabled: i === 0, onclick: () => { [order[i - 1], order[i]] = [order[i], order[i - 1]]; draw(); } }, '↑'), ' ',
          h('button', { class: 'btn sm', disabled: i === order.length - 1, onclick: () => { [order[i + 1], order[i]] = [order[i], order[i + 1]]; draw(); } }, '↓')),
          h('td', { class: 'mono' }, mod), h('td', {}, x.count), h('td', { style: 'width:90px' }, h('input', { type: 'number', min: 0, max: x.count, value: spares[mod] || 0, onchange: e => spares[mod] = +e.target.value })),
          h('td', { class: 'hint' }, x.coded ? `${x.coded} con código` : ''))); });
    };
    draw();
    const go = guard(async (renumber) => {
      if (renumber && info.models.some(x => x.coded) && !confirm('Ya hay cajas con código. Renumerar cambia los códigos de TODAS: solo hazlo si aún no se han rotulado. ¿Continuar?')) return;
      await api('POST', `/api/projects/${pid()}/numbering`, { dtype: t, order, spares, renumber }); await refreshAll(); toast('Códigos asignados'); render();
    });
    card.append(h('h2', { style: 'margin:14px 0 6px;font-size:15px' }, `${t} · prefijo ${info.prefix}`),
      h('table', {}, h('thead', {}, h('tr', {}, ['Orden', 'Modelo', 'Cantidad', 'Spare', ''].map(x => h('th', {}, x)))), tb),
      h('div', { class: 'row', style: 'margin-top:8px' }, h('button', { class: 'btn primary', onclick: () => go(false) }, 'Asignar códigos a los que no tienen'), h('button', { class: 'btn', onclick: () => go(true) }, 'Renumerar todos')));
  });
  host.append(card);
}

/* ---- fotos -> revisión -> confirmar */
async function scanFiles(files, input) {
  if (!files.length) return;
  const zone = $('#scanzone'); zone.innerHTML = '';
  zone.append(h('div', { class: 'card' }, h('span', { class: 'spinner' }), `Leyendo ${files.length} foto(s)… ${S.status.ai ? '' : '(OCR local: puede tardar y es menos preciso)'}`));
  const fd = new FormData(); [...files].forEach(f => fd.append('files', f)); input.value = '';
  try { const r = await api('POST', `/api/projects/${pid()}/scan`, fd); renderReview(r); }
  catch (e) { zone.innerHTML = ''; toast(e.message, true); }
}
function renderReview(r) {
  const zone = $('#scanzone'); zone.innerHTML = '';
  const items = r.items;
  const body = h('tbody');
  const card = h('div', { class: 'card', style: 'border-color:var(--sky)' }, h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `Revisa lo leído (${items.length})`),
    h('span', { class: 'chip' }, r.provider === 'claude' ? 'Leído con IA' : 'Leído con OCR local')), 
    h('p', { class: 'hint' }, 'Corrige cualquier dato antes de confirmar. Las filas con aviso rojo conviene mirarlas con la caja delante.'));
  const typeSel = it => h('select', { onchange: e => it.dtype = e.target.value }, (S.status.device_types || []).map(t => h('option', { value: t, selected: it.dtype === t }, t)));
  const inp = (it, k, cls) => h('input', { class: cls || '', value: it[k] || '', onchange: e => { it[k] = e.target.value; } });
  items.forEach(it => {
    const row = h('tr', {}, h('td', {}, h('span', { class: 'badge ' + (it.match.startsWith('coincide') ? 'b-confirmed' : it.match === 'no coincide' ? 'b-mismatch' : it.match === 'ya escaneado' ? 'b-expected' : 'b-added') }, it.match)),
      h('td', { style: 'width:80px' }, inp(it, 'box_code')), h('td', { style: 'width:80px' }, typeSel(it)), h('td', { style: 'width:150px' }, inp(it, 'model')),
      h('td', { style: 'width:175px' }, inp(it, 'mac', 'mono')), h('td', { style: 'width:170px' }, inp(it, 'serial', 'mono')),
      h('td', {}, h('span', { class: 'flag' }, [...(it.flags || []), it.notes].filter(Boolean).join(' · '))),
      h('td', {}, h('button', { class: 'btn sm danger', onclick: () => { items.splice(items.indexOf(it), 1); renderReview(r); } }, '✕')));
    body.append(row);
  });
  card.append(h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Validación', 'Caja', 'Tipo', 'Modelo', 'MAC', 'Serial', 'Avisos', ''].map(x => h('th', {}, x)))), body)));
  card.append(h('div', { class: 'row', style: 'margin-top:12px' },
    h('button', { class: 'btn primary', onclick: guard(async () => {
      const res = await api('POST', `/api/projects/${pid()}/scan/commit`, { items, scan_id: r.scan_id });
      toast(`Guardado: ${res.confirmed} confirmados, ${res.received} recibidos, ${res.added} fuera de plantilla, ${res.mismatch} no coinciden, ${res.duplicates} repetidos`);
      await refreshAll(); render();
    }) }, `Confirmar ${items.length} equipos`),
    h('button', { class: 'btn', onclick: () => zone.innerHTML = '' }, 'Descartar')));
  zone.append(card);
  zone.scrollIntoView({ behavior: 'smooth' });
}

/* ---------------------------------------------------------------- 2 · UBICACIÓN */
function viewLocation(m) {
  m.append(...header('2 · Ubicación de equipos', 'Primero los racks, luego dónde va cada equipo y, por último, el plano de APs numerado.'));
  const tabs = h('div', { class: 'tabs' }, [['racks', 'A · Racks'], ['devices', 'B · Equipos en rack'], ['applan', 'C · AP Plan']].map(([k, t]) =>
    h('button', { class: S.tab === k ? 'on' : '', onclick: () => { S.tab = k; render(); } }, t)));
  m.append(tabs);
  const box = h('div'); m.append(box);
  ({ racks: tabRacks, devices: tabDevices, applan: tabApPlan })[S.tab](box);
}

/* ---- A · racks */
function tabRacks(box) {
  const rows = S.racks.length ? S.racks.map(r => ({ ...r })) : [];
  const qty = h('input', { type: 'number', min: 0, max: 40, value: rows.length || 2, style: 'width:80px' });
  const tbody = h('tbody');
  const draw = () => {
    tbody.innerHTML = '';
    rows.forEach((r, i) => tbody.append(h('tr', {},
      h('td', { style: 'width:130px' }, h('input', { value: r.code, class: 'mono', style: 'text-transform:uppercase', onchange: e => r.code = e.target.value.toUpperCase() })),
      h('td', { style: 'width:110px' }, h('select', { onchange: e => r.rtype = e.target.value }, ['MDF', 'IDF'].map(t => h('option', { selected: r.rtype === t }, t)))),
      h('td', {}, h('input', { value: r.location, placeholder: 'Planta −1, cuarto técnico…', onchange: e => r.location = e.target.value })),
      h('td', { style: 'width:90px' }, h('input', { value: r.floor, placeholder: 'planta', onchange: e => r.floor = e.target.value })),
      h('td', { style: 'width:90px' }, h('input', { type: 'number', value: r.units, onchange: e => r.units = +e.target.value })),
      h('td', {}, h('button', { class: 'btn sm danger', onclick: () => { rows.splice(i, 1); qty.value = rows.length; draw(); } }, '✕')))));
  };
  const gen = () => {
    const n = +qty.value || 0;
    while (rows.length < n) { const idf = rows.filter(r => r.rtype === 'IDF').length + 1; const needMdf = !rows.some(r => r.rtype === 'MDF');
      rows.push(needMdf ? { code: 'MDF', rtype: 'MDF', location: '', floor: '', units: 42 } : { code: 'IDF' + String(idf).padStart(2, '0'), rtype: 'IDF', location: '', floor: '', units: 42 }); }
    rows.length = Math.min(rows.length, n); draw();
  };
  box.append(h('div', { class: 'card' }, h('h2', {}, 'Racks del sitio'),
    h('p', { class: 'hint' }, 'El código del rack (MDF, IDF01…) es lo que aparece en el nombre de los switches y equipos ({LOC}).'),
    h('div', { class: 'row' }, h('label', {}, 'Cantidad de racks'), qty, h('button', { class: 'btn', onclick: gen }, 'Generar filas'), h('span', { class: 'grow' }),
      h('button', { class: 'btn', onclick: () => { rows.push({ code: 'IDF' + String(rows.filter(r => r.rtype === 'IDF').length + 1).padStart(2, '0'), rtype: 'IDF', location: '', floor: '', units: 42 }); qty.value = rows.length; draw(); } }, '+ Rack')),
    h('div', { class: 'tablewrap', style: 'margin-top:12px' }, h('table', {}, h('thead', {}, h('tr', {}, ['Código', 'Tipo', 'Ubicación', 'Planta', 'Unidades (U)', ''].map(x => h('th', {}, x)))), tbody)),
    h('div', { class: 'row', style: 'margin-top:12px' }, h('button', { class: 'btn primary', onclick: guard(async () => {
      S.racks = await api('PUT', `/api/projects/${pid()}/racks`, rows); await refreshAll(); toast('Racks guardados'); S.tab = 'devices'; render();
    }) }, 'Guardar racks y continuar'))));
  draw();
}

/* ---- B · equipos en rack */
function tabDevices(box) {
  const devs = S.devices.filter(d => d.dtype !== 'AP');
  if (!S.racks.length) return box.append(h('div', { class: 'card empty' }, 'Primero define los racks en la pestaña A.'));
  if (!devs.length) return box.append(h('div', { class: 'card empty' }, 'No hay equipos que no sean APs en el inventario. Los APs se ubican en la pestaña C.'));
  const edits = {}; const body = h('tbody'); const warn = h('div');
  const rackSel = d => h('select', { onchange: e => { edits[d.id].rack_id = +e.target.value || null; } }, h('option', { value: '' }, '—'),
    S.racks.map(r => h('option', { value: r.id, selected: d.rack_id === r.id }, `${r.code} (${r.units}U)`)));
  devs.forEach(d => {
    edits[d.id] = { id: d.id, rack_id: d.rack_id, rack_u: d.rack_u, rack_h: d.rack_h || 1, role: d.role, seq: d.seq };
    body.append(h('tr', {}, h('td', {}, h('span', { class: 'mono' }, d.box_code || '—')), h('td', {}, d.dtype), h('td', { class: 'mono' }, d.model),
      h('td', {}, d.dtype === 'SW' ? h('select', { onchange: e => edits[d.id].role = e.target.value }, [['', 'por defecto (AS)'], ['AS', 'AS · access'], ['CS', 'CS · core'], ['DS', 'DS · distribution']].map(([v, t]) => h('option', { value: v, selected: (d.role || '') === v }, t))) : ''),
      h('td', { style: 'width:90px' }, h('input', { type: 'number', min: 1, value: d.seq ?? '', placeholder: 'auto', title: 'Por defecto, el número de la caja', onchange: e => edits[d.id].seq = e.target.value })),
      h('td', { style: 'width:150px' }, rackSel(d)),
      h('td', { style: 'width:80px' }, h('input', { type: 'number', min: 1, value: d.rack_u ?? '', placeholder: 'U', onchange: e => edits[d.id].rack_u = e.target.value })),
      h('td', { style: 'width:70px' }, h('input', { type: 'number', min: 1, value: d.rack_h || 1, onchange: e => edits[d.id].rack_h = +e.target.value || 1 })),
      h('td', {}, nameCell(d))));
  });
  const def = h('select', {}, h('option', { value: '' }, 'Rack por defecto…'), S.racks.map(r => h('option', { value: r.id }, r.code)));
  box.append(h('div', { class: 'card' }, h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, 'Rack y posición de cada equipo'), h('span', { class: 'grow' }), def,
    h('button', { class: 'btn', onclick: () => { if (!def.value) return; devs.forEach(d => { if (!edits[d.id].rack_id) edits[d.id].rack_id = +def.value; }); save(false); } }, 'Aplicar a los que no tienen rack')),
    h('p', { class: 'hint' }, 'U = posición en el rack (U1 es la de arriba). «Alto» son las U que ocupa el equipo (1 para la mayoría de switches y firewalls).'),
    h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Caja', 'Tipo', 'Modelo', 'Rol', 'Nº en nombre', 'Rack', 'U', 'Alto', 'Nombre resultante'].map(x => h('th', {}, x)))), body)),
    h('div', { class: 'row', style: 'margin-top:12px' }, h('button', { class: 'btn primary', onclick: () => save(true) }, 'Guardar ubicaciones')), warn));
  async function save() {
    await guard(async () => {
      const r = await api('PUT', `/api/projects/${pid()}/placements`, Object.values(edits));
      await refreshAll(); warn.innerHTML = '';
      if (r.warnings.length) warn.append(h('div', { class: 'card', style: 'background:var(--warn-bg);margin-top:12px' }, r.warnings.map(w => h('div', {}, '⚠ ' + w))));
      else toast('Ubicaciones guardadas');
      renderElevations(elev); 
      body.querySelectorAll('tr').forEach((tr, i) => { const d = S.devices.find(x => x.id === devs[i].id); tr.lastChild.innerHTML = ''; tr.lastChild.append(nameCell(d)); });
    })();
  }
  const elev = h('div', { class: 'card' }, h('h2', {}, 'Vista de racks')); box.append(elev); renderElevations(elev);
}
function renderElevations(host) {
  host.querySelectorAll('.rackview').forEach(x => x.remove());
  const wrap = h('div', { class: 'rackview' });
  S.racks.forEach(r => {
    const U = 11, W = 230, H = r.units * U + 30;
    const devs = S.devices.filter(d => d.rack_id === r.id && d.rack_u);
    let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><text x="${W / 2}" y="16" text-anchor="middle" font-size="13" font-weight="700" fill="#0b3a63">${r.code}${r.location ? ' · ' + r.location : ''}</text>`;
    for (let u = 1; u <= r.units; u++) { const y = 24 + (u - 1) * U; svg += `<rect x="26" y="${y}" width="${W - 34}" height="${U}" fill="${u % 2 ? '#fbf7ee' : '#f4f0e8'}" stroke="#dfe6ee" stroke-width=".5"/>`; if (u % 5 === 0 || u === 1) svg += `<text x="22" y="${y + 9}" text-anchor="end" font-size="8" fill="#5f7186">${u}</text>`; }
    devs.forEach(d => { const y = 24 + (d.rack_u - 1) * U, hh = (d.rack_h || 1) * U; const nm = d.name.replace(/^[A-Z]+-[A-Z0-9]+-/, '');
      svg += `<rect x="28" y="${y + .5}" width="${W - 38}" height="${hh - 1}" rx="2" fill="#cfe3f7" stroke="#1a6fb8"/><text x="34" y="${y + hh / 2 + 3}" font-size="8.5" fill="#0b3a63">${d.box_code || ''} ${nm}</text>`; });
    wrap.append(h('div', { html: svg + '</svg>' }));
  });
  host.append(wrap);
}

/* ---- C · AP plan */
async function loadPlan() { S.plan = await api('GET', `/api/projects/${pid()}/applan`); return S.plan; }
async function tabApPlan(box) {
  const apDevs = S.devices.filter(d => d.dtype === 'AP');
  if (!S.plan) await loadPlan();
  const pdfIn = h('input', { type: 'file', accept: 'application/pdf', style: 'display:none', onchange: guard(async e => {
    box.innerHTML = ''; box.append(h('div', { class: 'card' }, h('span', { class: 'spinner' }), 'Analizando el plano…'));
    const fd = new FormData(); fd.append('file', e.target.files[0]); S.plan = await api('POST', `/api/projects/${pid()}/applan`, fd); S.planPage = 0; S.selMarker = null; box.innerHTML = ''; await tabApPlan(box);
  }) });
  box.innerHTML = '';
  if (!apDevs.length) box.append(h('div', { class: 'card', style: 'background:var(--warn-bg)' }, 'Aún no hay APs en el inventario. Puedes subir el plano igualmente, pero la asignación necesita los APs con su código de caja (AP01, AP02…).'));
  if (!S.plan) {
    box.append(h('div', { class: 'card' }, h('h2', {}, 'AP Plan'), h('div', { class: 'dz', onclick: () => pdfIn.click() }, h('b', {}, 'Sube el AP Plan en PDF'), h('br'), 'Cada tipo de AP con un color distinto. Si el PDF viene de CAD/Visio (vectorial) la detección es exacta; también funciona con PDFs escaneados.'), pdfIn));
    return;
  }
  const P = S.plan, pl = P.plan;
  box.append(h('div', { class: 'card' }, h('div', { class: 'row' }, h('div', {}, h('b', {}, pl.filename), h('div', { class: 'hint' }, `${pl.pages} página(s) · detección ${pl.mode === 'vector' ? 'vectorial' : 'por imagen'} · ${P.markers.filter(m => !m.ignored).length} marcadores`)), h('span', { class: 'grow' }),
    h('button', { class: 'btn', onclick: () => pdfIn.click() }, 'Cambiar PDF'), pdfIn)));

  // 1) plantas por página
  box.append(h('div', { class: 'card' }, h('h2', {}, '1 · Planta de cada página'), h('div', { class: 'row' }, Object.keys(pl.page_floors).map(i => {
    const det = P.markers.filter(m => m.page === +i && !m.ignored && !m.legendless).length, ex = P.expected_per_page[i];
    return h('label', { style: 'margin-right:10px' }, `Pág. ${+i + 1} → planta `, h('input', { value: pl.page_floors[i], style: 'width:60px', onchange: guard(async e => { pl.page_floors[i] = e.target.value; S.plan = await api('PUT', `/api/applan/${pl.id}`, { page_floors: pl.page_floors }); }) }),
      ex != null ? h('small', { class: det === ex ? 'ok-t' : 'warn-t', title: 'El plano indica cuántos APs se pidieron en esta planta' }, ` plano: ${ex} · detectados: ${det}`) : null); })),
    h('p', { class: 'hint', style: 'margin:8px 0 0' }, 'Cuando el plano trae «N AP requested» lo uso como control. Los marcadores del color de «no es un AP» cuentan igual aquí hasta que los ignores.')));

  // 2) colores -> modelo
  const cmap = { ...pl.color_map }; const tb = h('tbody');
  P.colors.forEach(g => {
    const exp = P.models.find(x => x.model === g.model); const diff = exp ? g.count - exp.devices : null;
    tb.append(h('tr', {}, h('td', {}, h('span', { class: 'swatch', style: `background:${g.color}` }), h('span', { class: 'mono' }, g.color)), h('td', {}, g.count + (g.ignored ? ` (+${g.ignored} ignorados)` : '')),
      h('td', {}, h('select', { onchange: e => { cmap[g.color] = e.target.value; } }, h('option', { value: '' }, '— No es un AP (ignorar) —'), P.models.map(x => h('option', { value: x.model, selected: x.model === g.model }, `${x.model}  (${x.devices} en plano${x.spares ? ' + ' + x.spares + ' spare' : ''})`)))),
      h('td', {}, exp ? (diff === 0 ? h('span', { class: 'ok-t' }, '✓ coincide') : h('span', { class: 'bad-t' }, `${diff > 0 ? '+' : ''}${diff} vs inventario`)) : h('span', { class: 'hint' }, g.model ? '' : '—'))));
  });
  const rep = h('div');
  box.append(h('div', { class: 'card' }, h('h2', {}, '2 · Color → modelo de AP'),
    h('p', { class: 'hint' }, 'Si las cantidades coinciden de forma única, ya te lo he propuesto. Si un color suma de más, suele ser la leyenda del plano: pulsa ese marcador en el visor y márcalo como ignorado.'),
    h('table', {}, h('thead', {}, h('tr', {}, ['Color', 'Marcadores', 'Modelo', 'Control'].map(x => h('th', {}, x)))), tb),
    h('div', { class: 'row', style: 'margin-top:12px' }, h('button', { class: 'btn primary', onclick: guard(async () => {
      S.plan = await api('PUT', `/api/applan/${pl.id}`, { color_map: cmap });
      const r = await api('POST', `/api/applan/${pl.id}/assign`); S.plan = r.state; await refreshAll();
      toast('Números asignados'); box.innerHTML = ''; await tabApPlan(box);
    }) }, '3 · Asignar números de AP a las posiciones'), h('span', { class: 'hint' }, 'Orden: por planta, de arriba abajo y de izquierda a derecha. Se respeta lo que hayas fijado a mano.')), rep));

  // 3) visor
  const pages = h('div', { class: 'row', style: 'margin-bottom:8px' }, Array.from({ length: pl.pages }, (_, i) => h('button', { class: 'btn sm' + (i === S.planPage ? ' primary' : ''), onclick: () => { S.planPage = i; box.innerHTML = ''; tabApPlan(box); } }, `Planta ${pl.page_floors[i]}`)));
  const dim = P.dims[S.planPage]; const view = h('div', { class: 'viewer', style: addMode ? 'cursor:crosshair' : '' }, h('img', { src: `/api/applan/${pl.id}/page/${S.planPage}.png`, alt: 'plano',
    onclick: guard(async e => { if (!addMode) return; const r = e.target.getBoundingClientRect();
      S.plan = await api('POST', `/api/applan/${pl.id}/markers`, { page: S.planPage, color: addMode, x: (e.clientX - r.left) / r.width * dim.w, y: (e.clientY - r.top) / r.height * dim.h });
      box.innerHTML = ''; await tabApPlan(box); }) }));
  P.markers.filter(m => m.page === S.planPage).forEach(m => {
    const w = Math.max(1.2, m.size / dim.w * 100 * 1.5);
    const el = h('div', { class: 'mk' + (m.ignored ? ' ig' : !m.device_id && !m.ignored && pl.color_map[m.color] ? ' un' : '') + (S.selMarker === m.id ? ' sel' : ''),
      style: `left:${m.x / dim.w * 100}%;top:${m.y / dim.h * 100}%;width:${w}%;border-color:${m.color};color:${m.color}`, title: m.room ? 'Hab. ' + m.room : '',
      onclick: () => { S.selMarker = m.id; box.innerHTML = ''; tabApPlan(box); } }, m.box_code ? h('span', { class: 'lb' }, m.box_code) : null);
    view.append(el);
  });
  const sel = P.markers.find(m => m.id === S.selMarker);
  const side = h('div', { class: 'card', style: 'margin:0' }, h('h2', {}, 'Marcador'));
  if (!sel) side.append(h('div', { class: 'hint' }, 'Pulsa un marcador del plano para cambiar su número, su habitación o ignorarlo.'));
  else {
    const mod = pl.color_map[sel.color]; const opts = P.devices.filter(d => !mod || d.model === mod).sort((a, b) => a.box_code.localeCompare(b.box_code, undefined, { numeric: true }));
    side.append(h('div', {}, h('span', { class: 'swatch', style: `background:${sel.color}` }), mod || 'Sin modelo asignado'),
      h('label', { class: 'hint' }, 'Número de AP (caja)'), h('select', { style: 'width:100%', onchange: guard(async e => { S.plan = await api('PATCH', `/api/ap-markers/${sel.id}`, { device_id: +e.target.value || null }); await refreshAll(); box.innerHTML = ''; tabApPlan(box); }) },
        h('option', { value: '' }, '— sin asignar —'), opts.map(d => h('option', { value: d.id, selected: d.id === sel.device_id }, d.box_code))),
      h('label', { class: 'hint' }, 'Habitación / zona'), h('input', { value: sel.room, style: 'width:100%', placeholder: '202', onchange: guard(async e => { S.plan = await api('PATCH', `/api/ap-markers/${sel.id}`, { room: e.target.value }); await refreshAll(); }) }),
      h('button', { class: 'btn sm danger', style: 'margin-top:10px', onclick: guard(async () => { S.plan = await api('DELETE', `/api/ap-markers/${sel.id}`); S.selMarker = null; box.innerHTML = ''; tabApPlan(box); }) }, 'Eliminar marcador'),
      h('label', { style: 'display:block;margin-top:10px' }, h('input', { type: 'checkbox', checked: sel.ignored, onchange: guard(async e => { S.plan = await api('PATCH', `/api/ap-markers/${sel.id}`, { ignored: e.target.checked }); box.innerHTML = ''; tabApPlan(box); }) }), ' Ignorar (leyenda u otro símbolo)'));
  }
  const addBar = h('div', { class: 'row', style: 'margin-bottom:8px' }, h('span', { class: 'hint' }, '¿Falta un AP (tapado por otro símbolo)?'),
    Object.keys(pl.color_map).map(c => h('button', { class: 'btn sm' + (addMode === c ? ' primary' : ''), onclick: () => { addMode = addMode === c ? null : c; box.innerHTML = ''; tabApPlan(box); } },
      h('span', { class: 'swatch', style: `background:${c}` }), '+ ' + pl.color_map[c])), addMode ? h('span', { class: 'hint' }, 'Pulsa en el plano donde está el AP') : null);
  box.append(h('div', { class: 'card' }, h('h2', {}, 'Plano'), pages, addBar, h('div', { class: 'plan-wrap' }, view, side),
    h('div', { class: 'legendbar' }, h('span', {}, '● círculo con borde rojo = posición sin AP asignado'), h('span', {}, '● punteado = ignorado')) ));

  // 3b) tabla rápida de habitaciones
  const rows = P.markers.filter(m => !m.ignored && pl.color_map[m.color]).sort((a, b) => (+pl.page_floors[a.page] - +pl.page_floors[b.page]) || (a.page - b.page) || (a.y - b.y) || (a.x - b.x));
  const rtb = h('tbody', {}, rows.map(m => h('tr', {}, h('td', {}, h('span', { class: 'mono' }, m.box_code || '— sin asignar —')), h('td', {}, 'Planta ' + pl.page_floors[m.page]),
    h('td', { class: 'hint' }, (pl.color_map[m.color] || '').replace(/-HILU$/, '')),
    h('td', { style: 'width:140px' }, h('input', { value: m.room, placeholder: 'hab.', onchange: guard(async e => { m.room = e.target.value; await api('PATCH', `/api/ap-markers/${m.id}`, { room: e.target.value }); await refreshAll(); }) })),
    h('td', {}, h('a', { href: '#', class: 'hint', onclick: e => { e.preventDefault(); S.planPage = m.page; S.selMarker = m.id; box.innerHTML = ''; tabApPlan(box); window.scrollTo({ top: 0, behavior: 'smooth' }); } }, 'ver en plano')))));
  box.append(h('div', { class: 'card' }, h('h2', {}, 'Habitación de cada AP'),
    h('p', { class: 'hint' }, 'El número de habitación va en el nombre del AP. Si el plano no lo trae legible, rellénalo aquí (Tab para pasar al siguiente).'),
    h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['AP', 'Planta', 'Modelo', 'Habitación', ''].map(x => h('th', {}, x)))), rtb))));

  // 4) entregable
  const assigned = P.markers.filter(m => !m.ignored && m.device_id).length, total = P.markers.filter(m => !m.ignored && pl.color_map[m.color]).length;
  box.append(h('div', { class: 'card tint' }, h('div', { class: 'row' }, h('div', {}, h('h2', { style: 'margin:0' }, '4 · Plano para el técnico'),
    h('div', { class: 'hint' }, `${assigned} de ${total} posiciones con número. El PDF lleva el AP01, AP02… junto a cada punto y una hoja final con la lista (caja → nombre → planta → habitación).`)), h('span', { class: 'grow' }),
    h('button', { class: 'btn primary', onclick: () => download(`/api/applan/${pl.id}/annotated.pdf`) }, '⬇ Descargar AP Plan numerado (PDF)'),
    h('button', { class: 'btn', onclick: () => download(`/api/projects/${pid()}/export.xlsx`) }, '⬇ Inventario con nombres (Excel)'))));
}

boot().catch(e => { document.body.append(h('pre', {}, 'Error: ' + e.message)); });


/* ---------------------------------------------------------------- 3 · INSTALACIÓN */
const isReal = d => !d.spare && d.dtype === 'AP';
function progress(devs) { const t = devs.length, i = devs.filter(d => d.installed).length; return { t, i, pct: t ? Math.round(i / t * 100) : 0 }; }
function progressBar(label, pr) {
  return h('div', { class: 'prog' }, h('div', { class: 'row' }, h('b', {}, label), h('span', { class: 'grow' }), h('span', {}, `${pr.i} / ${pr.t} · ${pr.pct}%`)),
    h('div', { class: 'bar' }, h('div', { style: `width:${pr.pct}%` })));
}
async function loadPortmap() { S.portmap = await api('GET', `/api/projects/${pid()}/portmap`); return S.portmap; }
async function reloadInstall(box) { await refreshAll(); await loadPortmap(); render(); }
function viewInstall(m) {
  m.append(...header('3 · Instalación', 'El técnico marca cada equipo cuando lo instala y puede subir la foto. Los puertos de switch se asignan solos (editables).'));
  const aps = S.devices.filter(isReal), others = S.devices.filter(d => d.dtype !== 'AP');
  m.append(h('div', { class: 'card' }, progressBar('APs instalados', progress(aps)), progressBar('Switches y firewall instalados', progress(others))));
  m.append(h('div', { class: 'tabs' }, [['aps', 'A · APs'], ['ports', 'B · Switches y puertos'], ['rack', 'C · Switches y firewall']].map(([k, t]) =>
    h('button', { class: S.itab === k ? 'on' : '', onclick: () => { S.itab = k; render(); } }, t))));
  const box = h('div'); m.append(box);
  ({ aps: instAps, ports: instPorts, rack: instRack })[S.itab](box);
}

/* panel de detalle de un equipo: instalado + foto + nota + conexión a switch */
function devPanel(d, redraw) {
  const save = async patch => { Object.assign(d, await api('PATCH', `/api/devices/${d.id}`, patch)); await loadPortmap(); redraw(); };
  const fileIn = h('input', { type: 'file', accept: 'image/*', capture: 'environment', style: 'display:none', onchange: guard(async e => {
    if (!e.target.files[0]) return; const fd = new FormData(); fd.append('file', e.target.files[0]);
    Object.assign(d, await api('POST', `/api/devices/${d.id}/photo`, fd)); toast('Foto guardada'); redraw(); }) });
  const p = h('div', { class: 'card devpanel' + (d.installed ? ' done' : '') },
    h('h2', {}, d.box_code || d.model, d.spare ? h('span', { class: 'badge b-added', style: 'margin-left:8px' }, 'SPARE') : null),
    h('div', { class: 'mono', style: 'word-break:break-all' }, d.name), h('div', { class: 'hint' }, `${d.model}${d.floor ? ' · planta ' + d.floor : ''}${d.room ? ' · hab. ' + d.room : ''}${d.rack_code ? ' · ' + d.rack_code : ''}`),
    d.dtype === 'AP' ? h('div', { class: 'conn' }, d.sw_code ? [h('b', {}, `${d.sw_code}`), ` · puerto `, h('b', {}, String(d.sw_port))] : h('span', { class: d.spare ? 'hint' : 'warn-t' }, d.spare ? 'Spare: sin puerto' : 'Sin puerto asignado')) : null,
    h('button', { class: 'btn big ' + (d.installed ? 'okbtn' : 'primary'), onclick: guard(() => save({ installed: !d.installed })) }, d.installed ? '✓ Instalado — pulsa para deshacer' : 'Marcar como instalado'),
    d.installed_at ? h('div', { class: 'hint' }, 'Instalado: ' + new Date(d.installed_at + 'Z').toLocaleString('es-ES') + (d.installed_by ? ' · ' + d.installed_by : '')) : null,
    d.has_photo ? h('a', { href: `/api/devices/${d.id}/photo?v=${d.photo_v}`, target: '_blank' }, h('img', { class: 'thumb', src: `/api/devices/${d.id}/photo?w=500&v=${d.photo_v}` })) : null,
    h('div', { class: 'row' }, h('button', { class: 'btn', onclick: () => fileIn.click() }, d.has_photo ? 'Cambiar foto' : '📷 Añadir foto'), fileIn,
      d.has_photo ? h('button', { class: 'btn danger sm', onclick: guard(async () => { await api('DELETE', `/api/devices/${d.id}/photo`); d.has_photo = false; redraw(); }) }, 'Quitar foto') : null),
    h('label', { class: 'hint' }, 'Nota'), h('input', { value: d.install_note || '', placeholder: 'p. ej. Falta tornillería', style: 'width:100%', onchange: guard(e => save({ install_note: e.target.value })) }));
  if (d.dtype === 'AP' && !d.spare && S.portmap) {
    const sws = S.portmap.switches, sel = h('select', {}, h('option', { value: '' }, '— sin switch —'), sws.map(s => h('option', { value: s.id, selected: s.id === d.sw_id }, `${s.box_code} · ${s.model.replace(/-M-HILU/, '')}`)));
    const port = h('input', { type: 'number', min: 1, value: d.sw_port || '', style: 'width:70px' });
    p.append(h('details', { style: 'margin-top:10px' }, h('summary', { class: 'hint' }, 'Cambiar switch / puerto'), h('div', { class: 'row', style: 'margin-top:6px' }, sel, port,
      h('button', { class: 'btn sm', onclick: guard(() => save({ sw_id: sel.value || null, sw_port: port.value || null })) }, 'Guardar')),
      d.port_manual ? h('div', { class: 'hint' }, 'Fijado a mano. «Recalcular puertos» no lo toca.') : null));
  }
  return p;
}

/* A · APs: plano con marcadores que se ponen verdes + lista */
async function instAps(box) {
  if (!S.plan) await loadPlan(); if (!S.portmap) await loadPortmap();
  const byId = Object.fromEntries(S.devices.map(d => [d.id, d])); box.innerHTML = '';
  const redraw = () => { box.innerHTML = ''; instAps(box); };
  const sel = byId[S.instSel]; const side = h('div', {});
  side.append(sel ? devPanel(sel, redraw) : h('div', { class: 'card hint' }, 'Pulsa un AP del plano o de la lista para marcarlo como instalado y añadir su foto.'));
  const main = h('div');
  const P = S.plan;
  if (P) {
    const pl = P.plan, dim = P.dims[S.instPage] || P.dims[0];
    const floorDevs = i => P.markers.filter(m => m.page === i && !m.ignored && m.device_id && byId[m.device_id]).map(m => byId[m.device_id]);
    main.append(h('div', { class: 'row', style: 'margin-bottom:8px' }, Array.from({ length: pl.pages }, (_, i) => { const pr = progress(floorDevs(i));
      return pr.t ? h('button', { class: 'btn sm' + (i === S.instPage ? ' primary' : ''), onclick: () => { S.instPage = i; redraw(); } }, `Planta ${pl.page_floors[i]} · ${pr.i}/${pr.t}`) : null; })));
    const view = h('div', { class: 'viewer' }, h('img', { src: `/api/applan/${pl.id}/page/${S.instPage}.png`, alt: 'plano' }));
    P.markers.filter(m => m.page === S.instPage && !m.ignored).forEach(m => {
      const d = byId[m.device_id]; const w = Math.max(1.2, m.size / dim.w * 100 * 1.5);
      view.append(h('div', { class: 'mk' + (d && d.installed ? ' inst' : '') + (d ? '' : ' un') + (d && d.id === S.instSel ? ' sel' : ''),
        style: `left:${m.x / dim.w * 100}%;top:${m.y / dim.h * 100}%;width:${w}%;border-color:${m.color};color:${m.color}`, title: d ? d.name : 'Sin número asignado',
        onclick: () => { if (d) { S.instSel = d.id; redraw(); } } }, d ? h('span', { class: 'lb' }, d.box_code) : null));
    });
    main.append(view);
  } else main.append(h('div', { class: 'card hint' }, 'Aún no has subido el AP Plan (módulo 2 · C). Mientras tanto puedes marcar los APs desde la lista.'));
  box.append(h('div', { class: 'grid2' }, main, side));
  // lista
  const list = h('div', { class: 'aplist' });
  S.devices.filter(d => d.dtype === 'AP').forEach(d => list.append(h('button', { class: 'apchip' + (d.installed ? ' inst' : '') + (d.spare ? ' spare' : '') + (d.id === S.instSel ? ' sel' : ''), title: d.name,
    onclick: () => { S.instSel = d.id; redraw(); } }, h('b', {}, d.box_code || '—'), h('small', {}, d.spare ? 'spare' : d.sw_code ? `${d.sw_code}·${d.sw_port}` : (d.room ? 'hab. ' + d.room : '')), d.has_photo ? ' 📷' : '')));
  box.append(h('div', { class: 'card' }, h('h2', {}, 'Lista de APs'), h('div', { class: 'hint', style: 'margin-bottom:8px' }, 'Verde = instalado. 📷 = tiene foto.'), list));
}

/* B · Switches: puertos gráficos */
async function instPorts(box) {
  box.innerHTML = ''; if (!S.portmap) await loadPortmap();
  const byId = Object.fromEntries(S.devices.map(d => [d.id, d]));
  const redraw = () => { box.innerHTML = ''; instPorts(box); };
  const recalc = h('button', { class: 'btn', onclick: guard(async () => { const r = await api('POST', `/api/projects/${pid()}/site-plan`); await reloadInstall(); if (r.warnings.length) toast(r.warnings[0], true); else toast('Puertos recalculados'); }) }, 'Recalcular puertos');
  box.append(h('div', { class: 'card' }, h('div', { class: 'row' }, h('div', {}, h('b', {}, 'Port Map'), h('div', { class: 'hint' }, 'SW01 core sin APs · SW02 (24 p.) APs de pasillo en orden · SW03 (48 p.) APs de habitaciones en orden · SW04 sin conexiones. Los puertos que cambies a mano se respetan.')),
    h('span', { class: 'grow' }), recalc, h('button', { class: 'btn primary', onclick: () => download(`/api/projects/${pid()}/portmap.xlsx`) }, 'Descargar Port Map (xlsx)'))));
  const sel = byId[S.instSel]; if (sel) box.append(devPanel(sel, redraw));
  S.portmap.switches.forEach(sw => {
    const half = Math.ceil(sw.n_ports / 2), grid = h('div', { class: 'ports', style: `grid-template-columns:repeat(${half},minmax(34px,1fr))` });
    const cell = pt => { const used = pt.device_id; return h('button', { class: 'port' + (used ? (pt.installed ? ' inst' : ' used') : ''), title: used ? `${pt.name}${pt.room ? ' · hab. ' + pt.room : ''}` : 'Libre',
      onclick: () => { if (used) { S.instSel = pt.device_id; redraw(); } } }, h('small', {}, pt.port), h('b', {}, used ? pt.box_code : '')); };
    const top = sw.ports.filter(p => p.port % 2 === 1), bot = sw.ports.filter(p => p.port % 2 === 0);
    top.forEach((pt, i) => { grid.append(cell(pt)); }); // fila 1 (impares) …
    const g2 = h('div', { class: 'ports', style: `grid-template-columns:repeat(${half},minmax(34px,1fr))` }); bot.forEach(pt => g2.append(cell(pt)));
    const used = sw.ports.filter(p => p.device_id), done = used.filter(p => p.installed).length;
    box.append(h('div', { class: 'card swcard' }, h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `${sw.box_code} · ${sw.model.replace(/-M-HILU/, '')}`), h('span', { class: 'badge' }, sw.rack || 'sin rack'), h('span', { class: 'grow' }),
      h('span', { class: 'hint' }, `${used.length} puertos con AP · ${done} instalados · ${sw.n_ports} puertos`)), h('div', { class: 'mono hint' }, sw.name),
      used.length || sw.feeds ? h('div', { class: 'swbody' }, grid, g2) : h('div', { class: 'hint', style: 'margin:10px 0' }, 'Sin APs conectados por ahora.')));
  });
  const un = S.portmap.unassigned.map(id => byId[id]).filter(Boolean);
  if (un.length) box.append(h('div', { class: 'card', style: 'background:var(--warn-bg)' }, h('b', {}, `${un.length} AP(s) sin puerto: `), un.map(d => d.box_code || d.model).join(', '), h('div', { class: 'hint' }, 'Normalmente es que faltan códigos de caja o el switch. Pulsa «Recalcular puertos» tras numerar.')));
}

/* C · switches y firewall */
function instRack(box) {
  box.innerHTML = ''; const redraw = () => { box.innerHTML = ''; instRack(box); };
  const list = S.devices.filter(d => d.dtype !== 'AP'); const sel = list.find(d => d.id === S.instSel);
  const l = h('div', { class: 'aplist' }); list.forEach(d => l.append(h('button', { class: 'apchip wide' + (d.installed ? ' inst' : '') + (d.id === S.instSel ? ' sel' : ''), onclick: () => { S.instSel = d.id; redraw(); } },
    h('b', {}, d.box_code || d.model), h('small', {}, `${d.model.replace(/-HILU|-M-/g, '')} · ${d.rack_code || 'sin rack'}`), d.has_photo ? ' 📷' : '')));
  box.append(h('div', { class: 'grid2' }, h('div', { class: 'card' }, h('h2', {}, 'Switches y firewall'), l), sel ? devPanel(sel, redraw) : h('div', { class: 'card hint' }, 'Elige un equipo para marcarlo como instalado y añadir su foto.')));
}
