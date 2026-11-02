import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const $ = (id) => document.getElementById(id);

// In the Chromebook file (window.I3D, see chromebook.py) this page runs from file://: the models come from the
// Cloudflare site, and everything else goes to the PC through its Cloudflare tunnel, with the passcode.
const SITE = window.I3D ? window.I3D.site : null;
const OFF = "Your PC isn't on, or Image to 3D isn't open on it. Your models still work: look at them and download them.";
let base = SITE ? null : '';  // where the PC's app answers: '' is this page's own server, null is "not found yet"
let passcode = '';
if (SITE) {
  document.body.classList.add('file-mode');
  try { passcode = localStorage.getItem('image-to-3d-passcode') || ''; } catch {}
}
function call(path, opts = {}) {
  if (base === null) return Promise.reject(new Error(OFF));
  const headers = { 'X-Image-To-3D': '1', ...(passcode ? { 'X-Key': passcode } : {}), ...(opts.headers || {}) };
  return fetch(base + path, { ...opts, headers }).then((r) => {
    if (r.status === 401 && SITE) askPasscode(passcode ? 'That passcode is wrong. Check it on your PC.' : '');
    return r;
  }, () => { throw new Error(SITE ? OFF : "Can't reach the app. Start it again with Start Image to 3D.bat"); });
}
const api = (path, opts = {}) =>
  call(path, opts).then(async (r) => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
    return data;
  });
// A URL an <img> or the 3D loader can use for a file on the PC. With a passcode (which an <img> can't send) the
// file is downloaded first.
async function pcFile(path) {
  if (!SITE) return path;
  const r = await call(path);
  if (!r.ok) throw new Error(`Error ${r.status}`);
  return URL.createObjectURL(await r.blob());
}
// r: a model, from the gallery or a finished job. r.online: it came from the Cloudflare site.
function modelFile(r, file, fresh = false) {
  const name = encodeURIComponent(r.folder);
  if (r.online) return Promise.resolve(`${SITE}/m/${name}/${file === 'cutout.png' ? 'thumb.webp' : file}`);
  return pcFile(`/files/${name}/${file}${fresh ? `?t=${Date.now()}` : ''}`);
}
async function saveFile(url, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await (await fetch(url)).blob());
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// ---------- 3D viewer ----------
const box = $('viewer');
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
box.prepend(renderer.domElement);
const scene = new THREE.Scene();
scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 0.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(2, 3, 2);
scene.add(sun);
const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
camera.position.set(0, 0.7, 2.4);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 1.5;
controls.target.set(0, 0.45, 0);
renderer.domElement.addEventListener('pointerdown', () => { controls.autoRotate = false; });
window.viewer = { camera, controls };  // handy for checking models from the browser console
let shown = null;

function resize() {
  const w = box.clientWidth, h = box.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(box);
renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });

// keepView: swap the model but leave the camera where it is (for comparing versions from the same angle)
async function show(r, file = 'model.glb', keepView = false) {
  let url;
  try {
    url = await modelFile(r, file, true);
  } catch (e) {
    return showError(e.message);
  }
  new GLTFLoader().load(url, (gltf) => {
    if (shown) scene.remove(shown);
    shown = gltf.scene;
    // fit whatever size the model is into view, sitting on the ground
    const b = new THREE.Box3().setFromObject(shown), size = b.getSize(new THREE.Vector3()), c = b.getCenter(new THREE.Vector3());
    const s = 1 / Math.max(size.x, size.y, size.z, 1e-6);
    shown.scale.setScalar(s);
    shown.position.set(-c.x * s, -b.min.y * s, -c.z * s);
    scene.add(shown);
    $('viewerHint').hidden = true;
    if (keepView) return;
    // a 3/4 view from a little above, just far enough to fit the whole model
    const r = size.length() * s / 2, dist = r / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 0.85;
    controls.target.set(0, size.y * s / 2, 0);
    camera.position.set(dist * 0.55, size.y * s / 2 + dist * 0.35, dist * 0.76);
    controls.autoRotate = true;
  }, undefined, () => showError('Could not open that model.'));
  const cut = $('cutout');
  cut.onerror = () => { cut.hidden = true; };
  modelFile(r, 'cutout.png').then((u) => { cut.src = u; cut.hidden = false; }, () => { cut.hidden = true; });
}

