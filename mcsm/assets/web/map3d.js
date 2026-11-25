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
// every atlas built, for animating its moving textures (water, lava, fire, portals...: see animateAtlases)
const ATLASES = [];
async function buildAtlas(textures, tints, texUrl = URLS.tex, sizes = {}, animUrl = null) {
  // one picture holding every texture; entity textures (chests) need more pixels than block ones
  // cells per row: a power of two, so the atlas (N x C pixels) halves cleanly at every mipmap level and a cell's
  // smaller copies stay inside the cell
  const N = 2 ** Math.ceil(Math.log2(Math.max(1, Math.ceil(Math.sqrt(textures.length))))), C = textures.some(t => t.startsWith("entity/")) ? 64 : 16;
  const cv = document.createElement("canvas");
  cv.width = cv.height = N * C;
  const g = cv.getContext("2d"); g.imageSmoothingEnabled = false;
  const tmp = document.createElement("canvas"); tmp.width = tmp.height = C; const tg = tmp.getContext("2d"); tg.imageSmoothingEnabled = false;
  const draw = (im, tint, whole = false, frame = 0) => {    // one square frame (animated strips: frame *frame*), tinted
    tg.globalCompositeOperation = "source-over"; tg.clearRect(0, 0, C, C);
    if (whole) tg.drawImage(im, 0, 0, im.width, im.height, 0, 0, C, C);   // entity textures: the whole picture
    else tg.drawImage(im, 0, frame * im.width, im.width, im.width, 0, 0, C, C);
    if (tint) {
      tg.globalCompositeOperation = "multiply"; tg.fillStyle = `rgb(${tint[0]},${tint[1]},${tint[2]})`; tg.fillRect(0, 0, C, C);
      tg.globalCompositeOperation = "destination-in"; tg.drawImage(im, 0, frame * im.width, im.width, im.width, 0, 0, C, C);
    }
    return tmp;
  };
  const anims = [];                                          // the moving ones: their frames and timing (.png.mcmeta)
  for (let i = 0; i < textures.length; i++) {
    const x = (i % N) * C, y = Math.floor(i / N) * C, parts = textures[i].split("+");
    const base = await loadImage(texUrl(parts[0]));
    if (base) g.drawImage(draw(base, tints[parts[0]], parts[0].startsWith("entity/")), x, y);
    else { g.fillStyle = "#888"; g.fillRect(x, y, C, C); }
    if (base && animUrl && !parts[1] && !parts[0].startsWith("entity/") && base.height >= base.width * 2) {
      // an animation strip: its frames in order (a number, or {index, time}), each frametime ticks (default 1)
      const n = Math.floor(base.height / base.width);
      const meta = await fetch(animUrl(parts[0])).then(r => r.ok ? r.json() : null).catch(() => null);
      const a = (meta && meta.animation) || {}, ft = a.frametime || 1;
      const frames = (a.frames || [...Array(n).keys()]).map(f => typeof f === "number" ? { i: f, t: ft } : { i: f.index, t: f.time || ft })
        .filter(f => f.i >= 0 && f.i < n);
      if (frames.length > 1) anims.push({ x, y, im: base, tint: tints[parts[0]], frames, interp: !!a.interpolate,
        total: frames.reduce((s, f) => s + f.t, 0), shown: "" });
    }
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
  // close up: crisp pixels; far away and at shallow angles: mipmaps (like the game's Mipmap Levels) and anisotropic
  // filtering, so distant grass and leaves don't shimmer into stripes. Each face reads only its own cell (see
  // lightShader), so the smaller levels only blend inside the texture.
  tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestMipmapLinearFilter; tex.generateMipmaps = true;
  tex.anisotropy = 8;
  tex.colorSpace = THREE.SRGBColorSpace;
  const atlas = { tex, N, C, anims, g, draw };
  if (anims.length) ATLASES.push(atlas);
  return atlas;
}
// The game's TextureAtlasSprite animation: each tick a strip's frame by its timing; "interpolate" ones (magma,
// prismarine, sea lanterns' kin) blend into the next frame across each frame's time. Drawn into the atlas only
// when the frame shown changes.
function animateAtlases(tick) {
  for (const at of ATLASES) {
    let changed = false;
    for (const a of at.anims) {
      let t = ((tick % a.total) + a.total) % a.total, k = 0;
      while (t >= a.frames[k].t) { t -= a.frames[k].t; k++; }
      const frac = a.interp ? t / a.frames[k].t : 0, key = a.interp ? `${k}|${Math.round(frac * 8)}` : `${k}`;
      if (key === a.shown) continue;
      a.shown = key; changed = true;
      at.g.clearRect(a.x, a.y, at.C, at.C);
      at.g.drawImage(at.draw(a.im, a.tint, false, a.frames[k].i), a.x, a.y);
      if (frac > 0) {                                      // blended into the next frame
        at.g.globalAlpha = Math.round(frac * 8) / 8;
        at.g.drawImage(at.draw(a.im, a.tint, false, a.frames[(k + 1) % a.frames.length].i), a.x, a.y);
        at.g.globalAlpha = 1;
      }
    }
    if (changed) at.tex.needsUpdate = true;
  }
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
  uFancy: { value: 0.0 },          // graphics Fancy/Ultra: the sun's light and shadows do the side shading
  uGain: { value: 1.0 },           // brightness match between Off and Fancy
  uTime: { value: 0.0 },           // seconds, for the water's waves
  uSky: { value: new THREE.Color(0.75, 0.85, 1.0) },   // what the water reflects
  uClock: { value: 0.0 },          // seconds, always running (the pages set it each frame): pieces building in
};
// Graphics: "off" (Minecraft-like flat light), "fancy" (sun shadows, sky reflections on water, glow), "ultra"
const GFX = { mode: "off" };
function setLight(o = {}) {
  for (const [k, v] of Object.entries(o)) {
    const u = LIGHT["u" + k[0].toUpperCase() + k.slice(1)];
    if (!u) continue;
    if (u.value && u.value.isVector3) u.value.copy(v).normalize(); else u.value = v;
  }
}
function lightShader(sh) {
  Object.assign(sh.uniforms, LIGHT);
  // our faces are seen from their back (clockwise winding): keep the stored normal instead of flipping it per side
  sh.fragmentShader = sh.fragmentShader.replace("#include <normal_fragment_begin>",
    "vec3 normal = normalize( vNormal ); vec3 nonPerturbedNormal = normal;");
  sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nattribute vec3 aN; attribute vec3 aL; attribute vec4 aT; attribute vec4 aC; varying vec3 vN; varying vec3 vL; varying vec4 vT; varying vec4 vC; varying vec3 vBW;")
    .replace("#include <begin_vertex>", "#include <begin_vertex>\nvN = aN; vL = aL; vT = aT; vC = aC; vBW = (modelMatrix * vec4(transformed, 1.0)).xyz;");
  sh.fragmentShader = sh.fragmentShader.replace("#include <common>", `#include <common>
varying vec3 vN; varying vec3 vL; varying vec4 vT; varying vec4 vC; varying vec3 vBW;
uniform float uDay, uTorch, uBright, uExposure, uSunMix, uAO, uFancy, uGain, uTime, uClock; uniform vec3 uSunDir; uniform vec3 uSky;
uniform vec3 uRev;                 // building in: (start time, lowest y, blocks per second); see revealIn
float rvHash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
float rvLevel() { return uRev.y + (uClock - uRev.x) * uRev.z + (rvHash(floor(vBW.xz)) - 0.5) * 6.0; }`)
    // a new piece rises out of the ground column by column; above its edge nothing is drawn yet
    .replace("void main() {", "void main() {\n  if (vBW.y > rvLevel()) discard;")
    // the rising edge glows as a cyan block grid, then settles into the real blocks
    .replace("#include <opaque_fragment>", `{
    float band = 1.0 - smoothstep(0.0, 5.0, rvLevel() - vBW.y);
    if (band > 0.001) {
      vec3 fw = fwidth(vBW), fr = fract(vBW), dd = min(fr, 1.0 - fr) / max(fw, vec3(1e-4));
      dd = mix(vec3(1e3), dd, step(vec3(1e-4), fw));               // not across the face's own plane
      float line = 1.0 - clamp(min(dd.x, min(dd.y, dd.z)) - 0.5, 0.0, 1.0);
      outgoingLight = mix(outgoingLight, outgoingLight * 0.35 + vec3(0.02, 0.08, 0.1), band * 0.8);
      outgoingLight += vec3(0.25, 0.95, 1.0) * line * band * 1.6;
    }
  }
  #include <opaque_fragment>`)
    // each face reads only its own texture in the atlas: at a face's edge (antialiasing samples a little outside the
    // triangle) the lookup would otherwise catch the next texture or an empty cell, a thin light line between blocks
    .replace("#include <map_fragment>", `#ifdef USE_MAP
  vec2 cuv = vC.z > vC.x ? clamp(vMapUv, vC.xy, vC.zw) : vMapUv;
  diffuseColor *= texture2D(map, cuv);
#endif`)
    .replace("#include <color_fragment>", `#include <color_fragment>
  if (vT.a > 1.5) {                                                  // only the overlay (marked alpha 250)
    float o = 1.0 - step(0.995, diffuseColor.a);
    diffuseColor.rgb *= mix(vec3(1.0), vT.rgb, o); diffuseColor.a = 1.0;
  } else if (vT.a > 0.5) diffuseColor.rgb *= vT.rgb;               // the biome's grass, leaves or water colour
  {
    // the game's lightmap: each level through its curve l / (4 - 3l); sky light (times daylight) and block light
    // (torches, lamps: warm, the game's own tint) ADDED, then its brightness option
    float sky = vL.x / (4.0 - 3.0 * vL.x) * uDay, bl = vL.y / (4.0 - 3.0 * vL.y) * uTorch;
    vec3 blk = vec3(bl, bl * ((bl * 0.6 + 0.4) * 0.6 + 0.4), bl * (bl * bl * 0.6 + 0.4));
    vec3 c = clamp(vec3(sky) + blk, 0.0, 1.0);
    c = mix(c, 1.0 - pow(1.0 - c, vec3(4.0)), uBright);
    c = max(c, vec3(0.04));
    float side = vN.y > 0.5 ? 1.0 : vN.y < -0.5 ? 0.5 : abs(vN.z) > 0.5 ? 0.8 : 0.6;
    if (dot(vN, vN) < 0.1) side = 1.0;                             // not shaded (torches, flames)
    else side = mix(side, 0.45 + 0.55 * max(dot(vN, uSunDir), 0.0), uSunMix * (0.4 + 0.6 * uDay));
    side = mix(side, 1.0, uFancy);                                   // fancy: the lit material shades by the sun
    float ao = mix(1.0, vL.z, uAO);
#ifdef MCSM_SPLIT
    // Fancy: the sky's share goes through the sun and its shadows; the torches' share glows on its own (a torch
    // under a roof isn't in the sun's shadow)
    vec3 cs = max(mix(vec3(sky), 1.0 - pow(1.0 - vec3(sky), vec3(4.0)), uBright), vec3(0.04));
    vec3 cb = max(c - cs, vec3(0.0));
    mcsmEm = diffuseColor.rgb * pow(clamp(cb * ao * uExposure, 0.0, 4.0), vec3(2.2)) * 1.6;
    diffuseColor.rgb *= pow(clamp(cs * side * ao * uExposure, 0.0, 4.0), vec3(2.2)) * uGain;
#else
    diffuseColor.rgb *= pow(clamp(c * side * ao * uExposure, 0.0, 4.0), vec3(2.2)) * uGain;
#endif
  }`)
    .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\n#ifdef MCSM_SPLIT\n  totalEmissiveRadiance += mcsmEm;\n#endif");
  if (sh.fragmentShader.includes("totalEmissiveRadiance"))   // a lit material (Fancy): torch light as its own glow
    sh.fragmentShader = "#define MCSM_SPLIT\nvec3 mcsmEm = vec3(0.0);\n" + sh.fragmentShader;
}
// Water in Fancy: the sky reflected more at grazing angles, the sun's glint, small moving waves.
function waterShader(sh) {
  lightShader(sh);
  sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nvarying vec3 vWorld;")
    .replace("#include <begin_vertex>", "#include <begin_vertex>\nvWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;");
  sh.fragmentShader = sh.fragmentShader.replace("#include <common>", "#include <common>\nvarying vec3 vWorld;")
    .replace("#include <opaque_fragment>", `
  // water is unlit: the Fancy brightness match (uGain, for the lit blocks) would wash its colour out
  outgoingLight /= max(uGain, 1.0);
  outgoingLight *= mix(1.0, 0.8, uFancy);                           // Fancy's film-like colour lifts it: a bit deeper
  if (uFancy > 0.5 && vN.y > 0.5) {
    vec3 n = normalize(vec3(0.06 * sin(vWorld.x * 1.7 + uTime * 1.3) + 0.04 * sin(vWorld.z * 2.3 - uTime * 1.7), 1.0,
                            0.06 * cos(vWorld.z * 1.5 + uTime * 1.1) + 0.04 * cos(vWorld.x * 2.1 + uTime * 1.9)));
    vec3 v = normalize(cameraPosition - vWorld);
    float fres = pow(1.0 - max(dot(v, n), 0.0), 5.0);              // the sky only at grazing angles
    outgoingLight = mix(outgoingLight, uSky * 0.3 * (0.25 + 0.75 * uDay), 0.2 * fres);
    outgoingLight += vec3(1.0, 0.95, 0.8) * pow(max(dot(reflect(-v, n), uSunDir), 0.0), 220.0) * 2.5 * uDay;
    diffuseColor.a = mix(diffuseColor.a, 0.88, fres);
  }
  #include <opaque_fragment>`);
}
// light sources get a soft glow in Fancy (a sprite each): torches, lanterns, glowstone, fire...
const GLOW = /(^|_)(torch|lantern|glowstone|sea_lantern|shroomlight|jack_o_lantern|campfire|end_rod|froglight|beacon|fire|lava_cauldron|redstone_lamp|candle)$/;
let glowTex = null;
function glowTexture() {
  if (glowTex) return glowTex;
  const c = document.createElement("canvas"); c.width = c.height = 64;
  const g = c.getContext("2d"), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(0.25, "rgba(255,255,255,0.45)"); r.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = r; g.fillRect(0, 0, 64, 64);
  glowTex = new THREE.CanvasTexture(c); return glowTex;
}
const GLOW_MAT = new THREE.PointsMaterial({ size: 3.2, map: null, transparent: true, depthWrite: false,
  blending: THREE.AdditiveBlending, vertexColors: true, opacity: 0.6 });
function blockMaterial(kind, atlas) {
  // Off: flat (Minecraft-like). Fancy/Ultra: a lit material that takes the sun's shadows.
  const fancy = GFX.mode !== "off", Mat = fancy && kind !== "water" ? THREE.MeshLambertMaterial : THREE.MeshBasicMaterial;
  // faces are one-sided as in the game (ours wind clockwise seen from outside, so the engine's back side is the
  // outside): no flicker where two faces back onto each other. Water stays two-sided (seen from below too).
  // Cut-out textures (leaves, glass frames) fade by alpha-to-coverage, so far-off mipmaps don't pop on and off.
  // See-through blocks (stained glass, ice, slime, honey) blend, in their own layer.
  const m = kind === "water"
    ? new Mat({ map: atlas.tex, transparent: true, opacity: 0.78, depthWrite: false, side: THREE.DoubleSide })
    : kind === "glass"
      ? new Mat({ map: atlas.tex, transparent: true, depthWrite: false, alphaTest: 0.02, side: THREE.BackSide })
      : new Mat({ map: atlas.tex, alphaTest: 0.5, alphaToCoverage: true, side: THREE.BackSide });
  m.userData.rev = { value: new THREE.Vector3(-1e4, -64, 1) };      // shown whole until revealIn starts it
  m.onBeforeCompile = sh => { (kind === "water" ? waterShader : lightShader)(sh); sh.uniforms.uRev = m.userData.rev; };
  // our faces wind clockwise seen from outside, so the engine's "front" is the inside: drawing that side into the
  // shadow map keeps sunlit surfaces from shadowing themselves
  m.shadowSide = THREE.FrontSide;
  m.customProgramCacheKey = () => kind === "water" ? "mcsm-water" : kind === "glass" ? "mcsm-glass" : "mcsm-blocks";
  return m;
}
// Switch the graphics of block meshes already built (their geometry stays).
function applyGraphics(meshes, atlasTex) {
  for (const m of meshes) {
    if (m.isPoints) { m.visible = GFX.mode !== "off"; continue; }
    const kind = m.userData.kind || "solid";
    const old = m.material, mat = blockMaterial(kind, { tex: old.map || atlasTex });
    if (old.userData.rev) mat.userData.rev.value.copy(old.userData.rev.value);   // a piece building in keeps going
    m.material = mat; old.dispose();
    m.castShadow = m.receiveShadow = GFX.mode !== "off" && kind !== "water" && kind !== "glass";
  }
}
// A new piece builds in: it rises from its lowest block to its highest in about a second and a half, its top edge
// a glowing grid (LIGHT.uClock must be running).
function revealIn(meshes, seconds = 1.5) {
  for (const m of meshes) {
    if (m.isPoints || !m.material.userData.rev) continue;
    if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
    const b = m.geometry.boundingBox, lo = b.min.y - 4, span = b.max.y - lo + 8;
    m.material.userData.rev.value.set(LIGHT.uClock.value, lo, span / seconds);
  }
}
function setGraphics(mode) { GFX.mode = mode; LIGHT.uFancy.value = mode === "off" ? 0 : 1; }
const AO_LEVEL = [1.0, 0.8, 0.64, 0.5];
// The real blocks of a block list (``info`` from the server, ``buf`` its records: int16 x, y, z, dir, look; uint16
// extra) as meshes: dir 0-5 one side of a full block, 7 a whole model (see mcsm/blockmesh.py).
function blockMeshes(info, buf, atlas) {
  const REC = 20;                                       // bytes a face (blockmesh.RECORD)
  const dv = new DataView(buf), n = buf.byteLength / REC, [ox, oz] = info.origin, N = atlas.N;
  const G = () => ({ p: [], u: [], n: [], ln: [], l: [], t: [], c: [], i: [] });
  const half = 0.5 / (N * (atlas.C || 16));                 // half a texel: the cell's edge, see lightShader
  const geo = { solid: G(), water: G(), glass: G() };
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
    const axis = Math.max(Math.abs(nx), Math.abs(ny), Math.abs(nz)) > 0.99;
    const lx = axis ? nx : 0, ly = axis ? ny : 1, lz = axis ? nz : 0;
    for (let k = 0; k < 4; k++) {
      const p = P(k);
      Gr.p.push(p[0], p[1], p[2]); Gr.n.push(nx, ny, nz); Gr.ln.push(lx, ly, lz);
      const u = Math.min(1 - e, Math.max(e, q[15 + k * 2])), v = Math.min(1 - e, Math.max(e, q[16 + k * 2]));
      Gr.u.push((col + u) / N, 1 - (row + v) / N);
      Gr.l.push(typeof sky === "number" ? sky : sky[k], typeof blk === "number" ? blk : blk[k], AO_LEVEL[(ao >> (2 * k)) & 3]);
      Gr.t.push(tint[0], tint[1], tint[2], mode);
      Gr.c.push(col / N + half, 1 - (row + 1) / N + half, (col + 1) / N - half, 1 - row / N - half);
    }
    Gr.i.push(base, base + 1, base + 2, base, base + 2, base + 3);
  };
  const tints = new Map();                               // RGB565 -> linear colour
  const CS = [0, 0, 0, 0], CB = [0, 0, 0, 0];            // a face's 4 corners' sky and block light
  const glows = [];                                      // light sources: a glow each (Fancy)
  const tops = new Map();                                // open-sky tops (rain splashes): x * 65536 + z -> y
  // faces grouped by chunk, so one chunk's faces can be hidden later (a live block change drawn over it):
  // each geometry remembers where each chunk's indices are ("cx,cz" -> [start, end])
  const ck = k => `${(dv.getInt16(k * REC, true) + ox) >> 4},${(dv.getInt16(k * REC + 4, true) + oz) >> 4}`;
  const keys = new Array(n);
  for (let k = 0; k < n; k++) keys[k] = ck(k);
  const order = Array.from({ length: n }, (_, k) => k).sort((a, b) => (keys[a] < keys[b] ? -1 : keys[a] > keys[b] ? 1 : a - b));
  geo.solid.ranges = new Map(); geo.water.ranges = new Map(); geo.glass.ranges = new Map();
  geo.solid.blocks = new Map();                          // chests: "x,y,z" -> their index ranges (drawn on their own when open)
  let curKey = null;
  const close = () => { if (curKey === null) return;
    for (const Gr of [geo.solid, geo.water, geo.glass]) { const r = Gr.ranges.get(curKey); if (r) r[1] = Gr.i.length; } };
  for (const k of order) {
    if (keys[k] !== curKey) {
      close(); curKey = keys[k];
      for (const Gr of [geo.solid, geo.water, geo.glass]) Gr.ranges.set(curKey, [Gr.i.length, Gr.i.length]);
    }
    const o = k * REC, x = dv.getInt16(o, true) + ox, y = dv.getInt16(o + 2, true), z = dv.getInt16(o + 4, true) + oz;
    const d = dv.getInt16(o + 6, true), b = info.infos[dv.getInt16(o + 8, true)], ex = dv.getUint16(o + 10, true);
    const t5 = dv.getUint16(o + 12, true);
    if (!b) continue;
    let tint = tints.get(t5);
    if (!tint) { tint = [lin(((t5 >> 11) & 31) / 31), lin(((t5 >> 5) & 63) / 63), lin((t5 & 31) / 31)]; tints.set(t5, tint); }
    const sky = ((ex >> 12) & 15) / 15, blk = ((ex >> 8) & 15) / 15;
    // smooth light: each corner's sky and block light (sums of 4 cells, 0..60), 12 bits a corner in 48 bits
    const lo = dv.getUint32(o + 14, true), hi = dv.getUint16(o + 18, true);
    const cw = [lo & 0xfff, (lo >>> 12) & 0xfff, ((lo >>> 24) | (hi << 8)) & 0xfff, (hi >>> 4) & 0xfff];
    for (let c = 0; c < 4; c++) { CS[c] = (cw[c] & 63) / 60; CB[c] = (cw[c] >> 6) / 60; }
    const Gr = b.kind === "water" ? geo.water : b.translucent ? geo.glass : geo.solid;
    if (d === 0 && sky === 1) { const tk = x * 65536 + z, ty = y + (b.kind === "water" ? 0.9 : 1); if (!(tops.get(tk) >= ty)) tops.set(tk, ty); }
    if (GLOW.test(b.name) && (d === 7 || d === 0)) {
      const soul = b.name.startsWith("soul_");
      glows.push(x + 0.5, y + (d === 7 ? 0.6 : 1.05), z + 0.5, soul ? 0.35 : 1.0, soul ? 0.8 : 0.72, soul ? 1.0 : 0.38);
    }
    const chest = Gr === geo.solid && /chest$|shulker_box$/.test(b.name), i0 = Gr.i.length;
    if (d === 7) {                                        // a whole model: its quads not hidden by a neighbour
      const mask = ex & 63;
      for (const q of b.quads) if (q[1] < 0 || !((mask >> q[1]) & 1)) quad(Gr, q, x, y, z, sky, blk, 0, tint);
    } else if (b.quads[d]) quad(Gr, b.quads[d], x, y, z, CS, CB, ex & 255, tint);
    if (chest && Gr.i.length > i0) {
      const bk = `${x},${y},${z}`;
      if (!Gr.blocks.has(bk)) Gr.blocks.set(bk, []);
      Gr.blocks.get(bk).push([i0, Gr.i.length]);
    }
  }
  close();
  const mesh = (Gr, mat) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(Gr.p, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(Gr.u, 2));
    g.setAttribute("aN", new THREE.Float32BufferAttribute(Gr.n, 3));
    // the lit material (Fancy) shades with "normal": a face's outward normal; plants and other slanted or unshaded
    // quads are lit as if facing up (like the game lights plants evenly). See lightShader: no back-face flip.
    g.setAttribute("normal", new THREE.Float32BufferAttribute(Gr.ln, 3));
    g.setAttribute("aL", new THREE.Float32BufferAttribute(Gr.l, 3));
    g.setAttribute("aT", new THREE.Float32BufferAttribute(Gr.t, 4));
    g.setAttribute("aC", new THREE.Float32BufferAttribute(Gr.c, 4));
    g.setIndex(Gr.i);
    g.computeBoundingSphere();
    const me = new THREE.Mesh(g, mat);
    me.userData.kind = mat.transparent ? "water" : "solid";
    me.userData.chunks = Gr.ranges;
    if (Gr.blocks) me.userData.blocks = Gr.blocks;
    me.castShadow = me.receiveShadow = GFX.mode !== "off" && !mat.transparent;
    return me;
  };
  const out = [mesh(geo.solid, blockMaterial("solid", atlas))];
  out[0].userData.fx = info.fx && info.fx.length ? { list: info.fx, codes: info.fxk || [] } : null;   // particle sources
  out[0].userData.tops = tops;
  if (geo.water.i.length) out.push(mesh(geo.water, blockMaterial("water", atlas)));
  if (geo.glass.i.length) { const gm = mesh(geo.glass, blockMaterial("glass", atlas)); gm.userData.kind = "glass"; gm.renderOrder = 1; out.push(gm); }
  if (glows.length) {
    const g = new THREE.BufferGeometry(), n6 = glows.length / 6, pos = new Float32Array(n6 * 3), col = new Float32Array(n6 * 3);
    for (let k = 0; k < n6; k++) { pos.set(glows.slice(k * 6, k * 6 + 3), k * 3); col.set(glows.slice(k * 6 + 3, k * 6 + 6), k * 3); }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.setAttribute("color", new THREE.BufferAttribute(col, 3));
    if (!GLOW_MAT.map) GLOW_MAT.map = glowTexture();
    const pts = new THREE.Points(g, GLOW_MAT); pts.visible = GFX.mode !== "off"; pts.userData.kind = "glow";
    out.push(pts);
  }
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
export { init, loadTerrain, loadBlocks, resize, render, clearWorld, lookFrom, view, renderFree, buildAtlas, animateAtlases, blockMeshes, setLight, LIGHT, revealIn,
         GFX, GLOW_MAT, setGraphics, applyGraphics };
