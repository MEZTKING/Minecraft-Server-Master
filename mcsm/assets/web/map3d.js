import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
// URLs of the world data (the video editor's defaults; the history viewer passes its own per build)
const URLS = { tiles: "/api/tiles", tile: t => `/tile/${t.rx}/${t.rz}/${t.step}`, blocks: "/api/blocks",
               tex: n => `/blocktex/${n}.png` };
const BLOCKS = [];                                    // block meshes (cleared with the world)
const rad = d => d * Math.PI / 180;
let renderer, scene, camView, camMap, controls, pathLine, camMarker, boxes = {}, mapPlaced = false, lastPath = null;
let keyGroup = null, lastKeys = "";
const terrainMat = new THREE.MeshLambertMaterial({ vertexColors: true, flatShading: true });
function init(canvas) {
  if (renderer) return;
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  scene = new THREE.Scene();
  const sky = 0x9ec9ff; scene.background = new THREE.Color(sky); scene.fog = new THREE.Fog(sky, 180, 900);
  scene.add(new THREE.HemisphereLight(0xeef6ff, 0x6b5a45, 1.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5); sun.position.set(0.4, 1, 0.25); scene.add(sun);
  camView = new THREE.PerspectiveCamera(70, 16 / 9, 0.05, 3000);
  camMap = new THREE.PerspectiveCamera(55, 16 / 9, 0.5, 8000);
  controls = new OrbitControls(camMap, canvas); controls.enableDamping = true; controls.enabled = false;
  pathLine = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xffd23f }));
  pathLine.visible = false; scene.add(pathLine);
  camMarker = new THREE.Mesh(new THREE.ConeGeometry(1.2, 3, 4), new THREE.MeshBasicMaterial({ color: 0xff4040 }));
  camMarker.visible = false; scene.add(camMarker);
}
const TILES = [];                                     // [{t, buf, mesh}] so they can be rebuilt
function tileMesh(t, buf, covered = null) {
  // vertices spread over the whole region (a hair wider than the samples) so regions meet without gaps
  const n = 512 / t.step, heights = new Int16Array(buf, 0, n * n), rgb = new Uint8Array(buf, n * n * 2, n * n * 3), NO = -32768;
  const pos = new Float32Array(n * n * 3), col = new Float32Array(n * n * 3), idx = [];
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const k = j * n + i, h = heights[k];
    pos.set([t.rx * 512 + i * 512 / (n - 1), h === NO ? 0 : h + 0.5, t.rz * 512 + j * 512 / (n - 1)], k * 3);
    col.set([rgb[k * 3] / 255, rgb[k * 3 + 1] / 255, rgb[k * 3 + 2] / 255], k * 3);
  }
  for (let j = 0; j < n - 1; j++) for (let i = 0; i < n - 1; i++) {
    const a = j * n + i, b = a + 1, c = a + n, d = c + 1;
    if (heights[a] === NO || heights[b] === NO || heights[c] === NO || heights[d] === NO) continue;
    if (covered) {                                     // real blocks are shown there: leave the smooth surface out
      const bx = Math.floor((t.rx * 512 + i * t.step) / 16), bz = Math.floor((t.rz * 512 + j * t.step) / 16);
      if (covered.has(`${bx},${bz}`)) continue;
    }
    idx.push(a, c, b, b, c, d);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  g.setIndex(idx); g.computeVertexNormals();
  return new THREE.Mesh(g, terrainMat);
}
async function loadTerrain(say, urls = {}) {
  Object.assign(URLS, urls);
  let list;
  try { list = await (await fetch(URLS.tiles)).json(); } catch (e) { say("couldn't load the map"); return; }
  if (!list.tiles.length) { say(list.message || "no terrain"); return; }
  let done = 0;
  for (const t of list.tiles) {
    say(`loading the map ${done}/${list.tiles.length} (the first time reads the backup)`);
    try {
      const r = await fetch(URLS.tile(t));
      if (r.ok) { const buf = await r.arrayBuffer(), mesh = tileMesh(t, buf); scene.add(mesh); TILES.push({ t, buf, mesh }); }
    } catch (e) { /* skip that region */ }
    done++;
  }
  say(`map from the backup ${list.backup}`);
}
function resize(w, h) {
  if (!renderer) return;
  renderer.setSize(w, h, false);
  camView.aspect = camMap.aspect = w / h; camView.updateProjectionMatrix(); camMap.updateProjectionMatrix();
}
function render(cam, samples, players, view, keys = [], ksel = null) {
  if (!renderer) return;
  const f = [-Math.sin(rad(cam.yaw)) * Math.cos(rad(cam.pitch)), -Math.sin(rad(cam.pitch)), Math.cos(rad(cam.yaw)) * Math.cos(rad(cam.pitch))];
  camView.position.set(...cam.pos); camView.fov = cam.fov; camView.updateProjectionMatrix();
  camView.lookAt(cam.pos[0] + f[0], cam.pos[1] + f[1], cam.pos[2] + f[2]);
  const seen = new Set();
  for (const p of players) {
    seen.add(p.name);
    let b = boxes[p.name];
    if (!b) { b = boxes[p.name] = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.8, 0.6), new THREE.MeshLambertMaterial({ color: p.colour })); scene.add(b); }
    b.visible = true; b.position.set(p.pos[0], p.pos[1] + 0.9, p.pos[2]);
  }
  for (const [n, b] of Object.entries(boxes)) if (!seen.has(n)) b.visible = false;
  const map = view === "map";
  controls.enabled = map; pathLine.visible = map; camMarker.visible = map; if (keyGroup) keyGroup.visible = map;
  if (map) {
    if (samples !== lastPath) {
      lastPath = samples;
      pathLine.geometry.dispose();
      pathLine.geometry = new THREE.BufferGeometry().setFromPoints(samples.filter((_, i) => i % 4 === 0).map(s => new THREE.Vector3(s[0], s[1], s[2])));
    }
    if (!mapPlaced) {                                    // first look: above and behind the camera
      mapPlaced = true; controls.target.set(...cam.pos);
      camMap.position.set(cam.pos[0] - f[0] * 60, cam.pos[1] + 70, cam.pos[2] - f[2] * 60);
    }
    const sig = JSON.stringify(keys.map(k => [k.tick, k.position])) + ksel;
    if (sig !== lastKeys) {                              // camera keyframes as dots (selected: white, bigger)
      lastKeys = sig;
      if (keyGroup) { scene.remove(keyGroup); keyGroup.traverse(o => o.geometry && o.geometry.dispose()); }
      keyGroup = new THREE.Group();
      for (const k of keys) {
        const sel = k.tick === ksel;
        const m = new THREE.Mesh(new THREE.SphereGeometry(sel ? 1.1 : 0.7, 12, 8), new THREE.MeshBasicMaterial({ color: sel ? 0xffffff : 0xffd23f }));
        m.position.set(...k.position); keyGroup.add(m);
      }
      scene.add(keyGroup);
    }
    camMarker.position.set(...cam.pos);
    camMarker.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(...f));
    controls.update();
    renderer.render(scene, camMap);
  } else renderer.render(scene, camView);
}
// --- real blocks near the camera, with Minecraft's own textures (from the installed game) -----------
const IMAGES = new Map();                              // texture pictures, loaded once (many tiles share them)
function loadImage(url) {
  if (!IMAGES.has(url)) IMAGES.set(url, new Promise(res => { const im = new Image(); im.onload = () => res(im); im.onerror = () => res(null); im.src = url; }));
  return IMAGES.get(url);
}
async function buildAtlas(textures, tints, texUrl = URLS.tex) {
  const N = Math.ceil(Math.sqrt(textures.length)), C = 16, cv = document.createElement("canvas");
  cv.width = cv.height = N * C;
  const g = cv.getContext("2d"); g.imageSmoothingEnabled = false;
  const tmp = document.createElement("canvas"); tmp.width = tmp.height = C; const tg = tmp.getContext("2d"); tg.imageSmoothingEnabled = false;
  const draw = (im, tint) => {                               // one 16x16 frame (animated strips: the first), tinted
    tg.globalCompositeOperation = "source-over"; tg.clearRect(0, 0, C, C);
    tg.drawImage(im, 0, 0, im.width, im.width, 0, 0, C, C);
    if (tint) {
      tg.globalCompositeOperation = "multiply"; tg.fillStyle = `rgb(${tint[0]},${tint[1]},${tint[2]})`; tg.fillRect(0, 0, C, C);
      tg.globalCompositeOperation = "destination-in"; tg.drawImage(im, 0, 0, im.width, im.width, 0, 0, C, C);
    }
    return tmp;
  };
  for (let i = 0; i < textures.length; i++) {
    const x = (i % N) * C, y = Math.floor(i / N) * C, parts = textures[i].split("+");
    const base = await loadImage(texUrl(parts[0]));
    if (base) g.drawImage(draw(base, tints[parts[0]]), x, y);
    else { g.fillStyle = "#888"; g.fillRect(x, y, C, C); }
    if (parts[1]) {                                          // grass block side: + tinted overlay
      const ov = await loadImage(texUrl(`${parts[0]}_${parts[1]}`));
      if (ov) g.drawImage(draw(ov, tints[`${parts[0]}_${parts[1]}`]), x, y);
    }
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter; tex.generateMipmaps = false;
  tex.colorSpace = THREE.SRGBColorSpace;
  return { tex, N };
}
const SHADE = [1.0, 0.5, 0.8, 0.8, 0.62, 0.62];
// The real blocks of a block list (``info`` from the server, ``buf`` its faces: int16 x, y, z, direction, look) as
// meshes. *lit*: lit by the scene's sun and sky (normals; the per-side shading is softer), else flat-shaded.
function blockMeshes(info, buf, atlas, lit = false) {
  const f = new Int16Array(buf), n = f.length / 5, [ox, oz] = info.origin, N = atlas.N, eps = 0.02 / N;
  const geo = { solid: { p: [], u: [], c: [], n: [], i: [] }, water: { p: [], u: [], c: [], n: [], i: [] } };
  const NORMAL = [[0, 1, 0], [0, -1, 0], [0, 0, -1], [0, 0, 1], [-1, 0, 0], [1, 0, 0], [0, 1, 0]];
  const quad = (G, pts, tex, v0, v1, u0, u1, shade, d) => {
    const base = G.p.length / 3, cx = (tex % N) / N, cy = Math.floor(tex / N) / N, nm = NORMAL[d];
    for (const q of pts) { G.p.push(q[0], q[1], q[2]); G.n.push(nm[0], nm[1], nm[2]); }
    const U = a => cx + eps + (a * (1 - 2 * eps * N)) / N, V = b => 1 - (cy + eps + ((1 - b) * (1 - 2 * eps * N)) / N);
    G.u.push(U(u0), V(v0), U(u1), V(v0), U(u1), V(v1), U(u0), V(v1));
    const sh = lit ? 0.72 + 0.28 * shade : shade;
    for (let k = 0; k < 4; k++) G.c.push(sh, sh, sh);
    G.i.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  for (let k = 0; k < n; k++) {
    const x = f[k * 5] + ox, y = f[k * 5 + 1], z = f[k * 5 + 2] + oz, d = f[k * 5 + 3], b = info.infos[f[k * 5 + 4]];
    if (!b) continue;
    const G = b.kind === "water" ? geo.water : geo.solid;
    if (d === 6) {                                             // a plant: two crossed quads
      const t = b.tex[0];
      quad(G, [[x, y, z], [x + 1, y, z + 1], [x + 1, y + 1, z + 1], [x, y + 1, z]], t, 0, 1, 0, 1, 0.9, 6);
      quad(G, [[x + 1, y, z], [x, y, z + 1], [x, y + 1, z + 1], [x + 1, y + 1, z]], t, 0, 1, 0, 1, 0.9, 6);
      continue;
    }
    const [a0, b0, c0, a1, b1, c1] = b.box.map(v => v / 16), X0 = x + a0, X1 = x + a1, Y0 = y + b0, Y1 = y + b1, Z0 = z + c0, Z1 = z + c1;
    const t = b.tex[d] ?? b.tex[0], sh = SHADE[d];
    if (d === 0) quad(G, [[X0, Y1, Z0], [X1, Y1, Z0], [X1, Y1, Z1], [X0, Y1, Z1]], t, a0, a1, c0, c1, sh, d);
    else if (d === 1) quad(G, [[X0, Y0, Z1], [X1, Y0, Z1], [X1, Y0, Z0], [X0, Y0, Z0]], t, a0, a1, c0, c1, sh, d);
    else if (d === 2) quad(G, [[X1, Y0, Z0], [X0, Y0, Z0], [X0, Y1, Z0], [X1, Y1, Z0]], t, b0, b1, 1 - a1, 1 - a0, sh, d);
    else if (d === 3) quad(G, [[X0, Y0, Z1], [X1, Y0, Z1], [X1, Y1, Z1], [X0, Y1, Z1]], t, b0, b1, a0, a1, sh, d);
    else if (d === 4) quad(G, [[X0, Y0, Z0], [X0, Y0, Z1], [X0, Y1, Z1], [X0, Y1, Z0]], t, b0, b1, c0, c1, sh, d);
    else quad(G, [[X1, Y0, Z1], [X1, Y0, Z0], [X1, Y1, Z0], [X1, Y1, Z1]], t, b0, b1, 1 - c1, 1 - c0, sh, d);
  }
  const mesh = (G, mat) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(G.p, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(G.u, 2));
    g.setAttribute("color", new THREE.Float32BufferAttribute(G.c, 3));
    if (lit) g.setAttribute("normal", new THREE.Float32BufferAttribute(G.n, 3));
    g.setIndex(G.i);
    return new THREE.Mesh(g, mat);
  };
  const Mat = lit ? THREE.MeshLambertMaterial : THREE.MeshBasicMaterial;
  const out = [mesh(geo.solid, new Mat({ map: atlas.tex, vertexColors: true, alphaTest: 0.5, side: THREE.DoubleSide }))];
  if (geo.water.i.length) out.push(mesh(geo.water, new Mat({ map: atlas.tex, vertexColors: true, transparent: true,
    opacity: 0.75, depthWrite: false, side: THREE.DoubleSide })));
  return out;
}
async function loadBlocks(say, urls = {}) {
  Object.assign(URLS, urls);
  let info;
  try { info = await (await fetch(URLS.blocks)).json(); } catch (e) { return; }
  if (!info.faces) { if (info.message) say(info.message); return; }
  say(`loading ${info.faces.toLocaleString()} block faces and their textures…`);
  const [buf, atlas] = await Promise.all([fetch(info.url).then(r => r.arrayBuffer()), buildAtlas(info.textures, info.tints)]);
  const f = new Int16Array(buf), n = f.length / 5, [ox, oz] = info.origin;
  const covered = new Set();
  for (let k = 0; k < n; k++) covered.add(`${(f[k * 5] + ox) >> 4},${(f[k * 5 + 2] + oz) >> 4}`);
  for (const T of TILES) {                             // rebuild the smooth terrain without the block area
    scene.remove(T.mesh); T.mesh.geometry.dispose(); T.mesh = tileMesh(T.t, T.buf, covered); scene.add(T.mesh);
  }
  for (const m of blockMeshes(info, buf, atlas, false)) { BLOCKS.push(m); scene.add(m); }
  say(`real blocks near the camera (textures from ${info.jar})`);
}
// --- looking around freely (the history viewer's timelapse view) ---------------------------------------
function clearWorld() {                               // another place: drop the terrain and blocks
  for (const T of TILES.splice(0)) { scene.remove(T.mesh); T.mesh.geometry.dispose(); }
  for (const b of BLOCKS.splice(0)) { scene.remove(b); b.geometry.dispose(); b.material.map && b.material.map.dispose(); b.material.dispose(); }
  for (const [n, b] of Object.entries(boxes)) { scene.remove(b); delete boxes[n]; }
}
function lookFrom(pos, target, fov = 70) {           // put the free camera somewhere, looking at a point
  camMap.position.set(...pos); camMap.fov = fov; camMap.updateProjectionMatrix();
  controls.target.set(...target); controls.update(); mapPlaced = true;
}
function view() {
  // the free camera as Minecraft sees it: eye position, yaw (0 = +z, 90 = -x) and pitch (+ = down)
  const d = new THREE.Vector3(); camMap.getWorldDirection(d);
  let yaw = Math.atan2(-d.x, d.z) * 180 / Math.PI; if (yaw <= -180) yaw += 360;
  const pitch = Math.max(-90, Math.min(90, -Math.asin(Math.max(-1, Math.min(1, d.y))) * 180 / Math.PI));
  return { eye: camMap.position.toArray(), yaw, pitch, fov: camMap.fov, target: controls.target.toArray() };
}
function renderFree(players, marks = []) {           // the world through the free camera (orbit/zoom/pan)
  if (!renderer) return;
  controls.enabled = true; pathLine.visible = false; camMarker.visible = false; if (keyGroup) keyGroup.visible = false;
  const seen = new Set();
  for (const p of players) {
    seen.add(p.name);
    let b = boxes[p.name];
    if (!b) { b = boxes[p.name] = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.8, 0.6), new THREE.MeshLambertMaterial({ color: p.colour })); scene.add(b); }
    b.visible = true; b.position.set(p.pos[0], p.pos[1] + 0.9, p.pos[2]);
  }
  for (const [n, b] of Object.entries(boxes)) if (!seen.has(n)) b.visible = false;
  const sig = JSON.stringify(marks);
  if (sig !== lastKeys) {                              // saved views as dots
    lastKeys = sig;
    if (keyGroup) { scene.remove(keyGroup); keyGroup.traverse(o => o.geometry && o.geometry.dispose()); }
    keyGroup = new THREE.Group();
    for (const m of marks) {
      const s = new THREE.Mesh(new THREE.SphereGeometry(0.8, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffd23f }));
      s.position.set(...m); keyGroup.add(s);
    }
    scene.add(keyGroup);
  }
  keyGroup.visible = true;
  controls.update();
  renderer.render(scene, camMap);
}
export { init, loadTerrain, loadBlocks, resize, render, clearWorld, lookFrom, view, renderFree, buildAtlas, blockMeshes };