// ---------- picking a picture ----------
// Right after a picture is picked, the server cuts the object out (about 10 s) and the page shows exactly what the
// AI will get, so a bad cut-out (say, tire bottoms hidden in grass) is caught before a 5-minute run.
let picked = null, pickedUrl = null, pickedCredit = '', cutout = null, cutting = 0;
// credit: where a web picture came from and its license; it goes into the model's license.txt
function pick(file, credit = '') {
  if (!file || !file.type.startsWith('image/')) return showError("That file isn't a picture. Use a PNG, JPG or WEBP image.");
  picked = file;
  pickedUrl = URL.createObjectURL(file);
  pickedCredit = credit;
  $('picked').hidden = false;
  $('dropText').hidden = true;
  $('name').value = (file.name || 'pasted').replace(/\.[^.]+$/, '').replace(/[^a-z0-9_-]+/gi, '-').toLowerCase().slice(0, 60);
  $('error').hidden = true;
  $('pictureTools').hidden = false;
  $('fixPicture').hidden = $('cleanPicture').hidden = !canDraw();
  beforeClean = null;
  $('cleanPicture').textContent = 'Clean up with AI';
  $('creditText').hidden = !credit;
  $('creditText').textContent = credit ? 'Picture: ' + credit : '';
  cutout = null;
  refreshCutout();
}

async function refreshCutout() {
  const hint = $('cutHint');
  if (!picked || !$('removeBg').checked) {
    $('picked').src = pickedUrl || '';
    hint.hidden = !picked;
    hint.textContent = 'Using the picture as it is. It should already have a plain or see-through background.';
    return updateButton();
  }
  if (cutout && cutout.file === picked && cutout.failed) {
    $('picked').src = pickedUrl;
    hint.hidden = false;
    hint.textContent = "Couldn't cut it out ahead of time. The background will be removed during the run instead.";
    return updateButton();
  }
  if (cutout && cutout.file === picked) {
    $('picked').src = cutout.url;
    hint.hidden = false;
    hint.innerHTML = '<b>This is what the AI will see.</b> Check that nothing is missing, like wheel bottoms hidden ' +
      'in grass. Missing parts come out wrong, so if something is cut off, try a picture where the whole object shows.' +
      (cutout.upscaled ? ' The picture was small, so it was sharpened 4× first.' : '');
    return updateButton();
  }
  const file = picked, ticket = ++cutting;
  $('picked').src = pickedUrl;
  hint.hidden = false;
  hint.textContent = 'Cutting the object out of the picture… (about 10 seconds)';
  updateButton();
  try {
    const r = await api('/api/cutout', { method: 'POST', body: file, headers: { 'Content-Type': file.type } });
    const url = await pcFile(r.url);
    if (ticket !== cutting) return;  // a newer picture was picked meanwhile
    cutout = { file, id: r.id, url, upscaled: r.upscaled };
  } catch (e) {
    if (ticket !== cutting) return;
    cutout = { file, failed: true };
    showError(e.message);
  }
  cutting = ticket === cutting ? 0 : cutting;
  refreshCutout();
}
$('removeBg').addEventListener('change', refreshCutout);
$('file').addEventListener('change', (e) => pick(e.target.files[0]));
const drop = $('drop');
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  const file = [...e.dataTransfer.files].find((f) => f.type.startsWith('image/'));
  if (file) return pick(file);
  // a picture dragged from a web page often arrives as just its address
  const link = (e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain') || '').split('\n')[0].trim();
  if (link) pickFromWeb(link);
});
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') $('file').click(); });
window.addEventListener('paste', (e) => {
  if (e.target.closest && e.target.closest('input, textarea')) return;  // typing in a box: leave it alone
  const item = [...e.clipboardData.items].find((i) => i.type.startsWith('image/'));
  if (item) return pick(item.getAsFile());
  const text = e.clipboardData.getData('text/plain').trim();
  if (/^https?:\/\/\S+$/i.test(text)) pickFromWeb(text);
});

