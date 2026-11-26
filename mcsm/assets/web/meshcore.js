// The real blocks' faces (blockmesh's records) -> the arrays of their meshes, without three.js: used on the page
// (map3d.js) and in a worker (meshworker.js), so building a piece needn't hold up the page's frames.
export const GLOW = /(^|_)(torch|lantern|glowstone|sea_lantern|shroomlight|jack_o_lantern|campfire|end_rod|froglight|beacon|fire|lava_cauldron|redstone_lamp|candle)$/;
export const AO_LEVEL = [1.0, 0.8, 0.64, 0.5];
export function buildFaces(info, buf, N, C) {
  // Built straight into typed arrays sized beforehand (a first pass counts each geometry's quads), faces grouped by
  // chunk with numeric keys, each block kind's tests worked out once: several times faster than growing arrays.
  const REC = 20;                                       // bytes a face (blockmesh.RECORD)
  const dv = new DataView(buf), n = buf.byteLength / REC, [ox, oz] = info.origin;
  const half = 0.5 / (N * (C || 16));                 // half a texel: the cell's edge, see lightShader
  const e = 0.001;
  const lin = v => Math.pow(v, 2.2);
  // per block kind: which geometry (0 solid, 1 water, 2 glass), glowing, a chest
  const infos = info.infos, nk = infos.length;
  const kGeo = new Uint8Array(nk), kGlow = new Uint8Array(nk), kChest = new Uint8Array(nk);
  for (let j = 0; j < nk; j++) { const b = infos[j]; if (!b) continue;
    kGeo[j] = b.kind === "water" ? 1 : b.translucent ? 2 : 0; kGlow[j] = GLOW.test(b.name) ? 1 : 0;
    kChest[j] = kGeo[j] === 0 && /chest$|shulker_box$/.test(b.name) ? 1 : 0; }
  // pass 1: quads per geometry (upper bound) and each face's chunk
  const cnt = [0, 0, 0], ckey = new Float64Array(n);
  for (let k = 0; k < n; k++) {
    const o = k * REC, b = infos[dv.getInt16(o + 8, true)];
    ckey[k] = (((dv.getInt16(o, true) + ox) >> 4) + 2097152) * 4194304 + (((dv.getInt16(o + 4, true) + oz) >> 4) + 2097152);
    if (!b) continue;
    const d = dv.getInt16(o + 6, true);
    cnt[kGeo[dv.getInt16(o + 8, true)]] += d === 7 ? b.quads.length : (b.quads[d] ? 1 : 0);
  }
  const G = q => ({ p: new Float32Array(q * 12), u: new Float32Array(q * 8), n: new Float32Array(q * 12), ln: new Float32Array(q * 12),
    l: new Float32Array(q * 12), t: new Float32Array(q * 16), c: new Float32Array(q * 16), i: new Uint32Array(q * 6), v: 0, ii: 0 });
  const geo = { solid: G(cnt[0]), water: G(cnt[1]), glass: G(cnt[2]) }, GS = [geo.solid, geo.water, geo.glass];
  const quad = (Gr, q, x, y, z, sky, blk, ao, tint) => {
    const mode = q[23] || 0;
    const base = Gr.v, t = q[0], col = t % N, row = Math.floor(t / N);
    let nx = 0, ny = 0, nz = 0;
    if (q[2]) {                                          // normal = down x right (the face points outwards)
      const ax = q[6] - q[3], ay = q[7] - q[4], az = q[8] - q[5], bx = q[12] - q[3], by = q[13] - q[4], bz = q[14] - q[5];
      nx = by * az - bz * ay; ny = bz * ax - bx * az; nz = bx * ay - by * ax;
      const len = Math.hypot(nx, ny, nz) || 1; nx /= len; ny /= len; nz /= len;
    }
    const axis = Math.max(Math.abs(nx), Math.abs(ny), Math.abs(nz)) > 0.99;
    const lx = axis ? nx : 0, ly = axis ? ny : 1, lz = axis ? nz : 0;
    const c0 = col / N + half, c1 = 1 - (row + 1) / N + half, c2 = (col + 1) / N - half, c3 = 1 - row / N - half;
    const sn = typeof sky === "number", bn = typeof blk === "number";
    for (let k = 0; k < 4; k++) {
      const v3 = (base + k) * 3, v2 = (base + k) * 2, v4 = (base + k) * 4;
      Gr.p[v3] = x + q[3 + k * 3]; Gr.p[v3 + 1] = y + q[4 + k * 3]; Gr.p[v3 + 2] = z + q[5 + k * 3];
      Gr.n[v3] = nx; Gr.n[v3 + 1] = ny; Gr.n[v3 + 2] = nz; Gr.ln[v3] = lx; Gr.ln[v3 + 1] = ly; Gr.ln[v3 + 2] = lz;
      const u = Math.min(1 - e, Math.max(e, q[15 + k * 2])), v = Math.min(1 - e, Math.max(e, q[16 + k * 2]));
      Gr.u[v2] = (col + u) / N; Gr.u[v2 + 1] = 1 - (row + v) / N;
      Gr.l[v3] = sn ? sky : sky[k]; Gr.l[v3 + 1] = bn ? blk : blk[k]; Gr.l[v3 + 2] = AO_LEVEL[(ao >> (2 * k)) & 3];
      Gr.t[v4] = tint[0]; Gr.t[v4 + 1] = tint[1]; Gr.t[v4 + 2] = tint[2]; Gr.t[v4 + 3] = mode;
      Gr.c[v4] = c0; Gr.c[v4 + 1] = c1; Gr.c[v4 + 2] = c2; Gr.c[v4 + 3] = c3;
    }
    const I = Gr.ii;
    Gr.i[I] = base; Gr.i[I + 1] = base + 1; Gr.i[I + 2] = base + 2; Gr.i[I + 3] = base; Gr.i[I + 4] = base + 2; Gr.i[I + 5] = base + 3;
    Gr.ii += 6; Gr.v += 4;
  };
  const tints = new Map();                               // RGB565 -> linear colour
  const CS = [0, 0, 0, 0], CB = [0, 0, 0, 0];            // a face's 4 corners' sky and block light
  const glows = [];                                      // light sources: a glow each (Fancy)
  const tops = new Map();                                // open-sky tops (rain splashes): x * 65536 + z -> y
  const ups = new Map();                                 // blocks with a top showing (what's walked on): x * 65536 + z -> [y, name, ...]
  // faces grouped by chunk, so one chunk's faces can be hidden later (a live block change drawn over it):
  // each geometry remembers where each chunk's indices are ("cx,cz" -> [start, end]); within a chunk, file order
  const order = new Uint32Array(n);
  for (let k = 0; k < n; k++) order[k] = k;
  order.sort((p0, p1) => (ckey[p0] - ckey[p1]) || (p0 - p1));
  for (const Gr of GS) Gr.ranges = new Map();
  geo.solid.blocks = new Map();                          // chests: "x,y,z" -> their index ranges (drawn on their own when open)
  let curKey = null, curNum = NaN;
  const close = () => { if (curKey === null) return;
    for (const Gr of GS) { const r = Gr.ranges.get(curKey); if (r) r[1] = Gr.ii; } };
  for (let oi = 0; oi < n; oi++) {
    const k = order[oi];
    if (ckey[k] !== curNum) {
      close(); curNum = ckey[k]; curKey = `${Math.floor(curNum / 4194304) - 2097152},${(curNum % 4194304) - 2097152}`;
      for (const Gr of GS) Gr.ranges.set(curKey, [Gr.ii, Gr.ii]);
    }
    const o = k * REC, x = dv.getInt16(o, true) + ox, y = dv.getInt16(o + 2, true), z = dv.getInt16(o + 4, true) + oz;
    const d = dv.getInt16(o + 6, true), bi = dv.getInt16(o + 8, true), b = infos[bi], ex = dv.getUint16(o + 10, true);
    const t5 = dv.getUint16(o + 12, true);
    if (!b) continue;
    let tint = tints.get(t5);
    if (!tint) { tint = [lin(((t5 >> 11) & 31) / 31), lin(((t5 >> 5) & 63) / 63), lin((t5 & 31) / 31)]; tints.set(t5, tint); }
    const sky = ((ex >> 12) & 15) / 15, blk = ((ex >> 8) & 15) / 15;
    // smooth light: each corner's sky and block light (sums of 4 cells, 0..60), 12 bits a corner in 48 bits
    const lo = dv.getUint32(o + 14, true), hi = dv.getUint16(o + 18, true);
    const cw0 = lo & 0xfff, cw1 = (lo >>> 12) & 0xfff, cw2 = ((lo >>> 24) | (hi << 8)) & 0xfff, cw3 = (hi >>> 4) & 0xfff;
    CS[0] = (cw0 & 63) / 60; CB[0] = (cw0 >> 6) / 60; CS[1] = (cw1 & 63) / 60; CB[1] = (cw1 >> 6) / 60;
    CS[2] = (cw2 & 63) / 60; CB[2] = (cw2 >> 6) / 60; CS[3] = (cw3 & 63) / 60; CB[3] = (cw3 >> 6) / 60;
    const g = kGeo[bi], Gr = GS[g];
    if (d === 0 && sky === 1) { const tk = x * 65536 + z, ty = y + (g === 1 ? 0.9 : 1); if (!(tops.get(tk) >= ty)) tops.set(tk, ty); }
    if ((d === 0 || d === 7) && g !== 1) { const tk = x * 65536 + z; let u = ups.get(tk); if (!u) ups.set(tk, u = []);
      if (u[u.length - 2] !== y) u.push(y, b.name); }
    if (kGlow[bi] && (d === 7 || d === 0)) {
      const soul = b.name.startsWith("soul_");
      glows.push(x + 0.5, y + (d === 7 ? 0.6 : 1.05), z + 0.5, soul ? 0.35 : 1.0, soul ? 0.8 : 0.72, soul ? 1.0 : 0.38);
    }
    const i0 = Gr.ii;
    if (d === 7) {                                        // a whole model: its quads not hidden by a neighbour
      const mask = ex & 63;
      for (const q of b.quads) if (q[1] < 0 || !((mask >> q[1]) & 1)) quad(Gr, q, x, y, z, sky, blk, 0, tint);
    } else if (b.quads[d]) quad(Gr, b.quads[d], x, y, z, CS, CB, ex & 255, tint);
    if (kChest[bi] && Gr.ii > i0) {
      const bk = `${x},${y},${z}`;
      if (!Gr.blocks.has(bk)) Gr.blocks.set(bk, []);
      Gr.blocks.get(bk).push([i0, Gr.ii]);
    }
  }
  close();
  return { geo, tops, ups, glows };
}
