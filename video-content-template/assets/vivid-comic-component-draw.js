/* Bright-comic semantic painting: exclusive objects and complete brush footprints.
 * Drawing order, timings, brush motion and card runtime follow the accepted
 * sponge component renderer. Original sponge runtime is left unchanged. */
(function () {
  "use strict";
  const clamp = x => Math.max(0, Math.min(1, x));
  const legacyDraw = window.installSpongeRegionDraw;
  const canvasOf = (w, h) => Object.assign(document.createElement("canvas"), { width: w, height: h });

  function pathOf(polygons, w, h) {
    const path = new Path2D();
    polygons.forEach(poly => {
      poly.forEach(([x, y], i) => i ? path.lineTo(x * w, y * h) : path.moveTo(x * w, y * h));
      path.closePath();
    });
    return path;
  }

  function semanticPlan(scene, plan) {
    const entry = window.__vividComicSemanticParts?.[scene.id];
    if (window.__vividComicSemanticParts && !entry) throw new Error(`comic semantic partition missing scene: ${scene.id}`);
    if (!entry) return { plan, entry: null };
    if (!/^[a-f0-9]{64}$/i.test(entry.source_sha256 || "") || entry.source_sha256 !== plan.source_sha256) {
      throw new Error(`comic semantic source hash mismatch: ${scene.id}`);
    }
    if (!Array.isArray(entry.parts) || !entry.parts.length || entry.parts.length > 255 ||
      entry.parts.some(p => !p.id || !Number.isFinite(p.weight) || p.weight <= 0) ||
      new Set(entry.parts.map(p => p.id)).size !== entry.parts.length) {
      throw new Error(`comic semantic parts invalid: ${scene.id}`);
    }
    return { plan: { ...plan, parts: entry.parts }, entry };
  }

  function decodeOwners(entry, w, h) {
    if (entry.width !== w || entry.height !== h) throw new Error("comic semantic image dimensions mismatch");
    const binary = atob(entry.ownership_rle);
    if (!binary.length || binary.length % 5) throw new Error("comic semantic ownership RLE length invalid");
    const bytes = Uint8Array.from(binary, c => c.charCodeAt(0)), runs = new DataView(bytes.buffer);
    const owners = new Uint8Array(w * h);
    let position = 0;
    for (let i = 0; i < bytes.length; i += 5) {
      const count = runs.getUint32(i, true), owner = bytes[i + 4];
      if (!count || owner > entry.parts.length || position + count > owners.length) {
        throw new Error("comic semantic ownership RLE run invalid");
      }
      owners.fill(owner, position, position + count); position += count;
    }
    if (position !== owners.length) throw new Error("comic semantic ownership RLE is incomplete");
    return owners;
  }

  function buildLayers(source, plan, semanticEntry) {
    const w = source.naturalWidth, h = source.naturalHeight;
    const probe = canvasOf(w, h), ctx = probe.getContext("2d", { willReadFrequently: true });
    // Validate and paint the visible colour on the actual white canvas. A
    // translucent dark shadow is visible even when its source alpha is <128.
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, w, h);
    ctx.drawImage(source, 0, 0);
    const src = ctx.getImageData(0, 0, w, h).data;
    // Each pixel receives an integer owner, not an antialiased RGB label.
    // RGB labels blend at polygon joins and can become an unrelated part ID.
    // Independent alpha masks preserve union edges and the existing later-part
    // reservation rule without punching transparent seams through completed art.
    const owners = semanticEntry ? decodeOwners(semanticEntry, w, h) : new Uint8Array(w * h);
    if (!semanticEntry) plan.parts.forEach((part, i) => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#fff";
      ctx.fill(pathOf(part.polygons, w, h));
      const mask = ctx.getImageData(0, 0, w, h).data;
      for (let p = 0; p < owners.length; p++) if (mask[p * 4 + 3]) owners[p] = i + 1;
    });
    // Complete pale fills and narrow gaps omitted by the geometry's dark-ink
    // threshold. Extend the nearest existing semantic owner; do not introduce
    // a final-frame overlay or expose future props in the character layer.
    const nearest = semanticEntry ? null : owners.slice(), queue = semanticEntry ? null : new Uint32Array(w * h);
    let head = 0, tail = 0;
    if (nearest) for (let p = 0; p < nearest.length; p++) if (nearest[p]) queue[tail++] = p;
    if (nearest && !tail) throw new Error("comic drawing has no semantic seed pixels");
    while (head < tail) {
      const p = queue[head++], x = p % w;
      const visit = q => { if (!nearest[q]) { nearest[q] = nearest[p]; queue[tail++] = q; } };
      if (x) visit(p - 1);
      if (x + 1 < w) visit(p + 1);
      if (p >= w) visit(p - w);
      if (p + w < nearest.length) visit(p + w);
    }
    let uncovered = 0, ink = 0, palePixelsRecovered = 0;
    const bounds = plan.parts.map(() => [w, h, -1, -1]);
    const excludedNearWhite = plan.parts.map(() => 0);
    for (let p = 0; p < owners.length; p++) {
      const at = p * 4;
      const foreground = src[at + 3] > 127 && Math.min(src[at], src[at + 1], src[at + 2]) < 252;
      if (!owners[p] && foreground && nearest) { owners[p] = nearest[p]; palePixelsRecovered++; }
      if (foreground) { ink++; if (!owners[p]) uncovered++; }
      if (owners[p] && src[at + 3] && Math.min(src[at], src[at + 1], src[at + 2]) >= 252) excludedNearWhite[owners[p] - 1]++;
      if (owners[p] && src[at + 3] && Math.min(src[at], src[at + 1], src[at + 2]) < 252) {
        const b = bounds[owners[p] - 1], x = p % w, y = Math.floor(p / w);
        b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y);
        b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y);
      }
    }
    if (semanticEntry && uncovered) throw new Error(`comic semantic partition omits ${uncovered} visible pixels`);
    const layers = plan.parts.map((part, i) => {
      const b = bounds[i];
      if (b[2] < 0) throw new Error(`comic semantic part has no pixels: ${part.id}`);
      const x0 = Math.max(0, b[0] - 2), y0 = Math.max(0, b[1] - 2);
      const x1 = Math.min(w, Math.max(x0 + 1, b[2] + 3));
      const y1 = Math.min(h, Math.max(y0 + 1, b[3] + 3));
      const image = canvasOf(x1 - x0, y1 - y0), out = image.getContext("2d");
      const data = out.createImageData(image.width, image.height);
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
        const at = (y * w + x) * 4, to = ((y - y0) * image.width + x - x0) * 4;
        if (owners[y * w + x] === i + 1 && Math.min(src[at], src[at + 1], src[at + 2]) < 252) {
          data.data[to] = src[at]; data.data[to + 1] = src[at + 1];
          data.data[to + 2] = src[at + 2]; data.data[to + 3] = src[at + 3];
        }
      }
      out.putImageData(data, 0, 0);
      const width = part.kind === "text" ? 13 : part.kind === "motion" ? 12 : 25;
      const step = width * .65, strokes = [];
      let length = 0;
      // Disconnected islands are visited in order. Scanning the whole part's
      // bounding box would draw the tops of several independent objects at once.
      const islands = new Uint32Array(image.width * image.height), pending = new Uint32Array(islands.length), groups = [];
      for (let seed = 0; seed < islands.length; seed++) {
        if (islands[seed] || !data.data[seed * 4 + 3]) continue;
        const id = groups.length + 1, box = [image.width, image.height, -1, -1];
        let read = 0, write = 1; pending[0] = seed; islands[seed] = id;
        while (read < write) {
          const p = pending[read++], x = p % image.width, y = Math.floor(p / image.width);
          box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], y); box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], y);
          for (let yy = Math.max(0, y - 1); yy <= Math.min(image.height - 1, y + 1); yy++) {
            for (let xx = Math.max(0, x - 1); xx <= Math.min(image.width - 1, x + 1); xx++) {
              const next = yy * image.width + xx;
              if (!islands[next] && data.data[next * 4 + 3]) { islands[next] = id; pending[write++] = next; }
            }
          }
        }
        groups.push({ id, box, pixels: write });
      }
      groups.sort((a, b) => a.box[1] - b.box[1] || a.box[0] - b.box[0]);
      // Every owned pixel drives the path, including pale fills and white object
      // edges. Paper at >=252 differs from the white canvas by at most 3 and is
      // excluded explicitly, so almost-white backgrounds never drive the brush.
      for (const group of groups) {
       const [gx0, gy0, gx1, gy1] = group.box;
       group.start = length;
       for (let band = 0, y = gy0 + width / 3; y < gy1 + 1 + step; y += step, band++) {
        const center = Math.min(gy1, Math.round(y));
        const runs = []; let begin = null, last = null;
        for (let x = gx0; x <= gx1; x++) {
          let found = false;
          for (let yy = Math.max(0, center - Math.ceil(step / 2)); yy <= Math.min(image.height - 1, center + Math.ceil(step / 2)); yy++) {
            if (islands[yy * image.width + x] === group.id) { found = true; break; }
          }
          if (found) { if (begin === null) begin = x; last = x; }
          if (begin !== null && ((!found && x - last > width) || x === gx1)) {
            runs.push([Math.max(0, begin - width / 2), Math.min(image.width, last + width / 2)]); begin = null;
          }
        }
        if (band % 2) runs.reverse();
        runs.forEach(([a, b]) => {
          const from = [band % 2 ? b : a, center], to = [band % 2 ? a : b, center];
          const size = Math.max(1, Math.abs(b - a));
          strokes.push({ from, to, start: length, end: length + size, island: group.id }); length += size;
        });
       }
       group.end = length;
      }
      // Record the FIRST distance at which a round brush touches each pixel.
      // A pixel becomes its exact source colour at that instant and never
      // changes again; independent alpha-rasterized masks would repeatedly
      // soften old edges as new passes overlap them.
      const ranks = new Float32Array(image.width * image.height); ranks.fill(Infinity);
      const radius = width / 2, radius2 = radius * radius;
      const candidateStrokes = strokes.splice(0); length = 0;
      for (const candidate of candidateStrokes) {
        const stroke = { ...candidate, start: length, end: length + candidate.end - candidate.start };
        let newlyCovered = 0;
        const direction = stroke.to[0] >= stroke.from[0] ? 1 : -1;
        const lo = Math.max(0, Math.floor(Math.min(stroke.from[0], stroke.to[0]) - radius));
        const hi = Math.min(image.width - 1, Math.ceil(Math.max(stroke.from[0], stroke.to[0]) + radius));
        const top = Math.max(0, Math.floor(stroke.from[1] - radius));
        const bottom = Math.min(image.height - 1, Math.ceil(stroke.from[1] + radius));
        for (let y = top; y <= bottom; y++) {
          const dy = y + .5 - stroke.from[1], inside = radius2 - dy * dy;
          if (inside < 0) continue;
          const cap = Math.sqrt(inside);
          for (let x = lo; x <= hi; x++) {
            const p = y * image.width + x;
            // A brush touches every pixel of this semantic owner under its tip,
            // including nearby disconnected pale flecks. Restricting touches
            // to the currently traversed island leaves holes behind the brush.
            if (!data.data[p * 4 + 3] || Number.isFinite(ranks[p])) continue;
            const along = direction * (x + .5 - stroke.from[0]);
            if (along < -cap || along > stroke.end - stroke.start + cap) continue;
            const distance = stroke.start + Math.max(0, along - cap);
            ranks[p] = distance; newlyCovered++;
          }
        }
        // Do not spend time visiting tiny islands already painted by an earlier
        // neighbouring pass. Object ownership still prevents any future leak.
        if (newlyCovered) { strokes.push(stroke); length = stroke.end; }
      }
      for (const group of groups) {
        const ownStrokes = strokes.filter(stroke => stroke.island === group.id);
        group.start = ownStrokes[0]?.start ?? null;
        group.end = ownStrokes[ownStrokes.length - 1]?.end ?? null;
        group.coveredByNeighbouringPass = !ownStrokes.length;
      }
      let ownedPixels = 0, missingBrushPixels = 0, palePixels = 0;
      for (let p = 0; p < ranks.length; p++) if (data.data[p * 4 + 3]) {
        ownedPixels++;
        if (!Number.isFinite(ranks[p]) || ranks[p] > length) missingBrushPixels++;
        if (Math.min(data.data[p * 4], data.data[p * 4 + 1], data.data[p * 4 + 2]) >= 246) palePixels++;
      }
      if (missingBrushPixels) throw new Error(`comic brush misses ${missingBrushPixels} pixels in ${part.id}`);
      const order = new Uint32Array(ownedPixels);
      for (let p = 0, at = 0; p < ranks.length; p++) if (data.data[p * 4 + 3]) order[at++] = p;
      order.sort((a, b) => ranks[a] - ranks[b] || a - b);
      const scratch = canvasOf(image.width, image.height), scratchContext = scratch.getContext("2d");
      const revealed = scratchContext.createImageData(image.width, image.height);
      return { image, x: x0, y: y0, strokes, length, width, scratch, scratchContext,
        sourcePixels: new Uint32Array(data.data.buffer), revealed, revealedPixels: new Uint32Array(revealed.data.buffer),
        ranks, order, islands, groups, visibleCount: 0, lastLimit: -1, part,
        coverage: { ownedPixels, palePixels, excludedNearWhitePixels: excludedNearWhite[i], missingBrushPixels,
          strokeCount: strokes.length, islandCount: groups.length,
          finalPixelDistance: ownedPixels ? ranks[order[ownedPixels - 1]] : 0, pathLength: length } };
    });
    return { layers, owners, sourcePixels: src, width: w, height: h,
      coverage: { ink, uncovered, ratio: ink ? 1 - uncovered / ink : 0, threshold: 252, palePixelsRecovered,
        ownership: semanticEntry ? "explicit-semantic-rle-v2" : "integer-semantic-partition-v1",
        brushCoverage: "first-touch-source-pixels-v2", excludedNearWhitePixels: excludedNearWhite.reduce((n, value) => n + value, 0),
        nearWhiteMaxChannelDifference: 3, missingBrushPixels: layers.reduce((n, l) => n + l.coverage.missingBrushPixels, 0) } };
  }

  function paintLayer(context, layer, progress) {
    if (progress <= 1e-9) return null;
    const limit = layer.length * progress;
    if (limit < layer.lastLimit) {
      layer.revealedPixels.fill(0); layer.scratchContext.clearRect(0, 0, layer.image.width, layer.image.height); layer.visibleCount = 0;
    }
    layer.lastLimit = limit;
    let low = layer.visibleCount, high = layer.order.length;
    while (low < high) { const middle = (low + high) >>> 1; if (layer.ranks[layer.order[middle]] <= limit) low = middle + 1; else high = middle; }
    let left = layer.image.width, top = layer.image.height, right = -1, bottom = -1;
    for (let i = layer.visibleCount; i < low; i++) {
      const p = layer.order[i], x = p % layer.image.width, y = Math.floor(p / layer.image.width);
      layer.revealedPixels[p] = layer.sourcePixels[p];
      left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
    layer.visibleCount = low;
    if (right >= left) layer.scratchContext.putImageData(layer.revealed, 0, 0, left, top, right - left + 1, bottom - top + 1);
    context.drawImage(layer.scratch, layer.x, layer.y);
    if (progress >= 1 - 1e-8) return null;
    let strokeIndex = 0;
    while (strokeIndex + 1 < layer.strokes.length && layer.strokes[strokeIndex].end < limit) strokeIndex++;
    const stroke = layer.strokes[strokeIndex];
    if (!stroke) return null;
    const ratio = clamp((limit - stroke.start) / (stroke.end - stroke.start));
    return [stroke.from[0] + (stroke.to[0] - stroke.from[0]) * ratio + layer.x, stroke.from[1] + layer.y];
  }

  function installDrawing(tl, scene, plan) {
    const semantic = semanticPlan(scene, plan); plan = semantic.plan;
    const root = document.getElementById(scene.id);
    const source = document.getElementById(scene.id + "--draw-source");
    const canvas = document.getElementById(scene.id + "--draw-canvas");
    const cursor = document.getElementById(scene.id + "--pencil-cursor");
    const state = { elapsed: 0 };
    const duration = Math.min(19, Math.max(7, (scene.end - scene.start) * .70));
    const start = scene.start + .30, gap = .22;
    const weight = plan.parts.reduce((n, p) => n + p.weight, 0);
    let at = 0;
    const schedule = plan.parts.map((part, i) => {
      const length = (duration - gap * (plan.parts.length - 1)) * part.weight / weight;
      const result = { id: part.id, start: at, end: at + length }; at += length + (i < plan.parts.length - 1 ? gap : 0); return result;
    });
    let built = null;
    function tipFor(layer, last) {
      const stroke = last ? layer.strokes[layer.strokes.length - 1] : layer.strokes[0];
      const p = stroke ? (last ? stroke.to : stroke.from) : [0, 0];
      return [p[0] + layer.x, p[1] + layer.y];
    }
    function paint() {
      if (!built) return;
      const ctx = canvas.getContext("2d");
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      let tip = null, lifted = false;
      built.layers.forEach((layer, i) => {
        const entry = schedule[i];
        // GSAP rounds animated numeric state to six decimals. At a part's exact
        // start that can become a tiny positive value and expose rank-zero ink.
        const local = state.elapsed - entry.start;
        const progress = local <= 1e-6 ? 0 : clamp(local / (entry.end - entry.start));
        const activeTip = paintLayer(ctx, layer, progress);
        if (activeTip) tip = activeTip;
        if (i < schedule.length - 1 && state.elapsed >= entry.end && state.elapsed < schedule[i + 1].start) {
          const p = clamp((state.elapsed - entry.end) / gap), a = tipFor(layer, true), b = tipFor(built.layers[i + 1], false);
          const ease = p * p * (3 - 2 * p);
          tip = [a[0] + (b[0] - a[0]) * ease, a[1] + (b[1] - a[1]) * ease - Math.sin(p * Math.PI) * 30]; lifted = true;
        }
      });
      const active = state.elapsed > 0 && state.elapsed < duration && tip;
      cursor.style.opacity = active ? (lifted ? ".62" : "1") : "0";
      if (active) {
        const scaleX = canvas.clientWidth / canvas.width, scaleY = canvas.clientHeight / canvas.height;
        cursor.style.transform = `translate(${tip[0] * scaleX - 35.5}px,${tip[1] * scaleY - 77}px) rotate(${lifted ? -5 : 0}deg) scale(${lifted ? 1.035 : 1})`;
      }
      root.dataset.paintingPart = schedule.find(x => state.elapsed >= x.start && state.elapsed < x.end)?.id || (state.elapsed >= duration ? "complete" : "lift");
    }
    function load() {
      if (built || !source.naturalWidth) return;
      built = buildLayers(source, plan, semantic.entry);
      canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
      root.dataset.paintCoverage = String(built.coverage.ratio);
      window.__spongePaintingQC = window.__spongePaintingQC || {};
      window.__spongePaintingQC[scene.id] = { ...built.coverage, start, duration, schedule,
        parts: built.layers.map(l => ({ id: l.part.id, kind: l.part.kind, ...l.coverage })) };
      window.__vividComicPaintingDebug = window.__vividComicPaintingDebug || {};
      window.__vividComicPaintingDebug[scene.id] = { ...built, repaint: paint };
      paint();
    }
    // Timeline registration is synchronous. Asset load only fills the image cache.
    tl.fromTo(state, { elapsed: 0 }, { elapsed: duration, duration, ease: "none", immediateRender: false, onUpdate: paint }, start);
    source.addEventListener("load", load, { once: true }); if (source.complete) load();
    window.__spongeRepaint = window.__spongeRepaint || []; window.__spongeRepaint.push(paint);
  }

  window.installSpongeRegionDraw = function (tl, scenes, cues, ids) {
    const old = new Set();
    scenes.filter(s => ids.has(s.id)).forEach(scene => {
      const encoded = document.getElementById(scene.id).dataset.drawingPlan;
      if (encoded) installDrawing(tl, scene, JSON.parse(encoded)); else old.add(scene.id);
    });
    if (old.size) legacyDraw(tl, scenes, cues, old);
  };

})();