async function pickFromWeb(link, credit = '', name = '') {
  if (!/^https?:\/\//i.test(link)) return showError("That isn't a picture or a link to one.");
  $('error').hidden = true;
  $('dropText').hidden = false;
  $('dropText').textContent = 'Getting the picture…';
  try {
    const r = await call('/api/fetch', { method: 'POST', body: JSON.stringify({ url: link }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `Error ${r.status}`);
    const blob = await r.blob();
    const ext = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/bmp': 'bmp' }[blob.type] || 'png';
    const base = name || decodeURIComponent(new URL(link).pathname.split('/').pop() || 'web-picture').replace(/\.\w+$/, '');
    pick(new File([blob], `${slug(base)}.${ext}`, { type: blob.type }), credit || link);
  } catch (e) {
    if (!picked) $('dropText').textContent = 'Drop a picture here, click to pick one, or paste with Ctrl+V';
    else $('dropText').hidden = true;
    showError(e.message);
  }
}

// ---------- status ----------
let status = {};
let lastFind = 0;
async function findPc() {
  // the PC's tunnel address changes every time it starts, so ask the Cloudflare site where it is now
  if (Date.now() - lastFind < 12000) return;
  lastFind = Date.now();
  const r = await fetch(`${SITE}/pc`, { cache: 'no-store' }).then((x) => x.json()).catch(() => ({}));
  if (r.url && base === null) {
    base = r.url;
    loadGallery();
  }
}
async function poll() {
  if (SITE && base === null) await findPc();
  const pill = $('status');
  try {
    status = await api('/api/status');
    pill.className = 'pill' + (status.ready ? ' ok' : status.problems.length ? ' bad' : '');
    pill.textContent = status.ready ? `Ready · ${SITE ? "using your PC's " : ''}${status.device}` : status.problems[0] || 'Starting the AI…';
    for (const input of document.querySelectorAll('input[name=engine]')) {
      const ok = status.engines[input.value.split('-')[0]];
      input.disabled = ok === false;
    }
    if (SITE) $('passForm').hidden = true;
  } catch (e) {
    status = {};
    if (SITE && !$('passForm').hidden) {
      pill.textContent = 'Type the passcode from your PC to make models';
      pill.className = 'pill';
    } else {
      if (SITE) base = null;  // look for the PC again (it may have restarted at a new address)
      pill.textContent = SITE ? 'Your PC is off · your models still work' : e.message;
      pill.title = SITE ? OFF : '';
      pill.className = 'pill bad';
    }
  }
  $('tabWords').disabled = !canDraw();
  $('tabWords').title = canDraw() ? '' : status.engines ? "The picture AI isn't installed. Run Setup.bat." : OFF;
  updateButton();
}
function askPasscode(text) {
  $('passForm').hidden = false;
  $('passText').textContent = text || 'Your PC asks for its passcode (you only type it once on this computer). ' +
    'It\'s on the PC: Image to 3D → Take models to a Chromebook.';
}
$('passForm').addEventListener('submit', (e) => {
  e.preventDefault();
  passcode = $('passInput').value.trim();
  try { localStorage.setItem('image-to-3d-passcode', passcode); } catch {}
  $('passText').textContent = 'Checking…';
  poll().then(() => { if (!$('passForm').hidden) return; loadGallery(); });
});
setInterval(poll, 3000);
poll();

let busy = false;
function canDraw() { return !!(status.engines && status.engines.pictures); }
function updateButton() { $('go').disabled = !picked || busy || !status.ready || (cutting && $('removeBg').checked); }
function showError(msg) { $('error').textContent = msg; $('error').hidden = false; }

// ---------- making a model ----------
$('go').addEventListener('click', async () => {
  busy = true;
  updateButton();
  $('error').hidden = true;
  $('progress').hidden = false;
  setStep('Sending the picture', 0.01);
  const engine = document.querySelector('input[name=engine]:checked').value;
  const q = new URLSearchParams({
    name: $('name').value || 'model', engine, size: document.querySelector('input[name=size]:checked').value,
    remove_bg: $('removeBg').checked ? '1' : '0', filename: picked.name || 'pasted.png',
  });
  if ($('removeBg').checked && cutout && cutout.file === picked && cutout.id) q.set('cutout', cutout.id);
  if (pickedCredit) q.set('credit', pickedCredit);
  try {
    const { id } = await api('/api/jobs?' + q, { method: 'POST', body: picked, headers: { 'Content-Type': picked.type } });
    for (;;) {
      await new Promise((r) => setTimeout(r, 700));
      const job = await api('/api/jobs/' + id);
      setStep(job.step, job.progress, job.started);
      if (job.state === 'done') { done(job.result); break; }
      if (job.state === 'error') { showError(job.error); break; }
    }
  } catch (e) {
    showError(e.message);
  }
  $('progress').hidden = true;
  busy = false;
  updateButton();
});

function setStep(text, frac, started) {
  const secs = started ? Math.round(Date.now() / 1000 - started) : 0;
  $('stepText').textContent = secs ? `${text} · ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}` : text;
  $('barFill').style.width = `${Math.round((frac || 0) * 100)}%`;
}

let current = null;
function done(r) {
  current = r;
  show(r);
  $('resultBar').hidden = false;
  $('resultName').textContent = r.folder;
  const bits = [r.engine_label, r.faces && `${r.faces.toLocaleString()} triangles`, `${(r.size / 1e6).toFixed(1)} MB`, r.seconds && `${Math.round(r.seconds)} s`];
  $('resultInfo').textContent = bits.filter(Boolean).join(' · ');
  $('resultPath').textContent = r.path;
  $('download').href = SITE ? '#' : `/files/${encodeURIComponent(r.folder)}/model.glb`;
  $('download').download = `${r.folder}.glb`;
  $('fixModelBar').hidden = !canDraw();
  $('mirrorBar').hidden = !status.engines;  // needs the PC
  $('mirrorBtn').hidden = false;
  $('mirrorPick').hidden = true;
  $('mirrorText').textContent = r.mirrored
    ? 'Mirrored so both sides match.'
    : 'Other side look wrong? The AI only saw one side of it.';
  $('mirrorBtn').textContent = r.mirrored ? 'Pick a different side' : 'Fix it by mirroring';
  loadGallery();
}

// ---------- mirror fix ----------
let picking = 'mirror-a';
function pickVersion(v) {
  picking = v;
  for (const s of document.querySelectorAll('#mirrorPick .seg')) s.classList.toggle('on', s.dataset.v === v);
  show(current, v + '.glb', true);
}
$('mirrorBtn').addEventListener('click', async () => {
  const b = $('mirrorBtn');
  b.disabled = true;
  b.textContent = 'Mirroring… (a few seconds)';
  try {
    await api('/api/mirror?folder=' + encodeURIComponent(current.folder), { method: 'POST' });
    current = { ...current, online: false };  // the mirrored versions are only on the PC
    b.hidden = true;
    $('mirrorText').textContent = 'Spin it around and pick the one that looks right:';
    $('mirrorPick').hidden = false;
    pickVersion('mirror-a');
  } catch (e) {
    showError(e.message);
  }
  b.disabled = false;
});
for (const s of document.querySelectorAll('#mirrorPick .seg')) s.addEventListener('click', () => pickVersion(s.dataset.v));
$('mirrorKeep').addEventListener('click', async () => {
  try {
    done(await api(`/api/choose?folder=${encodeURIComponent(current.folder)}&version=${picking}`, { method: 'POST' }));
  } catch (e) {
    showError(e.message);
  }
});
$('openFolder').addEventListener('click', () => current && api('/api/open?folder=' + encodeURIComponent(current.folder), { method: 'POST' }));
$('openAll').addEventListener('click', () => api('/api/open', { method: 'POST' }));
$('download').addEventListener('click', async (e) => {
  if (!SITE || !current) return;  // on the PC the link downloads by itself
  e.preventDefault();
  try {
    await saveFile(await modelFile(current, 'model.glb'), `${current.folder}.glb`);
  } catch (err) {
    showError(err.message);
  }
});

// ---------- gallery ----------
async function loadGallery() {
  let list = [];
  if (SITE) {
    // the models on the Cloudflare site, plus any new ones on the PC that aren't up there yet
    list = await fetch(`${SITE}/models.json`, { cache: 'no-store' }).then((r) => r.json()).catch(() => []);
    list = list.map((m) => ({ ...m, online: true }));
    const pc = base === null ? [] : await api('/api/recent').catch(() => []);
    const there = new Set(list.map((m) => m.folder));
    list = [...pc.filter((m) => !there.has(m.folder)), ...list];
  } else {
    list = await api('/api/recent').catch(() => []);
  }
  const g = $('gallery');
  g.replaceChildren(...list.map((r) => {
    const card = document.createElement('button');
    card.className = 'card' + (current && current.folder === r.folder ? ' active' : '');
    const img = document.createElement('img');
    modelFile(r, 'cutout.png').then((u) => { img.src = u; }, () => { img.style.visibility = 'hidden'; });
    img.alt = '';
    const name = document.createElement('div');
    name.textContent = r.folder;
    const info = document.createElement('small');
    info.textContent = [r.engine_label, `${(r.size / 1e6).toFixed(1)} MB`].filter(Boolean).join(' · ');
    card.append(img, name, info);
    card.addEventListener('click', () => done(r));
    return card;
  }));
  if (!list.length) g.textContent = 'Nothing yet. Your models will appear here.';
}
loadGallery();

// ---------- shared: waiting on a GPU job ----------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitJob(id, onStep) {
  for (;;) {
    await sleep(700);
    const job = await api('/api/jobs/' + id);
    onStep(job);
    if (job.state === 'done') return job.result;
    if (job.state === 'error') throw new Error(job.error);
  }
}
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').split('-').slice(0, 5).join('-') || 'model';
async function urlToFile(url, name) {
  const blob = await (await call(url)).blob();
  return new File([blob], name, { type: blob.type || 'image/png' });
}

// ---------- prompt mode: words -> picture (then the usual picture -> 3D) ----------
function showTab(name) {
  for (const [tab, t] of [['picture', 'tabPicture'], ['words', 'tabWords'], ['web', 'tabWeb']]) $(t).classList.toggle('on', tab === name);
  $('wordsBox').hidden = name !== 'words';
  $('webBox').hidden = name !== 'web';
  $('dropText').textContent = {
    words: 'Your drawn picture will show up here',
    web: 'Click a search result, or drag a picture here from a web page',
  }[name] || 'Drop a picture here, click to pick one, or paste with Ctrl+V';
  if (name === 'words') $('prompt').focus();
  if (name === 'web') $('webQuery').focus();
}
$('tabPicture').addEventListener('click', () => showTab('picture'));
$('tabWords').addEventListener('click', () => showTab('words'));
$('tabWeb').addEventListener('click', () => showTab('web'));

// ---------- from the web: free-to-use picture search, plus links out to Google / Bing ----------
let searchTicket = 0;
async function webSearch() {
  const q = $('webQuery').value.trim();
  if (!q) return $('webQuery').focus();
  const source = document.querySelector('input[name=webSource]:checked').value;
  $('googleLink').href = 'https://www.google.com/search?tbm=isch&q=' + encodeURIComponent(q);
  $('bingLink').href = 'https://www.bing.com/images/search?q=' + encodeURIComponent(q);
  const ticket = ++searchTicket, box = $('webResults');
  $('webText').textContent = 'Searching…';
  try {
    const list = await api(`/api/search?source=${source}&q=${encodeURIComponent(q)}`);
    if (ticket !== searchTicket) return;
    box.replaceChildren(...list.map((r) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'web-card';
      card.title = `${r.title}${r.creator ? ' by ' + r.creator : ''} (${r.license})`;
      const img = document.createElement('img');
      img.src = r.thumb;
      img.alt = r.title;
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      const tag = document.createElement('span');
      tag.textContent = r.license;
      card.append(img, tag);
      card.addEventListener('click', () => {
        for (const c of box.children) c.classList.toggle('on', c === card);
        const credit = `"${r.title}"${r.creator ? ' by ' + r.creator : ''}, ${r.license}, ${r.source}: ${r.page || r.url}`;
        pickFromWeb(r.url, credit, r.title);
      });
      return card;
    }));
    $('webText').textContent = list.length
      ? 'Click a picture to use it. The license is on each one; its credit goes into the model\'s license.txt.'
      : 'Nothing found. Try other words, or search Google or Bing below.';
  } catch (e) {
    if (ticket === searchTicket) { box.replaceChildren(); $('webText').textContent = e.message; }
  }
}
$('webForm').addEventListener('submit', (e) => { e.preventDefault(); webSearch(); });
for (const r of document.querySelectorAll('input[name=webSource]')) r.addEventListener('change', () => $('webQuery').value.trim() && webSearch());

// ---------- cut out one part (crop) ----------
const crImage = $('crImage'), crBox = $('crBox');
const cr = { box: null, start: null, name: 'model' };
async function openCropper(src, name) {
  const img = new Image();
  img.src = src;
  await img.decode();
  for (const c of [crImage, crBox]) { c.width = img.naturalWidth; c.height = img.naturalHeight; }
  crImage.getContext('2d').drawImage(img, 0, 0);
  cr.box = null;
  cr.name = name;
  drawCropBox();
  $('cropper').hidden = false;
}
function cropPoint(e) {
  const r = crBox.getBoundingClientRect();
  return [Math.min(Math.max((e.clientX - r.left) * crBox.width / r.width, 0), crBox.width),
    Math.min(Math.max((e.clientY - r.top) * crBox.height / r.height, 0), crBox.height)];
}
function drawCropBox() {
  const g = crBox.getContext('2d');
  g.clearRect(0, 0, crBox.width, crBox.height);
  const b = cr.box;
  const ok = b && b[2] - b[0] > 16 && b[3] - b[1] > 16;
  $('crUse').disabled = !ok;
  $('crSize').textContent = ok ? `${Math.round(b[2] - b[0])} × ${Math.round(b[3] - b[1])} pixels` : '';
  if (!b) return;
  g.fillStyle = 'rgba(5, 7, 10, 0.6)';  // darken everything outside the box
  g.fillRect(0, 0, crBox.width, crBox.height);
  g.clearRect(b[0], b[1], b[2] - b[0], b[3] - b[1]);
  g.strokeStyle = '#3d8bff';
  g.lineWidth = Math.max(2, crBox.width / 300);
  g.strokeRect(b[0], b[1], b[2] - b[0], b[3] - b[1]);
}
crBox.addEventListener('pointerdown', (e) => { cr.start = cropPoint(e); crBox.setPointerCapture(e.pointerId); });
crBox.addEventListener('pointermove', (e) => {
  if (!cr.start) return;
  const [x, y] = cropPoint(e), [sx, sy] = cr.start;
  cr.box = [Math.min(x, sx), Math.min(y, sy), Math.max(x, sx), Math.max(y, sy)];
  drawCropBox();
});
crBox.addEventListener('pointerup', () => { cr.start = null; });
$('crClose').addEventListener('click', () => { $('cropper').hidden = true; });
$('cropper').addEventListener('keydown', (e) => { if (e.key === 'Escape') $('cropper').hidden = true; });
$('crUse').addEventListener('click', async () => {
  const [x0, y0, x1, y1] = cr.box.map(Math.round);
  const c = document.createElement('canvas');
  c.width = x1 - x0;
  c.height = y1 - y0;
  c.getContext('2d').drawImage(crImage, x0, y0, c.width, c.height, 0, 0, c.width, c.height);
  const blob = await new Promise((r) => c.toBlob(r, 'image/png'));
  $('cropper').hidden = true;
  pick(new File([blob], cr.name + '-part.png', { type: 'image/png' }), pickedCredit);
});
$('cropPicture').addEventListener('click', () => picked && openCropper(pickedUrl, $('name').value || 'model'));

// ---------- take Image to 3D to a Chromebook ----------
// 1. the models go up to the user's own Cloudflare site, 2. a Cloudflare tunnel lets the Chromebook file use this PC
// (with the passcode), 3. one small HTML file runs the whole app from file:// (see cloud.py and chromebook.py).
let cb = {}, cbTimer = null;
function cbShow() {
  const text = $('cbSiteText');
  text.classList.toggle('error', !!cb.error);
  if (!cb.installed) text.textContent = "Cloudflare's tools aren't installed. Run Setup.bat.";
  else if (cb.busy) {
    text.textContent = cb.busy + ' ';
    if (cb.login_url) {
      const a = document.createElement('a');
      a.href = cb.login_url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = "Open Cloudflare's login page";
      text.append(a);
    }
  }
  else if (cb.error) text.textContent = cb.error;
  else if (!cb.site) {
    text.textContent = "Not set up yet. If you don't have a Cloudflare account, sign up for free at cloudflare.com first. " +
      'Then click Connect Cloudflare: a Cloudflare page opens in your browser; log in there and click Allow.';
  } else {
    text.textContent = `Online: ${cb.online.length} of your ${cb.count} models. Anyone who has your site's address ` +
      `(${cb.site}) can see them, so only put models online that you're allowed to share.`;
  }
  $('cbSetup').hidden = !!cb.site && !cb.error;
  $('cbSetup').textContent = cb.site ? 'Try again' : 'Connect Cloudflare';
  $('cbUpdate').hidden = !cb.site;
  $('cbSetup').disabled = $('cbUpdate').disabled = !cb.installed || !!cb.busy;
  $('cbAuto').checked = cb.auto;
  $('cbMakeOn').checked = cb.make;
  $('cbMakeOn').disabled = !cb.site;
  const pass = $('cbPassText');
  pass.classList.toggle('error', !!(cb.tunnel_error || cb.remote_error));
  if (!cb.site) pass.textContent = 'Do step 1 first.';
  else if (!cb.make) pass.textContent = 'Off: the Chromebook file can only show and download your models.';
  else if (cb.remote_error || cb.tunnel_error) pass.textContent = cb.remote_error || cb.tunnel_error;
  else if (!cb.tunnel) pass.textContent = 'Connecting to Cloudflare…';
  else {
    pass.innerHTML = 'On. Passcode: <b class="code"></b> (type it once on the Chromebook). It works while this PC is on ' +
      'with Image to 3D open.';
    pass.querySelector('.code').textContent = cb.passcode;
  }
  $('cbMake').disabled = !cb.site;
}
async function cbRefresh() {
  cb = await api('/api/cloud').catch((e) => ({ ...cb, error: e.message }));
  cbShow();
}
$('chromebookBtn').addEventListener('click', async () => {
  await cbRefresh();
  const list = await api('/api/recent?all=1').catch(() => []);
  $('cbList').replaceChildren(...list.map((r) => {
    const row = document.createElement('label');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !cb.skip.includes(r.folder);
    box.value = r.folder;
    box.addEventListener('change', cbSkip);
    const img = document.createElement('img');
    img.src = `/files/${encodeURIComponent(r.folder)}/cutout.png`;
    img.alt = '';
    img.onerror = () => { img.style.visibility = 'hidden'; };
    const name = document.createElement('span');
    name.textContent = r.folder;
    const size = document.createElement('small');
    size.textContent = [r.engine_label, `${(r.size / 1e6).toFixed(1)} MB`].filter(Boolean).join(' · ');
    row.append(box, img, name, size);
    return row;
  }));
  $('cbResult').hidden = $('cbOpen').hidden = true;
  $('chromebook').hidden = false;
  clearInterval(cbTimer);
  cbTimer = setInterval(() => { if ($('chromebook').hidden) clearInterval(cbTimer); else cbRefresh(); }, 2000);
});
const cbSettings = async (body) => { cb = await api('/api/cloud/settings', { method: 'POST', body: JSON.stringify(body) }); cbShow(); };
function cbSkip() {
  const skip = [...document.querySelectorAll('#cbList input')].filter((b) => !b.checked).map((b) => b.value);
  cbSettings({ skip }).then(() => { if (cb.site) $('cbSiteText').textContent += ' Click Update now to apply your ticks.'; });
}
$('cbAll').addEventListener('click', () => { document.querySelectorAll('#cbList input').forEach((b) => { b.checked = true; }); cbSkip(); });
$('cbNone').addEventListener('click', () => { document.querySelectorAll('#cbList input').forEach((b) => { b.checked = false; }); cbSkip(); });
$('cbClose').addEventListener('click', () => { $('chromebook').hidden = true; });
$('cbSetup').addEventListener('click', async () => {
  try { cb = await api('/api/cloud/setup', { method: 'POST' }); } catch (e) { cb.error = e.message; }
  cbShow();
});
$('cbUpdate').addEventListener('click', async () => {
  try { cb = await api('/api/cloud/publish', { method: 'POST' }); } catch (e) { cb.error = e.message; }
  cbShow();
});
$('cbAuto').addEventListener('change', () => cbSettings({ auto: $('cbAuto').checked }));
$('cbMakeOn').addEventListener('change', () => cbSettings({ make: $('cbMakeOn').checked }));
$('cbMake').addEventListener('click', async () => {
  const btn = $('cbMake');
  btn.disabled = true;
  try {
    const r = await api('/api/chromebook', { method: 'POST' });
    $('cbResult').textContent = `Done: saved as ${r.path} (${(r.size / 1e6).toFixed(1)} MB). Copy that one file to your ` +
      'Chromebook (USB stick, Google Drive or OneDrive) and open it from the Files app. You only need to do this ' +
      'once: new models show up in it by themselves.';
    $('cbResult').hidden = $('cbOpen').hidden = false;
  } catch (e) {
    $('cbResult').textContent = e.message;
    $('cbResult').hidden = false;
  }
  btn.disabled = false;
});
$('cbOpen').addEventListener('click', () => api('/api/open', { method: 'POST' }));

// ---------- clean up with AI ----------
// The picture AI redraws the picture as a clean studio render of the same object. Screenshots and photos have
// reflections, odd lighting and blur that TRELLIS.2 bakes into the model; a clean render gives a much cleaner model.
const CLEAN_UP = 'Turn this into a clean, sharp, high-detail 3D render of exactly the same object, with the same ' +
  'design, shape, parts and colors, the whole object in view. Plain light gray background, soft even studio ' +
  'lighting, no reflections of the surroundings.';
let beforeClean = null;  // the picture before a clean-up, so "Back to the original" can put it back
const fileToDataUrl = (file) => new Promise((ok, fail) => {
  const r = new FileReader();
  r.onload = () => ok(r.result);
  r.onerror = () => fail(new Error("Couldn't read the picture."));
  r.readAsDataURL(file);
});
$('cleanPicture').addEventListener('click', async () => {
  if (!picked) return;
  if (beforeClean) {
    const b = beforeClean;
    pick(b.file, b.credit);
    return;
  }
  const btn = $('cleanPicture'), file = picked, credit = pickedCredit, name = $('name').value || 'model';
  btn.disabled = true;
  $('error').hidden = true;
  try {
    const image = await fileToDataUrl(file);
    const { id } = await api('/api/edit', { method: 'POST', body: JSON.stringify({ prompt: CLEAN_UP, image, mask: null, seed: -1 }) });
    const r = await waitJob(id, (job) => { btn.textContent = job.step; });
    pick(await urlToFile(r.image, name + '-clean.png'), credit);
    beforeClean = { file, credit };
    btn.textContent = 'Back to the original';
  } catch (e) {
    btn.textContent = 'Clean up with AI';
    showError(e.message);
  }
  btn.disabled = false;
});

$('draw').addEventListener('click', async () => {
  const prompt = $('prompt').value.trim();
  if (!prompt) return $('prompt').focus();
  const b = $('draw');
  b.disabled = true;
  $('error').hidden = true;
  try {
    const { id } = await api('/api/imagine', { method: 'POST', body: JSON.stringify({ prompt, seed: -1 }) });
    const r = await waitJob(id, (job) => { $('drawText').textContent = job.step; });
    pick(await urlToFile(r.image, slug(prompt) + '.png'));
    $('drawText').textContent = "Not quite right? Click Draw again for a new one, or fix part of it below.";
    b.textContent = 'Draw again';
  } catch (e) {
    $('drawText').textContent = '';
    showError(e.message);
  }
  b.disabled = false;
});
$('prompt').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('draw').click(); } });

