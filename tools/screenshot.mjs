/*
 * screenshot.mjs — run a .tapp in the emulator and save a PNG of the screen.
 *
 *   node tools/screenshot.mjs app.tapp shot.png [frames]
 *
 * This drives the SAME emulator wasm the builder page runs when you press "run": it executes the
 * real ARM instructions of the artifact, so the screenshot is the tapp actually running, not a
 * mockup. No browser, and no npm dependency — the module is driven through its exported functions
 * and the PNG is written with node's built-in zlib.
 *
 * The module comes from https://tapp.b.edti.me/emu/ (see fetch-emu.mjs). Its main() only calls
 * platform_storage_init() and returns without installing a main loop, so instantiating it here is
 * safe; emu_web_start() is never called, so no audio device is opened.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { pathToFileURL } from 'node:url';
import { ensureEmu } from './fetch-emu.mjs';
import { resolve } from 'node:path';

/* No in-repo default: the emulator is resolved from the deployed page by ensureEmu().
 * EMU_DIR is an explicit override for testing against a local firmware build. */
const EMU_DIR = process.env.EMU_DIR ? resolve(process.env.EMU_DIR) : null;

/* The panel: 400x240, 1 bit per pixel. emu_web_render() hands back that framebuffer already
 * expanded to RGBA using the shell's palette, which is the same palette the desktop emulator
 * screenshots with — so captures from either are comparable. */
const W = 400, H = 240;
/* Interleaved stereo floats per block; must match unicorn_bridge.c's AUDIO_BLOCK_FS. */
const BLOCK_FS = 512;
/* Guest frame rate the browser shell runs at, used to convert frames to a "seconds in" figure. */
const GUEST_FPS = 29.86;
const SCALE = 2;   /* 800x480 out, matching targets/emu's built-in screenshot */

function fail(msg) {
    console.error(`screenshot: ${msg}`);
    process.exit(1);
}

/* ---- PNG ----------------------------------------------------------------
 * Minimal but correct RGB8 encoder: filter byte 0 per row, one IDAT, CRC per chunk. Enough for a
 * flat two-colour panel, and it keeps this script dependency-free. */

const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        t[n] = c >>> 0;
    }
    return t;
})();

