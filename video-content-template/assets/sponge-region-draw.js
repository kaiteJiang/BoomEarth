/* Draw native sponge illustrations by visual regions, then color each region. */
(function () {
  "use strict";
  const clamp = value => Math.max(0, Math.min(1, value));

  function nearest(point, centers) {
    let index = 0, distance = Infinity;
    centers.forEach((center, i) => {
      const dx = point.x - center.x, dy = point.y - center.y;
      const candidate = dx * dx + dy * dy;
      if (candidate < distance) { distance = candidate; index = i; }
    });
    return index;
  }

  function foregroundSamples(source) {
    const probe = document.createElement("canvas");
    probe.width = source.naturalWidth;
    probe.height = source.naturalHeight;
    const ctx = probe.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(source, 0, 0);
    const pixels = ctx.getImageData(0, 0, probe.width, probe.height).data;
    const samples = [], yellow = [];
    for (let y = 0; y < probe.height; y += 12) {
      for (let x = 0; x < probe.width; x += 12) {
        const at = (y * probe.width + x) * 4;
        const r = pixels[at], g = pixels[at + 1], b = pixels[at + 2];
        if (255 - Math.min(r, g, b) < 18) continue;
        const point = { x: x / probe.width, y: y / probe.height };
        samples.push(point);
        if (r > 205 && g > 155 && b < 140 && r > b + 95 && g > b + 55) yellow.push(point);
      }
    }
    return { samples, yellow };
  }

  function regionCenters(samples, count = 4) {
    if (samples.length < count) return count === 3 ? [
      { x: .25, y: .35 }, { x: .75, y: .35 }, { x: .5, y: .75 },
    ] : [
      { x: .25, y: .25 }, { x: .75, y: .25 },
      { x: .25, y: .75 }, { x: .75, y: .75 },
    ];
    const xs = samples.map(point => point.x), ys = samples.map(point => point.y);
    const left = Math.min(...xs), right = Math.max(...xs);
    const top = Math.min(...ys), bottom = Math.max(...ys);
    const four = [
      { x: left * .75 + right * .25, y: top * .75 + bottom * .25 },
      { x: left * .25 + right * .75, y: top * .75 + bottom * .25 },
      { x: left * .75 + right * .25, y: top * .25 + bottom * .75 },
      { x: left * .25 + right * .75, y: top * .25 + bottom * .75 },
    ];
    const centers = count === 3 ? [four[0], four[1],
      { x: (left + right) / 2, y: top * .25 + bottom * .75 }] : four;
    for (let repeat = 0; repeat < 8; repeat++) {
      const sums = centers.map(() => ({ x: 0, y: 0, count: 0 }));
      samples.forEach(point => {
        const sum = sums[nearest(point, centers)];
        sum.x += point.x; sum.y += point.y; sum.count++;
      });
      sums.forEach((sum, i) => {
        if (sum.count) centers[i] = { x: sum.x / sum.count, y: sum.y / sum.count };
      });
    }
    return centers;
  }

  function clipHalfPlane(polygon, a, b, c) {
    const result = [];
    for (let i = 0; i < polygon.length; i++) {
      const first = polygon[i], next = polygon[(i + 1) % polygon.length];
      const f = a * first.x + b * first.y - c;
      const n = a * next.x + b * next.y - c;
      if (f <= 0) result.push(first);
      if ((f < 0 && n > 0) || (f > 0 && n < 0)) {
        const ratio = f / (f - n);
        result.push({ x: first.x + (next.x - first.x) * ratio,
          y: first.y + (next.y - first.y) * ratio });
      }
    }
    return result;
  }

  function regionsFor(centers) {
    return centers.map((center, index) => {
      let polygon = [
        { x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 },
      ];
      centers.forEach((other, j) => {
        if (index === j || !polygon.length) return;
        polygon = clipHalfPlane(polygon, 2 * (other.x - center.x),
          2 * (other.y - center.y), other.x ** 2 + other.y ** 2 - center.x ** 2 - center.y ** 2);
      });
      const xs = polygon.map(point => point.x), ys = polygon.map(point => point.y);
      return { polygon, box: {
        x0: Math.max(0, Math.min(...xs)), x1: Math.min(1, Math.max(...xs)),
        y0: Math.max(0, Math.min(...ys)), y1: Math.min(1, Math.max(...ys)),
      }, p: 0 };
    });
  }

  function drawRegion(ctx, source, region, progress, filter) {
    if (!region.polygon.length || progress <= 0) return;
    const w = source.naturalWidth, h = source.naturalHeight;
    const box = region.box;
    ctx.save();
    ctx.beginPath();
    region.polygon.forEach((point, i) => {
      if (!i) ctx.moveTo(point.x * w, point.y * h);
      else ctx.lineTo(point.x * w, point.y * h);
    });
    ctx.closePath();
    if (region.holes) {
      region.holes.forEach(hole => ctx.rect(hole.x0 * w, hole.y0 * h,
        (hole.x1 - hole.x0) * w, (hole.y1 - hole.y0) * h));
      ctx.clip("evenodd");
    } else ctx.clip();
    const cx = (box.x0 + box.x1) * w / 2, cy = (box.y0 + box.y1) * h / 2;
    const radius = Math.hypot((box.x1 - box.x0) * w, (box.y1 - box.y0) * h) * progress;
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(.01, radius), 0, Math.PI * 2);
    ctx.clip();
    ctx.filter = filter;
    ctx.drawImage(source, 0, 0);
    ctx.restore();
  }

  function drawMascots(ctx, source, boxes, progress, filter) {
    const w = source.naturalWidth, h = source.naturalHeight;
    boxes.forEach((box, index) => {
      const part = clamp(progress * boxes.length - index);
      if (!part) return;
      const cx = (box.x0 + box.x1) * w / 2, cy = (box.y0 + box.y1) * h / 2;
      const radius = Math.hypot((box.x1 - box.x0) * w, (box.y1 - box.y0) * h) * part;
      ctx.save();
      ctx.beginPath();
      ctx.rect(box.x0 * w, box.y0 * h, (box.x1 - box.x0) * w, (box.y1 - box.y0) * h);
      ctx.clip();
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(.01, radius), 0, Math.PI * 2);
      ctx.clip();
      ctx.filter = filter;
      ctx.drawImage(source, 0, 0);
      ctx.restore();
    });
  }

  function tipFor(region, samples, progress) {
    const box = region.box;
    const x = (box.x0 + box.x1) / 2, y = (box.y0 + box.y1) / 2;
    const radius = Math.hypot(box.x1 - box.x0, box.y1 - box.y0) * progress;
    let best = null, distance = Infinity;
    samples.forEach(point => {
      const candidate = Math.abs(Math.hypot((point.x - x) * 1.4, point.y - y) - radius);
      if (candidate < distance) { distance = candidate; best = point; }
    });
    return best;
  }

  function cardEntryTimes(scene, own, cards) {
    let previousAt = scene.start + 1.0;
    return cards.map((card, index) => {
      const keyword = card.dataset.keyword || "";
      const fragments = [keyword, keyword.slice(0, 2), keyword.slice(-2)]
        .filter(value => value.length >= 2);
      const matched = own.find(cue => fragments.some(fragment => cue.text.includes(fragment)));
      const fallback = own[Math.min(own.length - 1,
        Math.floor((index + 1) * own.length / (cards.length + 1)))];
      const candidate = matched ? matched.start : fallback ? fallback.start : scene.start + 3 + index * 3;
      previousAt = Math.min(scene.end - 2, Math.max(previousAt + 1.2, candidate));
      return previousAt;
    });
  }

  window.installSpongeRegionDraw = function (tl, scenes, cues, drawIds) {
    scenes.filter(scene => drawIds.has(scene.id)).forEach(scene => {
      const source = document.getElementById(scene.id + "--draw-source");
      const canvas = document.getElementById(scene.id + "--draw-canvas");
      const cursor = document.getElementById(scene.id + "--pencil-cursor");
      const ctx = canvas.getContext("2d");
      let regions = null, ordered = [], regionSamples = [], guided = false;
      const closure = { p: 0 };
      function paint() {
        if (!source.complete || !source.naturalWidth || !regions) return;
        if (canvas.width !== source.naturalWidth || canvas.height !== source.naturalHeight) {
          canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
        }
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (regions.every(region => region.p >= 1 - 1e-6)) {
          ctx.drawImage(source, 0, 0);
          cursor.style.opacity = "0";
          return;
        }
        if (guided) {
          ctx.save();
          ctx.globalAlpha = .19;
          ctx.filter = "grayscale(1) contrast(1.6)";
          ctx.drawImage(source, 0, 0);
          ctx.restore();
        }
        // Keep the completed mascot layer above later prop sketches. Voronoi prop
        // regions can otherwise repaint part of a finished body in grayscale.
        [...regions.filter(region => !region.mascots),
          ...regions.filter(region => region.mascots)].forEach(region => {
          const progress = clamp(region.p);
          const draw = region.mascots
            ? (amount, filter) => drawMascots(ctx, source, region.mascots, amount, filter)
            : (amount, filter) => drawRegion(ctx, source, region, amount, filter);
          draw(clamp(progress / .42), "grayscale(1) contrast(3)");
          draw(clamp((progress - .2) / .75), "none");
        });
        if (closure.p > 0) {
          ctx.save();
          ctx.globalAlpha = clamp(closure.p);
          ctx.drawImage(source, 0, 0);
          ctx.restore();
        }
        if (closure.p >= 1 - 1e-6) { cursor.style.opacity = "0"; return; }
        const active = ordered.find(index => regions[index].p > 0 && regions[index].p < 1);
        if (active === undefined) { cursor.style.opacity = "0"; return; }
        const region = regions[active];
        const phase = region.p < .42 ? region.p / .42 : (region.p - .2) / .75;
        const mascotIndex = region.mascots
          ? Math.min(region.mascots.length - 1, Math.floor(clamp(phase) * region.mascots.length)) : 0;
        const mascotBox = region.mascots ? region.mascots[mascotIndex] : null;
        const cursorRegion = mascotBox ? { box: mascotBox } : region;
        const cursorSamples = mascotBox ? regionSamples[active].filter(point =>
          point.x >= mascotBox.x0 && point.x <= mascotBox.x1 &&
          point.y >= mascotBox.y0 && point.y <= mascotBox.y1) : regionSamples[active];
        const cursorProgress = mascotBox ? clamp(phase * region.mascots.length - mascotIndex) : clamp(phase);
        const tip = tipFor(cursorRegion, cursorSamples, cursorProgress);
        if (!tip) { cursor.style.opacity = "0"; return; }
        cursor.style.left = (tip.x * canvas.clientWidth - 112) + "px";
        cursor.style.top = (tip.y * canvas.clientHeight - 137) + "px";
        cursor.style.opacity = "1";
      }
      function init() {
        if (!source.naturalWidth) return;
        const data = foregroundSamples(source);
        const root = document.getElementById(scene.id);
        const explicitHosts = root.dataset.hostRegions
          ? JSON.parse(root.dataset.hostRegions).map(([x0, y0, x1, y1]) => ({ x0, y0, x1, y1 })) : null;
        const regionBoxes = root.dataset.drawRegions ? JSON.parse(root.dataset.drawRegions)
          .map(([x0, y0, x1, y1]) => ({ x0, y0, x1, y1 })) : null;
        let hostBoxes = explicitHosts;
        if (!hostBoxes) {
          const yellow = data.yellow.length ? data.yellow : [{ x: .7, y: .45 }];
          const sortedX = yellow.map(point => point.x).sort((a, b) => a - b);
          const sortedY = yellow.map(point => point.y).sort((a, b) => a - b);
          const middleX = sortedX[Math.floor(sortedX.length / 2)];
          const middleY = sortedY[Math.floor(sortedY.length / 2)];
          const core = yellow.filter(point => Math.abs(point.x - middleX) < .24
            && Math.abs(point.y - middleY) < .30);
          const body = core.length > 20 ? core : yellow;
          hostBoxes = [{
            x0: clamp(Math.min(...body.map(point => point.x)) - .11),
            y0: clamp(Math.min(...body.map(point => point.y)) - .16),
            x1: clamp(Math.max(...body.map(point => point.x)) + .11),
            y1: clamp(Math.max(...body.map(point => point.y)) + .23),
          }];
        }
        const insideHost = point => hostBoxes.some(box => point.x >= box.x0 && point.x <= box.x1
          && point.y >= box.y0 && point.y <= box.y1);
        if (regionBoxes) {
          regions = [{ mascots: hostBoxes, box: hostBoxes[0], p: 0 }, { polygon: [
            { x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 },
          ], box: { x0: 0, y0: 0, x1: 1, y1: 1 },
            holes: [...hostBoxes, ...regionBoxes], p: 0 }, ...regionBoxes.map(box => ({
            polygon: [
              { x: box.x0, y: box.y0 }, { x: box.x1, y: box.y0 },
              { x: box.x1, y: box.y1 }, { x: box.x0, y: box.y1 },
            ], box, holes: hostBoxes, p: 0,
          }))];
          ordered = regions.map((_region, index) => index);
          regionSamples = regions.map(() => []);
          data.samples.forEach(point => {
            const card = regionBoxes.findIndex(box => point.x >= box.x0 && point.x <= box.x1
              && point.y >= box.y0 && point.y <= box.y1);
            regionSamples[insideHost(point) ? 0 : card < 0 ? 1 : card + 2].push(point);
          });
        } else {
          const others = data.samples.filter(point => !insideHost(point));
          const centers = regionCenters(others, 3);
          regions = [{ mascots: hostBoxes, box: hostBoxes[0], p: 0 }, ...regionsFor(centers).map(region => ({
            ...region, holes: hostBoxes,
          }))];
          regionSamples = regions.map(() => []);
          data.samples.forEach(point => regionSamples[insideHost(point) ? 0 : nearest(point, centers) + 1].push(point));
          ordered = [0, ...centers.map((_center, i) => i + 1)
            .sort((a, b) => centers[a - 1].x - centers[b - 1].x)];
          guided = true;
        }
        const cards = Array.from(document.getElementById(scene.id).querySelectorAll(".sponge-info-card"));
        // Drawing has its own uninterrupted rhythm. The left cards still follow speech cues.
        const sceneLength = scene.end - scene.start;
        const drawStart = scene.start + .35;
        const drawDuration = Math.min(12, Math.max(6.2, sceneLength * .42));
        const weights = cards.length === 3 ? [.34, .16, .17, .16, .17]
          : hostBoxes.length > 1 ? [.44, .19, .18, .19] : [.36, .22, .20, .22];
        const totalWeight = ordered.reduce((sum, _index, step) => sum + (weights[step] || .2), 0);
        let at = drawStart;
        ordered.forEach((index, step) => {
          const duration = drawDuration * (weights[step] || .2) / totalWeight;
          tl.to(regions[index], { p: 1, duration, ease: "none",
            onUpdate: paint, onComplete: paint }, at);
          at += duration;
        });
        tl.to(closure, { p: 1, duration: .35, ease: "none",
          onUpdate: paint, onComplete: paint }, at);
        paint();
      }
      source.addEventListener("load", init, { once: true });
      if (source.complete) init();
    });
  };

  window.installSpongeInfoCards = function (tl, scenes, cues, drawIds) {
    scenes.filter(scene => drawIds.has(scene.id)).forEach(scene => {
      const root = document.getElementById(scene.id);
      const cards = Array.from(root.querySelectorAll(".sponge-info-card"));
      const own = cues.filter(cue => cue.start >= scene.start - .001 && cue.start < scene.end - .001);
      let previous = null;
      const times = cardEntryTimes(scene, own, cards);
      cards.forEach((card, index) => {
        const at = times[index];
        tl.fromTo(card, { opacity: 0, x: -32 },
          { opacity: 1, x: 0, duration: .36, ease: "power3.out" }, at);
        tl.to(card, { backgroundColor: "#fff3d8", borderColor: "#477d5b",
          boxShadow: "0 0 0 2px #477d5b", duration: .25 }, at + .14);
        tl.to(card.querySelector(".card-indicator"), { opacity: 1, duration: .2 }, at + .14);
        if (previous) {
          tl.to(previous, { backgroundColor: "#ffffff", borderColor: "#d5ded6",
            boxShadow: "none", duration: .2 }, at + .14);
          tl.to(previous.querySelector(".card-indicator"), { opacity: 0, duration: .2 }, at + .14);
        }
        previous = card;
      });
      const decision = document.getElementById(scene.id + "--note-row-1");
      if (decision) {
        tl.fromTo(decision, { opacity: 0, y: 18 },
          { opacity: 1, y: 0, duration: .3, ease: "power3.out" },
          Math.min(scene.end - 1.2, (times[times.length - 1] || scene.start + 2) + 1.5));
      }
    });
  };
})();