// ---------- highlight and fix ----------
// Paint over what's wrong, say what it should be; the picture AI redraws just that area. Works on the picked picture,
// or on a snapshot of the 3D model from the angle it's shown at (the fixed picture then makes a new model).
const ed = { name: 'model', history: [], erasing: false, painting: false, last: null };
const edImage = $('edImage'), edMask = $('edMask');

async function openEditor(src, name) {
  const img = new Image();
  img.src = src;
  await img.decode();
  const s = Math.min(1, 2048 / Math.max(img.width, img.height));
  const w = Math.round(img.width * s), h = Math.round(img.height * s);
  for (const c of [edImage, edMask]) { c.width = w; c.height = h; }
  const g = edImage.getContext('2d');
  g.fillStyle = '#c8c8c8';  // see-through parts (a 3D snapshot) on plain light gray, which the AIs handle well
  g.fillRect(0, 0, w, h);
  g.drawImage(img, 0, 0, w, h);
  edMask.getContext('2d').clearRect(0, 0, w, h);
  ed.name = name;
  ed.history = [];
  $('edPrompt').value = '';
  $('edUndo').disabled = true;
  $('edError').hidden = true;
  $('edBusy').hidden = true;
  $('editor').hidden = false;
  $('edPrompt').focus();
}
const closeEditor = () => { $('editor').hidden = true; };
$('edClose').addEventListener('click', closeEditor);
$('editor').addEventListener('keydown', (e) => { if (e.key === 'Escape') closeEditor(); });

