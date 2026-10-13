import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { prepareVividComicDemo } from '../automation/scripts/prepare_vivid_comic_demo.mjs';

async function fixture() {
  // Retain these tiny test workspaces so failed or interrupted runs remain
  // inspectable. The tests never overwrite a real example or runtime.
  const root = await mkdtemp(path.join(os.tmpdir(), 'vivid-comic-prepare-test-'));
  const example = path.join(root, 'examples', 'vivid-comic');
  const runtime = path.join(root, 'video-content-template', 'assets');
  const gsap = path.join(root, 'node_modules', 'gsap', 'dist');
  await Promise.all([mkdir(path.join(example, 'assets'), { recursive: true }), mkdir(runtime, { recursive: true }), mkdir(gsap, { recursive: true })]);
  const scenes = {};
  const plan = {};
  for (const id of ['scene-11', 'scene-26']) {
    const source = Buffer.from(`test native source ${id}`);
    const hash = createHash('sha256').update(source).digest('hex');
    const rle = Buffer.from([1, 0, 0, 0, 1]);
    scenes[id] = { source_sha256: hash, width: 1, height: 1, parts: [{ id: 'one', kind: 'object', weight: 1 }], ownership_rle: rle.toString('base64') };
    plan[id] = { source_sha256: hash, parts: scenes[id].parts };
    await writeFile(path.join(example, 'assets', `${id}.png`), source);
    await writeFile(path.join(example, 'assets', `${id}.index.png`), Buffer.from([1]));
  }
  await writeFile(path.join(example, 'comic-semantic-parts.json'), JSON.stringify({ schema_version: 1, scenes }));
  await writeFile(path.join(example, 'sponge-drawing-plan.json'), JSON.stringify({ schema_version: 2, scenes: plan }));
  await writeFile(path.join(example, 'index.html'), '<img src="assets/scene-11.png"><img src="assets/scene-26.png"><script src="generated/gsap.min.js"></script><script src="generated/comic-semantic-parts.js"></script>');
  await writeFile(path.join(gsap, 'gsap.min.js'), 'window.gsap = {};');
  for (const name of ['sponge-region-draw.js', 'sponge-component-draw.js', 'vivid-comic-component-draw.js', 'hand-paintbrush-cursor.png']) await writeFile(path.join(runtime, name), `test ${name}`);
  return { root, example, runtime, gsap };
}

test('preparation resolves references, reuses equal bytes and retains changed runtime', async () => {
  const { root, example, runtime } = await fixture();
  const first = await prepareVividComicDemo({ repoRoot: root });
  assert.equal(first.local_references_checked, 4);
  assert.equal(first.resources.length, 6);
  assert.ok(first.resources.every((entry) => entry.status === 'created'));
  const second = await prepareVividComicDemo({ repoRoot: root });
  assert.ok(second.resources.every((entry) => entry.status === 'reused'));
  const generated = path.join(example, 'generated');
  assert.deepEqual(await readFile(path.join(generated, 'hand-pencil-cursor.png')), await readFile(path.join(runtime, 'hand-paintbrush-cursor.png')));
  const old = await readFile(path.join(generated, 'vivid-comic-component-draw.js'));
  await writeFile(path.join(runtime, 'vivid-comic-component-draw.js'), 'new runtime bytes');
  const third = await prepareVividComicDemo({ repoRoot: root });
  assert.equal(third.resources.find((entry) => entry.file.endsWith('/vivid-comic-component-draw.js')).status, 'updated-and-retained');
  const archive = await readdir(path.join(generated, 'retained'));
  assert.equal(archive.length, 1);
  assert.deepEqual(await readFile(path.join(generated, 'retained', archive[0])), old);
  assert.equal(await readFile(path.join(generated, 'vivid-comic-component-draw.js'), 'utf8'), 'new runtime bytes');
});

test('missing dependency fails before creating generated resources', async () => {
  const { root, example } = await fixture();
  await assert.rejects(prepareVividComicDemo({ repoRoot: root, nodeModulesDir: path.join(root, 'missing-modules') }), /Run npm ci/);
  assert.ok(!(await readdir(example)).includes('generated'));
});

test('source hash mismatch fails before creating generated resources', async () => {
  const { root, example } = await fixture();
  await writeFile(path.join(example, 'assets', 'scene-26.png'), 'changed native source');
  await assert.rejects(prepareVividComicDemo({ repoRoot: root }), /SHA-256 does not match/);
  assert.ok(!(await readdir(example)).includes('generated'));
});
