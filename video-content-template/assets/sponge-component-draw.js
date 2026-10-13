/* Sequential semantic painting. Every source pixel has one immutable owner. */
(function () {
  "use strict";
  const clamp = x => Math.max(0, Math.min(1, x));
  const legacyDraw = window.installSpongeRegionDraw;
  const legacyCards = window.installSpongeInfoCards;
  const canvasOf = (w, h) => Object.assign(document.createElement("canvas"), { width: w, height: h });

  function pathOf(polygons, w, h) {
    const path = new Path2D();
    polygons.forEach(poly => {
      poly.forEach(([x, y], i) => i ? path.lineTo(x * w, y * h) : path.moveTo(x * w, y * h));
      path.closePath();
    });
    return path;
  }

  function buildLayers(source, plan) {
    const w = source.naturalWidth, h = source.naturalHeight;
    const probe = canvasOf(w, h), ctx = probe.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(source, 0, 0);
    const src = ctx.getImageData(0, 0, w, h).data;
    ctx.clearRect(0, 0, w, h);
    // Later, smaller semantic regions reserve their pixels before any painting.
    // Thus text inside a prop never leaks into the earlier prop reveal.
    plan.parts.forEach((part, i) => {
      ctx.fillStyle = `rgb(${i + 1},0,0)`;
      ctx.fill(pathOf(part.polygons, w, h));
    });
    const ownership = ctx.getImageData(0, 0, w, h).data;
    let uncovered = 0, ink = 0;
    for (let p = 0; p < w * h; p++) {
      const at = p * 4;
      if (Math.min(src[at], src[at + 1], src[at + 2]) < 224 && src[at + 3] > 127) {
        ink++;
        if (!ownership[at + 3] || ownership[at] < 1 || ownership[at] > plan.parts.length) uncovered++;
      }
    }
    const layers = plan.parts.map((part, i) => {
      const points = part.polygons.flat();
      const x0 = Math.max(0, Math.floor(Math.min(...points.map(p => p[0])) * w) - 2);
      const y0 = Math.max(0, Math.floor(Math.min(...points.map(p => p[1])) * h) - 2);
      const x1 = Math.min(w, Math.ceil(Math.max(...points.map(p => p[0])) * w) + 2);
      const y1 = Math.min(h, Math.ceil(Math.max(...points.map(p => p[1])) * h) + 2);
      const image = canvasOf(x1 - x0, y1 - y0), out = image.getContext("2d");
      const data = out.createImageData(image.width, image.height);
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
        const at = (y * w + x) * 4, to = ((y - y0) * image.width + x - x0) * 4;
        if (ownership[at] === i + 1 && ownership[at + 3] > 0) {
          data.data[to] = src[at]; data.data[to + 1] = src[at + 1];
          data.data[to + 2] = src[at + 2]; data.data[to + 3] = src[at + 3];
        }
      }
      out.putImageData(data, 0, 0);
      const width = part.kind === "text" ? 13 : part.kind === "motion" ? 12 : 25;
      const step = width * .65, strokes = [];
      let length = 0;
      // Follow short, alternating brush passes over actual ink, not a radial wipe.
      for (let band = 0, y = width / 3; y < image.height + step; y += step, band++) {
        const center = Math.min(image.height - 1, Math.round(y));
        const runs = []; let begin = null, last = null;
        for (let x = 0; x < image.width; x++) {
          let found = false;
          for (let yy = Math.max(0, center - Math.ceil(step / 2)); yy <= Math.min(image.height - 1, center + Math.ceil(step / 2)); yy++) {
            const at = (yy * image.width + x) * 4;
            if (data.data[at + 3] && Math.min(data.data[at], data.data[at + 1], data.data[at + 2]) < 246) { found = true; break; }
          }
          if (found) { if (begin === null) begin = x; last = x; }
          if (begin !== null && ((!found && x - last > width) || x === image.width - 1)) {
            runs.push([Math.max(0, begin - width / 2), Math.min(image.width, last + width / 2)]); begin = null;
          }
        }
        if (band % 2) runs.reverse();
        runs.forEach(([a, b]) => {
          const from = [band % 2 ? b : a, center], to = [band % 2 ? a : b, center];
          const size = Math.max(1, Math.abs(b - a));
          strokes.push({ from, to, start: length, end: length + size }); length += size;
        });
      }
      return { image, x: x0, y: y0, strokes, length, width,
        scratch: canvasOf(image.width, image.height), mask: canvasOf(image.width, image.height), part };
    });
    return { layers, coverage: { ink, uncovered, ratio: ink ? 1 - uncovered / ink : 0 } };
  }

  function paintLayer(context, layer, progress) {
    if (progress <= 0) return null;
    if (progress >= 1) { context.drawImage(layer.image, layer.x, layer.y); return null; }
    const mask = layer.mask.getContext("2d");
    mask.clearRect(0, 0, layer.mask.width, layer.mask.height);
    mask.strokeStyle = "#fff"; mask.lineWidth = layer.width; mask.lineCap = "round";
    const limit = layer.length * progress; let tip = null;
    for (const stroke of layer.strokes) {
      if (limit <= stroke.start) break;
      const ratio = clamp((limit - stroke.start) / (stroke.end - stroke.start));
      const point = [stroke.from[0] + (stroke.to[0] - stroke.from[0]) * ratio, stroke.from[1]];
      mask.beginPath(); mask.moveTo(...stroke.from); mask.lineTo(...point); mask.stroke();
      tip = [point[0] + layer.x, point[1] + layer.y];
      if (ratio < 1) break;
    }
    const scratch = layer.scratch.getContext("2d");
    scratch.globalCompositeOperation = "source-over";
    scratch.clearRect(0, 0, layer.scratch.width, layer.scratch.height);
    scratch.drawImage(layer.image, 0, 0);
    scratch.globalCompositeOperation = "destination-in"; scratch.drawImage(layer.mask, 0, 0);
    scratch.globalCompositeOperation = "source-over";
    context.drawImage(layer.scratch, layer.x, layer.y);
    return tip;
  }

  function installDrawing(tl, scene, plan) {
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
        const progress = clamp((state.elapsed - entry.start) / (entry.end - entry.start));
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
      built = buildLayers(source, plan);
      canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
      root.dataset.paintCoverage = String(built.coverage.ratio);
      window.__spongePaintingQC = window.__spongePaintingQC || {};
      window.__spongePaintingQC[scene.id] = { ...built.coverage, start, duration, schedule, parts: plan.parts.map(p => ({ id: p.id, kind: p.kind })) };
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

  window.installSpongeInfoCards = function (tl, scenes, cues, ids) {
    const old = new Set();
    scenes.filter(s => ids.has(s.id)).forEach(scene => {
      const root = document.getElementById(scene.id);
      if (!root.dataset.drawingPlan) { old.add(scene.id); return; }
      const cards = [...root.querySelectorAll(".sponge-info-card")], effect = root.dataset.textEffect;
      const times = cards.map(card => Number(card.dataset.cueAt));
      const rail = root.querySelector(".sponge-step-progress");
      cards.forEach((card, i) => {
        const at = times[i], next = times[i + 1] || scene.end - .65;
        const copy = card.querySelector(".card-copy"), marker = card.querySelector(".card-marker");
        const check = card.querySelector(".card-check"), underline = card.querySelector(".card-underline");
        const before = effect === "compare" ? { opacity: 0, x: i % 2 ? 34 : -34, rotationY: -8 }
          : effect === "steps" ? { opacity: 0, y: 24, scale: .96 }
          : effect === "checklist" ? { opacity: 0, x: -18, y: 12 }
          : { opacity: 0, y: 22, scale: .96 };
        tl.fromTo(card, before, { opacity: 1, x: 0, y: 0, scale: 1, rotationY: 0, duration: .42, ease: "power3.out", immediateRender: false }, at);
        tl.fromTo(copy, { opacity: 0, y: 10 }, { opacity: 1, y: 0, duration: .32, ease: "power2.out", immediateRender: false }, at + .10);
        tl.fromTo(marker, { scaleX: 0 }, { scaleX: 1, duration: .50, ease: "power2.out", immediateRender: false }, at + .06);
        tl.fromTo(underline, { scaleX: 0 }, { scaleX: 1, duration: Math.min(1, Math.max(.35, next - at - .4)), ease: "power2.inOut", immediateRender: false }, at + .23);
        tl.to(card, { borderColor: "#477d5b", duration: .2 }, at + .10);
        if (i > 0) {
          tl.to(cards[i - 1].querySelector(".card-marker"), { opacity: .12, duration: .3 }, at);
          tl.to(cards[i - 1], { borderColor: "#d5ded6", duration: .25 }, at);
          tl.fromTo(cards[i - 1].querySelector(".card-check"), { scale: .4, opacity: 0 }, { scale: 1, opacity: 1, duration: .3, ease: "back.out(1.6)", immediateRender: false }, at);
        }
        if (rail) tl.to(rail, { scaleY: (i + 1) / cards.length, duration: .5, ease: "power2.inOut" }, at);
        if (i === cards.length - 1) tl.fromTo(check, { scale: .4, opacity: 0 }, { scale: 1, opacity: 1, duration: .3, immediateRender: false }, Math.min(scene.end - .6, at + 2.5));
      });
      const decision = document.getElementById(scene.id + "--note-row-1");
      if (decision) tl.fromTo(decision, { opacity: 0, y: 16 }, { opacity: 1, y: 0, duration: .38, ease: "power3.out", immediateRender: false }, Math.min(scene.end - 1, times[times.length - 1] + 2.2));
    });
    if (old.size) legacyCards(tl, scenes, cues, old);
  };
})();