function brushAt(e) {
  const r = edMask.getBoundingClientRect();
  return [(e.clientX - r.left) * edMask.width / r.width, (e.clientY - r.top) * edMask.height / r.height,
    Number($('edBrush').value) * edMask.width / r.width];
}
function paint(e) {
  const [x, y, size] = brushAt(e);
  const g = edMask.getContext('2d');
  g.globalCompositeOperation = ed.erasing ? 'destination-out' : 'source-over';
  g.strokeStyle = g.fillStyle = '#ff3355';
  g.lineWidth = size;
  g.lineCap = 'round';
  g.beginPath();
  if (ed.last) { g.moveTo(...ed.last); g.lineTo(x, y); g.stroke(); }
  g.arc(x, y, size / 2, 0, Math.PI * 2);
  g.fill();
  ed.last = [x, y];
}
edMask.addEventListener('pointerdown', (e) => { ed.painting = true; ed.last = null; edMask.setPointerCapture(e.pointerId); paint(e); });
edMask.addEventListener('pointermove', (e) => { if (ed.painting) paint(e); });
edMask.addEventListener('pointerup', () => { ed.painting = false; ed.last = null; });
$('edErase').addEventListener('click', () => {
  ed.erasing = !ed.erasing;
  $('edErase').classList.toggle('on', ed.erasing);
  $('edErase').textContent = ed.erasing ? 'Eraser (on)' : 'Eraser';
});
$('edClear').addEventListener('click', () => edMask.getContext('2d').clearRect(0, 0, edMask.width, edMask.height));

