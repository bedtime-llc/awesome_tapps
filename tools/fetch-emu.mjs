/*
 * fetch-emu.mjs — resolve the emulator wasm that screenshot.mjs drives.
 *
 *   node tools/fetch-emu.mjs [dest]        default: the cache dir below
 *   import { ensureEmu } from './fetch-emu.mjs'
 *
 * The emulator is built from the private firmware tree and is not in any repo; it is published as
 * part of the builder page. Taking it from there means a catalog screenshot is produced by exactly
 * the build a visitor runs when they press "run" on tapp.b.edti.me.
 *
 * NOTHING EMULATOR-SHAPED LIVES IN THIS REPO.
 * The default destination is deliberately OUTSIDE the working tree. It used to be `<repo>/.emu/`,
 * and that directory became a second source of truth: a hand-copied local build sat there, newer
 * than the deployed page in some symbols and older in others, and screenshot.mjs used it without
 * ever checking. A screenshot taken locally then disagreed with the one CI published. There is now
 * no in-repo path to hand-place a build into, and a stray copy would show up in `git status`
 * instead of hiding behind a .gitignore rule.
 *
 * Point EMU_BASE at a local firmware build's dist/ (or EMU_DIR at screenshot.mjs) when you
 * deliberately want to test against something other than what is deployed.
 *
 * Revalidation is a conditional GET: the ETag of each file is stored beside it, so a repeat run
 * costs one 304 rather than 1.3 MB, and a redeploy is picked up immediately. The sha256 is printed
 * either way — if a screenshot ever changes for no apparent reason, compare it against the previous
 * run's log first.
 */

import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve, join } from 'node:path';
import { homedir, tmpdir } from 'node:os';

const BASE = process.env.EMU_BASE ?? 'https://tapp.b.edti.me/emu/';
const FILES = ['tapp_emu.js', 'tapp_emu.wasm'];

/* XDG cache if we can find a home, tmpdir otherwise (some CI images have no HOME). */
export function emuCacheDir() {
    const xdg = process.env.XDG_CACHE_HOME;
    if (xdg) return join(xdg, 'awesome-tapps', 'emu');
    const home = homedir();
    if (home && home !== '/') return join(home, '.cache', 'awesome-tapps', 'emu');
    return join(tmpdir(), 'awesome-tapps-emu');
}

/*
 * Downloads only what changed. Returns { dir, files: [{name, bytes, sha, cached}] } so the caller
 * can record exactly which emulator produced its output.
 */
export async function ensureEmu({ dest = emuCacheDir(), base = BASE, quiet = false } = {}) {
    const dir = resolve(dest);
    mkdirSync(dir, { recursive: true });

    const files = [];
    for (const name of FILES) {
        const url = new URL(name, base).href;
        const path = resolve(dir, name);
        const etagPath = `${path}.etag`;

        const headers = {};
        const haveBody = existsSync(path);
        if (haveBody && existsSync(etagPath)) {
            headers['If-None-Match'] = readFileSync(etagPath, 'utf8').trim();
        }

        let res;
        try {
            res = await fetch(url, { headers });
        } catch (e) {
            throw new Error(`fetch-emu: ${url} — ${e.message}`);
        }

        let cached = false;
        if (res.status === 304 && haveBody) {
            cached = true;
        } else if (!res.ok) {
            throw new Error(`fetch-emu: ${url} -> HTTP ${res.status}`);
        } else {
            const buf = Buffer.from(await res.arrayBuffer());
            writeFileSync(path, buf);
            const etag = res.headers.get('etag');
            if (etag) writeFileSync(etagPath, etag);
            else if (existsSync(etagPath)) writeFileSync(etagPath, '');
        }

        const buf = readFileSync(path);
        const sha = createHash('sha256').update(buf).digest('hex');
        files.push({ name, bytes: buf.length, sha, cached });
        if (!quiet) {
            console.log(
                `${name}  ${(buf.length / 1048576).toFixed(2)} MB  sha256:${sha}` +
                    (cached ? '  (unchanged)' : ''),
            );
        }
    }

    if (!quiet) console.log(`emulator: ${base} -> ${dir}`);
    return { dir, files };
}

/* CLI: `node tools/fetch-emu.mjs [dest]`. Kept so CI can warm the cache and log the sha as
 * provenance before the screenshot step runs. */
if (import.meta.url === `file://${process.argv[1]}`) {
    try {
        await ensureEmu({ dest: process.argv[2] ?? emuCacheDir() });
    } catch (e) {
        console.error(e.message);
        process.exit(1);
    }
}
