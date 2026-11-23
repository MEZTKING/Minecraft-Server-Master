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
async function buildAtlas(textures, tints, texUrl = URLS.tex, sizes = {}) {
  // one picture holding every texture; entity textures (chests) need more pixels than block ones
  const N = Math.ceil(Math.sqrt(textures.length)), C = textures.some(t => t.startsWith("entity/")) ? 64 : 16;
  const cv = document.createElement("canvas");
  cv.width = cv.height = N * C;
  const g = cv.getContext("2d"); g.imageSmoothingEnabled = false;
  const tmp = document.createElement("canvas"); tmp.width = tmp.height = C; const tg = tmp.getContext("2d"); tg.imageSmoothingEnabled = false;
  const draw = (im, tint, whole = false) => {               // one square frame (animated strips: the first), tinted
    tg.globalCompositeOperation = "source-over"; tg.clearRect(0, 0, C, C);
    if (whole) tg.drawImage(im, 0, 0, im.width, im.height, 0, 0, C, C);   // entity textures: the whole picture
    else tg.drawImage(im, 0, 0, im.width, im.width, 0, 0, C, C);
    if (tint) {
      tg.globalCompositeOperation = "multiply"; tg.fillStyle = `rgb(${tint[0]},${tint[1]},${tint[2]})`; tg.fillRect(0, 0, C, C);
      tg.globalCompositeOperation = "destination-in"; tg.drawImage(im, 0, 0, im.width, im.width, 0, 0, C, C);
    }
    return tmp;
  };
  for (let i = 0; i < textures.length; i++) {
    const x = (i % N) * C, y = Math.floor(i / N) * C, parts = textures[i].split("+");
    const base = await loadImage(texUrl(parts[0]));
    if (base) g.drawImage(draw(base, tints[parts[0]], parts[0].startsWith("entity/")), x, y);
    else { g.fillStyle = "#888"; g.fillRect(x, y, C, C); }
    if (parts[1]) {                                          // grass block side: + the overlay the biome colours
      const ov = await loadImage(texUrl(`${parts[0]}_${parts[1]}`));
      if (ov) {
        // the overlay's pixels are marked (alpha 250) so the shader tints only them, not the dirt underneath
        const o = draw(ov, null), od = o.getContext("2d").getImageData(0, 0, C, C), cd = g.getImageData(x, y, C, C);
        for (let k = 0; k < od.data.length; k += 4) if (od.data[k + 3] > 128) {
          cd.data[k] = od.data[k]; cd.data[k + 1] = od.data[k + 1]; cd.data[k + 2] = od.data[k + 2]; cd.data[k + 3] = 250;
        }
        g.putImageData(cd, x, y);
      }
    }
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter; tex.generateMipmaps = false;
  tex.colorSpace = THREE.SRGBColorSpace;
  return { tex, N, C };
}
// How the real blocks are lit (changed live by the page's light controls): Minecraft's own light levels (sky light
// times daylight, or block light from torches), its side shading and smooth-lighting corners, and the sun.
const LIGHT = {
  uDay: { value: 1.0 },            // daylight 0 (night) .. 1 (noon)
  uTorch: { value: 1.0 },          // how strongly torches, lanterns... light their surroundings
  uBright: { value: 0.5 },         // Minecraft's brightness option: 0 moody .. 1 bright
  uExposure: { value: 1.0 },       // overall
  uSunMix: { value: 0.35 },        // 0: Minecraft's fixed side shading, 1: shaded by the sun's direction
  uSunDir: { value: new THREE.Vector3(0.4, 1, 0.25).normalize() },
  uAO: { value: 1.0 },             // corner shading strength
};
function setLight(o = {}) {
  for (const [k, v] of Object.entries(o)) {
    const u = LIGHT["u" + k[0].toUpperCase() + k.slice(1)];
    if (!u) continue;
    if (u.value && u.value.isVector3) u.value.copy(v).normalize(); else u.value = v;
  }
}
function lightShader(sh) {
  Object.assign(sh.uniforms, LIGHT);
  sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nattribute vec3 aN; attribute vec3 aL; attribute vec4 aT; varying vec3 vN; varying vec3 vL; varying vec4 vT;")
    .replace("#include <begin_vertex>", "#include <begin_vertex>\nvN = aN; vL = aL; vT = aT;");
  sh.fragmentShader = sh.fragmentShader.replace("#include <common>", `#include <common>
varying vec3 vN; varying vec3 vL; varying vec4 vT;
uniform float uDay, uTorch, uBright, uExposure, uSunMix, uAO; uniform vec3 uSunDir;`)
    .replace("#include <color_fragment>", `#include <color_fragment>
  if (vT.a > 1.5) {                                                  // only the overlay (marked alpha 250)
    float o = 1.0 - step(0.995, diffuseColor.a);
    diffuseColor.rgb *= mix(vec3(1.0), vT.rgb, o); diffuseColor.a = 1.0;
  } else if (vT.a > 0.5) diffuseColor.rgb *= vT.rgb;               // the biome's grass, leaves or water colour
  {
    float sky = vL.x * uDay, blk = vL.y * uTorch;
    float l = clamp(max(sky, blk), 0.0, 1.0);
    float b = l / (3.0 - 2.0 * l);                                  // the game's light curve
    b = mix(b, 1.0 - pow(1.0 - b, 4.0), uBright);                 // its brightness option
    b = max(b, 0.04);
    float side = vN.y > 0.5 ? 1.0 : vN.y < -0.5 ? 0.5 : abs(vN.z) > 0.5 ? 0.8 : 0.6;
    if (dot(vN, vN) < 0.1) side = 1.0;                             // not shaded (torches, flames)
    else side = mix(side, 0.45 + 0.55 * max(dot(vN, uSunDir), 0.0), uSunMix * (0.4 + 0.6 * uDay));
    float ao = mix(1.0, vL.z, uAO);
    vec3 warm = mix(vec3(1.0), vec3(1.0, 0.86, 0.68), clamp(blk - sky, 0.0, 1.0));   // torch light is warm
    diffuseColor.rgb *= pow(clamp(vec3(b * side * ao * uExposure) * warm, 0.0, 4.0), vec3(2.2));
  }`);
}
const AO_LEVEL = [1.0, 0.8, 0.64, 0.5];
// The real blocks of a block list (``info`` from the server, ``buf`` its records: int16 x, y, z, dir, look; uint16
// extra) as meshes: dir 0-5 one side of a full block, 7 a whole model (see mcsm/blockmesh.py).
function blockMeshes(info, buf, atlas) {
  const dv = new DataView(buf), n = buf.byteLength / 14, [ox, oz] = info.origin, N = atlas.N;
  const G = () => ({ p: [], u: [], n: [], l: [], t: [], i: [] });
  const geo = { solid: G(), water: G() };
  const e = 0.001;
  const lin = v => Math.pow(v, 2.2);
  const quad = (Gr, q, x, y, z, sky, blk, ao, tint) => {
    const mode = q[23] || 0;
    const base = Gr.p.length / 3, t = q[0], col = t % N, row = Math.floor(t / N);
    const P = k => [x + q[3 + k * 3], y + q[4 + k * 3], z + q[5 + k * 3]];
    const p0 = P(0), p1 = P(1), p3 = P(3);
    let nx = 0, ny = 0, nz = 0;
    if (q[2]) {                                          // normal = down x right (the face points outwards)
      const ax = p1[0] - p0[0], ay = p1[1] - p0[1], az = p1[2] - p0[2], bx = p3[0] - p0[0], by = p3[1] - p0[1], bz = p3[2] - p0[2];
      nx = by * az - bz * ay; ny = bz * ax - bx * az; nz = bx * ay - by * ax;
      const len = Math.hypot(nx, ny, nz) || 1; nx /= len; ny /= len; nz /= len;
    }
    for (let k = 0; k < 4; k++) {
      const p = P(k);
      Gr.p.push(p[0], p[1], p[2]); Gr.n.push(nx, ny, nz);
      const u = Math.min(1 - e, Math.max(e, q[15 + k * 2])), v = Math.min(1 - e, Math.max(e, q[16 + k * 2]));
      Gr.u.push((col + u) / N, 1 - (row + v) / N);
      Gr.l.push(sky, blk, AO_LEVEL[(ao >> (2 * k)) & 3]);
      Gr.t.push(tint[0], tint[1], tint[2], mode);
    }
    Gr.i.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  const tints = new Map();                               // RGB565 -> linear colour
  for (let k = 0; k < n; k++) {
    const o = k * 14, x = dv.getInt16(o, true) + ox, y = dv.getInt16(o + 2, true), z = dv.getInt16(o + 4, true) + oz;
    const d = dv.getInt16(o + 6, true), b = info.infos[dv.getInt16(o + 8, true)], ex = dv.getUint16(o + 10, true);
    const t5 = dv.getUint16(o + 12, true);
    if (!b) continue;
    let tint = tints.get(t5);
    if (!tint) { tint = [lin(((t5 >> 11) & 31) / 31), lin(((t5 >> 5) & 63) / 63), lin((t5 & 31) / 31)]; tints.set(t5, tint); }
    const sky = ((ex >> 12) & 15) / 15, blk = ((ex >> 8) & 15) / 15;
    const Gr = b.kind === "water" ? geo.water : geo.solid;
    if (d === 7) {                                        // a whole model: its quads not hidden by a neighbour
      const mask = ex & 63;
      for (const q of b.quads) if (q[1] < 0 || !((mask >> q[1]) & 1)) quad(Gr, q, x, y, z, sky, blk, 0, tint);
    } else if (b.quads[d]) quad(Gr, b.quads[d], x, y, z, sky, blk, ex & 255, tint);
  }
  const mesh = (Gr, mat) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(Gr.p, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(Gr.u, 2));
    g.setAttribute("aN", new THREE.Float32BufferAttribute(Gr.n, 3));
    g.setAttribute("aL", new THREE.Float32BufferAttribute(Gr.l, 3));
    g.setAttribute("aT", new THREE.Float32BufferAttribute(Gr.t, 4));
    g.setIndex(Gr.i);
    g.computeBoundingSphere();
    mat.onBeforeCompile = lightShader;
    return new THREE.Mesh(g, mat);
  };
  const out = [mesh(geo.solid, new THREE.MeshBasicMaterial({ map: atlas.tex, alphaTest: 0.5, side: THREE.DoubleSide }))];
  if (geo.water.i.length) out.push(mesh(geo.water, new THREE.MeshBasicMaterial({ map: atlas.tex, transparent: true,
    opacity: 0.78, depthWrite: false, side: THREE.DoubleSide })));
  return out;
}
async function loadBlocks(say, urls = {}) {
  Object.assign(URLS, urls);
  let info;
  try { info = await (await fetch(URLS.blocks)).json(); } catch (e) { return; }
  if (!info.faces) { if (info.message) say(info.message); return; }
  say(`loading ${info.faces.toLocaleString()} block faces and their textures…`);
  const [buf, atlas] = await Promise.all([fetch(info.url).then(r => r.arrayBuffer()), buildAtlas(info.textures, info.tints)]);
  const f = new Int16Array(buf), n = f.length / 7, [ox, oz] = info.origin;
  const covered = new Set();
  for (let k = 0; k < n; k++) covered.add(`${(f[k * 7] + ox) >> 4},${(f[k * 7 + 2] + oz) >> 4}`);
  for (const T of TILES) {                             // rebuild the smooth terrain without the block area
    scene.remove(T.mesh); T.mesh.geometry.dispose(); T.mesh = tileMesh(T.t, T.buf, covered); scene.add(T.mesh);
  }
  for (const m of blockMeshes(info, buf, atlas)) { BLOCKS.push(m); scene.add(m); }
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
export { init, loadTerrain, loadBlocks, resize, render, clearWorld, lookFrom, view, renderFree, buildAtlas, blockMeshes, setLight, LIGHT };