function maskDataUrl() {
  // the painted strokes as a plain black-and-white mask, or null if nothing is painted
  const src = edMask.getContext('2d').getImageData(0, 0, edMask.width, edMask.height);
  const out = new ImageData(src.width, src.height);
  let any = false;
  for (let i = 0; i < src.data.length; i += 4) {
    const on = src.data[i + 3] > 0;
    any = any || on;
    out.data[i] = out.data[i + 1] = out.data[i + 2] = on ? 255 : 0;
    out.data[i + 3] = 255;
  }
  if (!any) return null;
  const c = document.createElement('canvas');
  c.width = src.width; c.height = src.height;
  c.getContext('2d').putImageData(out, 0, 0);
  return c.toDataURL('image/png');
}

$('edFix').addEventListener('click', async () => {
  const prompt = $('edPrompt').value.trim();
  if (!prompt) return $('edPrompt').focus();
  $('edError').hidden = true;
  $('edBusy').hidden = false;
  $('edStep').textContent = 'Sending…';
  $('edBar').style.width = '2%';
  for (const b of ['edFix', 'edUse', 'edUndo']) $(b).disabled = true;
  const before = edImage.toDataURL('image/png');
  try {
    const { id } = await api('/api/edit', { method: 'POST', body: JSON.stringify({ prompt, image: before, mask: maskDataUrl(), seed: -1 }) });
    const r = await waitJob(id, (job) => { $('edStep').textContent = job.step; $('edBar').style.width = `${Math.round(job.progress * 100)}%`; });
    const img = new Image();
    img.src = await pcFile(r.image);
    await img.decode();
    ed.history.push(before);
    edImage.getContext('2d').drawImage(img, 0, 0, edImage.width, edImage.height);
    edMask.getContext('2d').clearRect(0, 0, edMask.width, edMask.height);
  } catch (e) {
    $('edError').textContent = e.message;
    $('edError').hidden = false;
  }
  $('edBusy').hidden = true;
  $('edFix').disabled = $('edUse').disabled = false;
  $('edUndo').disabled = !ed.history.length;
});
$('edUndo').addEventListener('click', async () => {
  const prev = ed.history.pop();
  if (!prev) return;
  const img = new Image();
  img.src = prev;
  await img.decode();
  edImage.getContext('2d').drawImage(img, 0, 0);
  $('edUndo').disabled = !ed.history.length;
});
$('edUse').addEventListener('click', async () => {
  const blob = await new Promise((r) => edImage.toBlob(r, 'image/png'));
  closeEditor();
  showTab('picture');
  pick(new File([blob], ed.name + '-fixed.png', { type: 'image/png' }), pickedCredit);
  $('drop').scrollIntoView({ block: 'nearest' });
});

$('fixPicture').addEventListener('click', () => picked && openEditor(pickedUrl, $('name').value || 'model'));

function snapshot() {
  // the model exactly as it's shown, at up to 1024 px, with a see-through background
  const size = renderer.getSize(new THREE.Vector2()), ratio = renderer.getPixelRatio();
  const s = 1024 / Math.max(size.x, size.y);
  renderer.setPixelRatio(1);
  renderer.setSize(Math.round(size.x * s), Math.round(size.y * s), false);
  renderer.render(scene, camera);
  const url = renderer.domElement.toDataURL('image/png');
  renderer.setPixelRatio(ratio);
  renderer.setSize(size.x, size.y, false);
  return url;
}
$('fixModel').addEventListener('click', () => {
  controls.autoRotate = false;
  openEditor(snapshot(), (current && current.folder) || 'model');
});
