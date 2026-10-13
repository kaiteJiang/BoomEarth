(function () {
  "use strict";
  const original = window.installSpongeRegionDraw;
  window.installSpongeRegionDraw = function (tl, scenes, cues, ids) {
    original(tl, scenes, cues, ids);
    scenes.filter(scene => ids.has(scene.id)).forEach(scene => {
      const root = document.getElementById(scene.id);
      if (!root || !root.dataset.drawingPlan) return;
      const start = scene.start + .30;
      const oldDuration = Math.min(19, Math.max(7, (scene.end - scene.start) * .70));
      const duration = Math.min(oldDuration, scene.end - start - .40);
      if (duration >= oldDuration) return;
      if (duration <= 0) throw new Error("comic drawing has no available scene time");
      const tweens = tl.getChildren(false, true, false).filter(tween =>
        Math.abs(tween.startTime() - start) < .000001 &&
        tween.vars.elapsed === oldDuration && tween.targets().length === 1 &&
        typeof tween.targets()[0].elapsed === "number");
      if (tweens.length !== 1) throw new Error("comic drawing tween must be unique");
      // Keep the original closure's component sequence and cursor path; only fit
      // its playback into this short scene. Labels finish before the final hold.
      tweens[0].duration(duration);
      const adaptQC = () => {
        const qc = window.__spongePaintingQC?.[scene.id];
        if (!qc || qc.duration === duration) return;
        const scale = duration / oldDuration;
        qc.duration = duration;
        // Copy, never mutate the schedule used by the original paint closure.
        qc.schedule = qc.schedule.map(part => ({ ...part, start: part.start * scale, end: part.end * scale }));
      };
      const source = document.getElementById(scene.id + "--draw-source");
      source.addEventListener("load", adaptQC, { once: true });
      adaptQC();
    });
  };
})();
