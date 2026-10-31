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
let picked = null;
function pick(file) {
  if (!file || !file.type.startsWith('image/')) return showError("That file isn't a picture. Use a PNG, JPG or WEBP image.");
  picked = file;
  $('picked').src = URL.createObjectURL(file);
  $('picked').hidden = false;
  $('dropText').hidden = true;
  $('name').value = (file.name || 'pasted').replace(/\.[^.]+$/, '').replace(/[^a-z0-9_-]+/gi, '-').toLowerCase().slice(0, 60);
  $('error').hidden = true;
  updateButton();
}
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
  } catch {
    $('status').textContent = 'The app window was closed. Start it again with Start Image to 3D.bat';
    $('status').className = 'pill bad';
  }
  updateButton();
}
setInterval(poll, 3000);
poll();

let busy = false;
function updateButton() { $('go').disabled = !picked || busy || !status.ready; }
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
