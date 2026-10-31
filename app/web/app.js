import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const $ = (id) => document.getElementById(id);
const api = (path, opts = {}) =>
  fetch(path, { ...opts, headers: { 'X-Image-To-3D': '1', ...(opts.headers || {}) } }).then(async (r) => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
    return data;
  });

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
function show(folder, file = 'model.glb', keepView = false) {
  new GLTFLoader().load(`/files/${encodeURIComponent(folder)}/${file}?t=${Date.now()}`, (gltf) => {
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
  cut.src = `/files/${encodeURIComponent(folder)}/cutout.png`;
  cut.hidden = false;
  cut.onerror = () => { cut.hidden = true; };
}

// ---------- picking a picture ----------
// Right after a picture is picked, the server cuts the object out (about 10 s) and the page shows exactly what the
// AI will get, so a bad cut-out (say, tire bottoms hidden in grass) is caught before a 5-minute run.
let picked = null, pickedUrl = null, cutout = null, cutting = 0;
function pick(file) {
  if (!file || !file.type.startsWith('image/')) return showError("That file isn't a picture. Use a PNG, JPG or WEBP image.");
  picked = file;
  pickedUrl = URL.createObjectURL(file);
  $('picked').hidden = false;
  $('dropText').hidden = true;
  $('name').value = (file.name || 'pasted').replace(/\.[^.]+$/, '').replace(/[^a-z0-9_-]+/gi, '-').toLowerCase().slice(0, 60);
  $('error').hidden = true;
  $('fixPicture').hidden = !canDraw();
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
      'in grass. Missing parts come out wrong, so if something is cut off, try a picture where the whole object shows.';
    return updateButton();
  }
  const file = picked, ticket = ++cutting;
  $('picked').src = pickedUrl;
  hint.hidden = false;
  hint.textContent = 'Cutting the object out of the picture… (about 10 seconds)';
  updateButton();
  try {
    const r = await api('/api/cutout', { method: 'POST', body: file, headers: { 'Content-Type': file.type } });
    if (ticket !== cutting) return;  // a newer picture was picked meanwhile
    cutout = { file, id: r.id, url: r.url };
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
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); pick(e.dataTransfer.files[0]); });
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') $('file').click(); });
window.addEventListener('paste', (e) => {
  const item = [...e.clipboardData.items].find((i) => i.type.startsWith('image/'));
  if (item) pick(item.getAsFile());
});

// ---------- status ----------
let status = {};
async function poll() {
  try {
    status = await api('/api/status');
    const pill = $('status');
    pill.className = 'pill' + (status.ready ? ' ok' : status.problems.length ? ' bad' : '');
    pill.textContent = status.ready ? `Ready · ${status.device}` : status.problems[0] || 'Starting the AI…';
    for (const input of document.querySelectorAll('input[name=engine]')) {
      const ok = status.engines[input.value.split('-')[0]];
      input.disabled = ok === false;
    }
    $('tabWords').disabled = !canDraw();
    $('tabWords').title = canDraw() ? '' : "The picture AI isn't installed. Run Setup.bat.";
  } catch {
    $('status').textContent = 'The app window was closed. Start it again with Start Image to 3D.bat';
    $('status').className = 'pill bad';
  }
  updateButton();
}
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
  show(r.folder);
  $('resultBar').hidden = false;
  $('resultName').textContent = r.folder;
  const bits = [r.engine_label, r.faces && `${r.faces.toLocaleString()} triangles`, `${(r.size / 1e6).toFixed(1)} MB`, r.seconds && `${Math.round(r.seconds)} s`];
  $('resultInfo').textContent = bits.filter(Boolean).join(' · ');
  $('resultPath').textContent = r.path;
  $('download').href = `/files/${encodeURIComponent(r.folder)}/model.glb`;
  $('download').download = `${r.folder}.glb`;
  $('fixModelBar').hidden = !canDraw();
  $('mirrorBar').hidden = false;
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
  show(current.folder, v + '.glb', true);
}
$('mirrorBtn').addEventListener('click', async () => {
  const b = $('mirrorBtn');
  b.disabled = true;
  b.textContent = 'Mirroring… (a few seconds)';
  try {
    await api('/api/mirror?folder=' + encodeURIComponent(current.folder), { method: 'POST' });
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

// ---------- gallery ----------
async function loadGallery() {
  const list = await api('/api/recent').catch(() => []);
  const g = $('gallery');
  g.replaceChildren(...list.map((r) => {
    const card = document.createElement('button');
    card.className = 'card' + (current && current.folder === r.folder ? ' active' : '');
    const img = document.createElement('img');
    img.src = `/files/${encodeURIComponent(r.folder)}/cutout.png`;
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
  const blob = await (await fetch(url)).blob();
  return new File([blob], name, { type: blob.type || 'image/png' });
}

// ---------- prompt mode: words -> picture (then the usual picture -> 3D) ----------
function showTab(words) {
  $('tabWords').classList.toggle('on', words);
  $('tabPicture').classList.toggle('on', !words);
  $('wordsBox').hidden = !words;
  $('dropText').textContent = words
    ? 'Your drawn picture will show up here'
    : 'Drop a picture here, click to pick one, or paste with Ctrl+V';
  if (words) $('prompt').focus();
}
$('tabPicture').addEventListener('click', () => showTab(false));
$('tabWords').addEventListener('click', () => showTab(true));

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
    img.src = r.image;
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
  showTab(false);
  pick(new File([blob], ed.name + '-fixed.png', { type: 'image/png' }));
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
