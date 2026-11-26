// Builds real blocks' meshes off the page's thread (see meshcore.js); the arrays go back without copying.
import { buildFaces } from "./meshcore.js";
self.onmessage = ev => {
  const { id, info, buf, N, C } = ev.data;
  try {
    const out = buildFaces(info, buf, N, C), moved = [];
    for (const Gr of Object.values(out.geo)) for (const k of ["p", "u", "n", "ln", "l", "t", "c", "i"]) moved.push(Gr[k].buffer);
    self.postMessage({ id, out }, moved);
  } catch (e) { self.postMessage({ id, error: String(e && e.message || e) }); }
};
