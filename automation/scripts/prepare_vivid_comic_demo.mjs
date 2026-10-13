#!/usr/bin/env node
/** Prepare the public demo from installed dependencies; preserve older outputs. */
import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { copyFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_ROOT = fileURLToPath(new URL('../../', import.meta.url));
const RUNTIME_FILES = [
  'sponge-region-draw.js',
  'sponge-component-draw.js',
  'vivid-comic-component-draw.js',
];
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

async function readOptional(filename) {
  try { return await readFile(filename); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

async function writePreserving(directory, name, bytes) {
  const destination = path.join(directory, name);
  const previous = await readOptional(destination);
  if (previous && sha256(previous) === sha256(bytes)) return 'reused';
  if (previous) {
    const archive = path.join(directory, 'retained', `${name}.${sha256(previous)}`);
    await mkdir(path.dirname(archive), { recursive: true });
    try { await copyFile(destination, archive, constants.COPYFILE_EXCL); }
    catch (error) {
      if (error.code !== 'EEXIST' || sha256(await readFile(archive)) !== sha256(previous)) throw error;
    }
  }
  // Publish only after the old bytes have a retained copy. A failed write or
  // rename leaves the prior destination and/or the unique pending file intact.
  const pending = path.join(directory, `${name}.${randomUUID()}.pending`);
  await writeFile(pending, bytes, { flag: 'wx' });
  await rename(pending, destination);
  return previous ? 'updated-and-retained' : 'created';
}

export async function prepareVividComicDemo({ repoRoot = DEFAULT_ROOT, nodeModulesDir } = {}) {
  const root = path.resolve(repoRoot);
  const example = path.join(root, 'examples', 'vivid-comic');
  const modules = path.resolve(nodeModulesDir ?? path.join(root, 'node_modules'));
  const semantic = JSON.parse(await readFile(path.join(example, 'comic-semantic-parts.json'), 'utf8'));
  const plan = JSON.parse(await readFile(path.join(example, 'sponge-drawing-plan.json'), 'utf8'));
  const html = await readFile(path.join(example, 'index.html'), 'utf8');
  const sceneIds = ['scene-11', 'scene-26'];
  for (const id of sceneIds) {
    const source = await readFile(path.join(example, 'assets', `${id}.png`));
    await readFile(path.join(example, 'assets', `${id}.index.png`));
    const entry = semantic.scenes?.[id];
    if (!entry || entry.source_sha256 !== sha256(source) || plan.scenes?.[id]?.source_sha256 !== entry.source_sha256) {
      throw new Error(`${id}: native source SHA-256 does not match both drawing contracts.`);
    }
    const rle = Buffer.from(entry.ownership_rle, 'base64');
    let pixels = 0;
    if (!rle.length || rle.length % 5) throw new Error(`${id}: malformed semantic ownership RLE.`);
    for (let offset = 0; offset < rle.length; offset += 5) {
      const count = rle.readUInt32LE(offset);
      if (!count || rle[offset + 4] > entry.parts.length) throw new Error(`${id}: invalid semantic ownership run.`);
      pixels += count;
    }
    if (pixels !== entry.width * entry.height) throw new Error(`${id}: semantic ownership dimensions do not match.`);
  }

  // Read every input before generating anything, so a missing dependency cannot
  // leave a partly prepared first run. No node_modules or runtime is vendored.
  const inputs = new Map();
  try { inputs.set('gsap.min.js', await readFile(path.join(modules, 'gsap', 'dist', 'gsap.min.js'))); }
  catch (error) { throw new Error('GSAP is unavailable. Run npm ci in the repository root, or pass --node-modules <installed-directory>.', { cause: error }); }
  for (const name of RUNTIME_FILES) inputs.set(name, await readFile(path.join(root, 'video-content-template', 'assets', name)));
  inputs.set('hand-pencil-cursor.png', await readFile(path.join(root, 'video-content-template', 'assets', 'hand-paintbrush-cursor.png')));
  inputs.set('comic-semantic-parts.js', Buffer.from(`window.__vividComicSemanticParts=${JSON.stringify(semantic.scenes)};\n`));
  const references = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)].map((match) => match[1]);
  for (const reference of references) {
    if (/^(?:[a-z]+:|\/\/|#)/i.test(reference)) throw new Error(`The standalone demo must use local references: ${reference}`);
    if (reference.startsWith('generated/')) {
      if (!inputs.has(reference.slice('generated/'.length))) throw new Error(`Unknown generated resource: ${reference}`);
    } else {
      const resource = path.resolve(example, reference);
      if (!resource.startsWith(`${example}${path.sep}`)) throw new Error(`Reference escapes the example directory: ${reference}`);
      await readFile(resource);
    }
  }
  const generated = path.join(example, 'generated');
  await mkdir(generated, { recursive: true });
  const resources = [];
  for (const [name, bytes] of inputs) {
    resources.push({ file: `generated/${name}`, sha256: sha256(bytes), status: await writePreserving(generated, name, bytes) });
  }
  return { example: 'examples/vivid-comic', duration_seconds: 18, local_references_checked: references.length, resources };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length && (args.length !== 2 || args[0] !== '--node-modules')) {
      throw new Error('Usage: node automation/scripts/prepare_vivid_comic_demo.mjs [--node-modules <directory>]');
    }
    const result = await prepareVividComicDemo({ nodeModulesDir: args[1] });
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(`Demo preparation failed: ${error.message}`);
    process.exitCode = 1;
  }
}