function crc32(buf) {
    let c = 0xffffffff;
    for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
    const out = Buffer.alloc(data.length + 12);
    out.writeUInt32BE(data.length, 0);
    out.write(type, 4, 'ascii');
    data.copy(out, 8);
    out.writeUInt32BE(crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
    return out;
}

function encodePng(rgb, w, h) {
    const raw = Buffer.alloc(h * (1 + w * 3));
    for (let y = 0; y < h; y++) {
        raw[y * (1 + w * 3)] = 0;                                  /* filter: none */
        rgb.copy(raw, y * (1 + w * 3) + 1, y * w * 3, (y + 1) * w * 3);
    }
    const ihdr = Buffer.alloc(13);
    ihdr.writeUInt32BE(w, 0);
    ihdr.writeUInt32BE(h, 4);
    ihdr[8] = 8;    /* bit depth */
    ihdr[9] = 2;    /* colour type: truecolour */
    return Buffer.concat([
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
        chunk('IHDR', ihdr),
        chunk('IDAT', deflateSync(raw, { level: 9 })),
        chunk('IEND', Buffer.alloc(0)),
    ]);
}

/* RGBA framebuffer -> nearest-neighbour upscaled RGB. */
function scaleToRgb(rgba, scale) {
    const out = Buffer.alloc(W * scale * H * scale * 3);
    let o = 0;
    for (let y = 0; y < H; y++) {
        for (let sy = 0; sy < scale; sy++) {
            for (let x = 0; x < W; x++) {
                const i = (y * W + x) * 4;
                for (let sx = 0; sx < scale; sx++) {
                    out[o++] = rgba[i];
                    out[o++] = rgba[i + 1];
                    out[o++] = rgba[i + 2];
                }
            }
        }
    }
    return out;
}

/* ---- run ---------------------------------------------------------------- */

async function main() {
    const [tappPath, pngPath, framesArg] = process.argv.slice(2);
    if (!tappPath || !pngPath) {
        fail('usage: node tools/screenshot.mjs <app.tapp> <shot.png> [frames]');
    }
    const frames = Number(framesArg ?? 90);
    if (!Number.isInteger(frames) || frames < 1 || frames > 600) fail('frames must be 1..600');

    /* Resolve the emulator ourselves rather than trusting a directory to be populated and current.
     * EMU_DIR still overrides, for pointing at a local firmware build. */
    let emuDir = EMU_DIR;
    if (!emuDir) {
        try {
            const { dir, files } = await ensureEmu({ quiet: true });
            emuDir = dir;
            /* Record which emulator produced this image. Screenshots are published; when one looks
             * wrong, the first question is always which build drew it. */
            for (const f of files) console.log(`emu ${f.name} sha256:${f.sha.slice(0, 16)} ${f.bytes} B`);
        } catch (e) {
            fail(e.message);
        }
    }

    let TappEmu, wasmBinary;
    try {
        TappEmu = (await import(pathToFileURL(resolve(emuDir, 'tapp_emu.js')).href)).default;
        wasmBinary = readFileSync(resolve(emuDir, 'tapp_emu.wasm'));
    } catch (e) {
        fail(`emulator not usable in ${emuDir} (${e.message})`);
    }

    /*
     * The emulator's API table is hand-curated and is a SUBSET of the device's exports. A symbol it
     * lacks is not an error there: unicorn_bridge.c logs "unresolved API symbol" and lets the call
     * return whatever junk is in R0. So a tapp using a newer API builds clean, clears every gate,
     * and gets published with a garbage or blank screenshot — which then reads as the tapp's fault.
     * Collect stderr and refuse to write the PNG if that happened.
     */
    const unresolved = new Set();
    const mod = await TappEmu({
        wasmBinary,
        /* The shell writes progress to stdout; keep it out of the build log unless it matters. */
        print: () => {},
        printErr: (t) => {
            const m = /unresolved API symbol:\s*(\S+)/.exec(t);
            if (m) unresolved.add(m[1]);
            if (process.env.EMU_VERBOSE) console.error(t);
        },
    });

    const bytes = readFileSync(tappPath);
    const p = mod._malloc(bytes.length);
    mod.HEAPU8.set(bytes, p);
    const loaded = mod._emu_web_load(p, bytes.length);
    mod._free(p);
    const status = () => { try { return mod.UTF8ToString(mod._emu_web_status()); } catch { return ''; } };
    if (!loaded) fail(`emulator refused the tapp: ${status() || 'unknown error'}`);

    /* Audio has to be pulled even though it is thrown away. The guest's tick() is scheduled off the
     * audio clock, so a tapp that sequences or animates from tick() would render its very first
     * frame over and over if only render() were called. */
    const audio = mod._malloc(BLOCK_FS * 4);
    const blocksPerFrame = Math.max(1, Math.round(48000 / (GUEST_FPS * (BLOCK_FS / 2))));
    let pixels = 0;
    for (let i = 0; i < frames; i++) {
        for (let b = 0; b < blocksPerFrame; b++) mod._emu_web_pull_audio(audio, 1);
        pixels = mod._emu_web_render(1);
    }
    mod._free(audio);
    if (!pixels) fail('emulator produced no frame');

    /* Check before judging the image: an unresolved symbol makes the pixels meaningless, and the
     * blank-screen message below would blame the tapp's splash screen for it. */
    if (unresolved.size) {
        fail(
            `the deployed emulator does not implement: ${[...unresolved].sort().join(', ')}\n` +
            '       Those calls returned junk, so any screenshot from this run is meaningless.\n' +
            '       The emulator is behind the tapp API — redeploy the builder page\n' +
            '       (make dist && make deploy in lib/tapp/builder/web) and re-run.',
        );
    }

    const rgba = Buffer.from(mod.HEAPU8.subarray(pixels, pixels + W * H * 4));
    /* An all-one-colour capture means the tapp never drew: a blank card on the site would look
     * like a site bug rather than a tapp that needs more warm-up frames. Say so instead. */
    const first = rgba.readUInt32LE(0);
    let uniform = true;
    for (let i = 4; i < rgba.length && uniform; i += 4) uniform = rgba.readUInt32LE(i) === first;
    if (uniform) {
        fail(`screen is blank after ${frames} frames (${(frames / GUEST_FPS).toFixed(1)}s) — ` +
             'raise screenshot_frames in the entry if it opens on a splash');
    }

    writeFileSync(pngPath, encodePng(scaleToRgb(rgba, SCALE), W * SCALE, H * SCALE));
    const st = status();
    console.log(`✓ ${pngPath} (${W * SCALE}x${H * SCALE}, ${frames} frames` +
                `${st ? `, status: ${st}` : ''})`);
}

main().then(() => process.exit(0), (e) => fail(e.stack ?? e.message));