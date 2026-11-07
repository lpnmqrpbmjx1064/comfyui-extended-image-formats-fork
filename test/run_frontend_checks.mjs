// Verifies the SHIPPED frontend files, not a copy of the logic.
// The mirror below contains byte-identical copies of web/*.js (hash-checked below)
// plus stubs for the two ComfyUI modules this extension imports, so the real
// dispatch code runs and we can see which loader it calls with what.
import { copyFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..", "web");
const OUT = process.env.MIRROR_DIR ?? join(tmpdir(), "eif-fe-mirror");
const SAVED = process.env.SAVED_DIR ?? join(tmpdir(), "eif-test-comfy_output");
if (!process.env.SAVED_DIR) {
	console.log(`  note: SAVED_DIR not set, assuming ${SAVED}`);
}

const fail = [];

// exifr's blob reader uses FileReader, which exists in a browser but not in Node.
// Without this the AVIF/JPEG branches would be untestable here (not a code defect).
if (typeof globalThis.FileReader === "undefined") {
	globalThis.FileReader = class {
		readAsArrayBuffer(blob) {
			blob.arrayBuffer().then(
				(buffer) => {
					this.result = buffer;
					this.onloadend?.();
				},
				(error) => this.onerror?.(error)
			);
		}
	};
}
const check = (name, ok, detail = "") => {
	console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? " — " + detail : ""}`);
	if (!ok) fail.push(name);
};
const sha = (p) => createHash("sha256").update(readFileSync(p)).digest("hex").slice(0, 16);

rmSync(OUT, { recursive: true, force: true });
mkdirSync(join(OUT, "scripts"), { recursive: true });
mkdirSync(join(OUT, "pkg", "web"), { recursive: true });

// --- mirror the shipped files, then prove the copies are identical ------------
writeFileSync(join(OUT, "package.json"), '{"type":"module"}');
for (const f of ["extended_image_formats.js", "jxl_metadata.js", "exifr.js"]) {
	copyFileSync(join(SRC, f), join(OUT, "pkg", "web", f));
	check(`mirror ${f} is byte-identical to the shipped file`,
		sha(join(SRC, f)) === sha(join(OUT, "pkg", "web", f)), sha(join(SRC, f)));
}

// --- stubs for the ComfyUI modules this extension imports --------------------
writeFileSync(join(OUT, "scripts", "app.js"), "export const app = { registerExtension() {} };\n");
writeFileSync(
	join(OUT, "scripts", "pnginfo.js"),
	`export async function getPngMetadata(file) {
		return { workflow: JSON.stringify({ nodes: [{ id: 1, type: "FromPng" }] }) };
	}
	export async function getWebpMetadata(file) { return {}; }
	export function importA1111() {}
	export async function getLatentMetadata(file) { return {}; }`
);

// --- capture the extension's init(app) BEFORE importing it --------------------
// The extension calls app.registerExtension at module import time, so the stub has
// to be in place first.
let registered = null;
const { app } = await import(join(OUT, "scripts", "app.js"));
app.registerExtension = (ext) => {
	registered = ext;
};

const mod = await import(join(OUT, "pkg", "web", "extended_image_formats.js") + "?v=" + Date.now());
const jxlMod = await import(join(OUT, "pkg", "web", "jxl_metadata.js") + "?v=" + Date.now());
check("extension registered itself with ComfyUI", registered !== null);
check("extension keeps its original unique name",
	registered?.name === "Comfyui_ExtendedImageFormats", String(registered?.name));

const calls = { loadGraphData: [], loadApiJson: [], importA1111: 0, previous: [] };
const fakeApp = {
	graph: {},
	loadGraphData: (g) => calls.loadGraphData.push(g),
	loadApiJson: (j) => calls.loadApiJson.push(j),
	isApiJson: () => false,
	loadTemplateData: () => {},
	async handleFile() {
		calls.previous.push("original");
	},
};
const original = fakeApp.handleFile;
await registered.init(fakeApp);
check("init replaced app.handleFile", fakeApp.handleFile !== original);
check("init kept a reference to the previous handler", typeof fakeApp.handleFile === "function");

const reset = () => {
	calls.loadGraphData.length = 0;
	calls.loadApiJson.length = 0;
	calls.previous.length = 0;
};
const fileFrom = (path, name, type) => new File([readFileSync(path)], name, { type });

// --- 1. the JXL path: workflow must load from the Exif box -------------------
console.log("\n=== JXL drop ===");
reset();
await fakeApp.handleFile(fileFrom(join(SAVED, "jxl_q90_00001_.jxl"), "jxl_q90_00001_.jxl", "image/jxl"));
check("handleFile(jxl) loaded a graph", calls.loadGraphData.length === 1,
	`loadGraphData x${calls.loadGraphData.length}`);
check("loaded graph is the saved workflow",
	JSON.stringify(calls.loadGraphData[0]) === JSON.stringify({ nodes: [{ id: 1, type: "KSampler" }], links: [] }),
	JSON.stringify(calls.loadGraphData[0]).slice(0, 80));
check("JXL drop did not fall through to the previous handler", calls.previous.length === 0);

reset();
await fakeApp.handleFile(fileFrom(join(SAVED, "jxl_q100_00001_.jxl"), "x.jxl", "image/jxl"));
check("lossless JXL also loads its workflow", calls.loadGraphData.length === 1);

reset();
await fakeApp.handleFile(fileFrom(join(SAVED, "batch_00002_.jxl"), "b.jxl", "image/jxl"));
check("second image of a batch loads its own workflow", calls.loadGraphData.length === 1);

// empty MIME (common on Windows) must still be recognised by extension
reset();
await fakeApp.handleFile(fileFrom(join(SAVED, "jxl_q90_00001_.jxl"), "no_mime.jxl", ""));
check("JXL recognised by filename when file.type is empty", calls.loadGraphData.length === 1);

// --- 2. metadata-free JXL must not throw ------------------------------------
console.log("\n=== negative controls ===");
reset();
let threw = null;
try {
	await fakeApp.handleFile(fileFrom(join(SAVED, "nometa_00001_.jxl"), "nometa.jxl", "image/jxl"));
} catch (e) {
	threw = e;
}
check("JXL without metadata does not throw", threw === null, threw ? String(threw.message) : "");
check("JXL without metadata loads nothing", calls.loadGraphData.length === 0);

// truncated file: no crash, no load
const truncated = join(OUT, "truncated.jxl");
writeFileSync(truncated, readFileSync(join(SAVED, "jxl_q90_00001_.jxl")).subarray(0, 40));
reset();
threw = null;
try {
	await fakeApp.handleFile(fileFrom(truncated, "truncated.jxl", "image/jxl"));
} catch (e) {
	threw = e;
}
check("truncated JXL does not throw", threw === null, threw ? String(threw.message) : "");
check("truncated JXL loads nothing", calls.loadGraphData.length === 0);

// a PNG renamed to .jxl must not crash the parser
const fake = join(OUT, "actually_png.jxl");
writeFileSync(fake, readFileSync(join(SAVED, "f_png_00001_.png")));
reset();
threw = null;
try {
	await fakeApp.handleFile(fileFrom(fake, "actually_png.jxl", "image/jxl"));
} catch (e) {
	threw = e;
}
check("PNG bytes named .jxl do not throw", threw === null, threw ? String(threw.message) : "");
check("PNG bytes named .jxl load nothing", calls.loadGraphData.length === 0);

// bare codestream (no container boxes at all)
const bare = join(OUT, "bare_codestream.jxl");
const fullJxl = readFileSync(join(SAVED, "jxl_q90_00001_.jxl"));
writeFileSync(bare, Buffer.concat([Buffer.from([0xff, 0x0a]), fullJxl.subarray(64, 400)]));
reset();
threw = null;
try {
	await fakeApp.handleFile(fileFrom(bare, "bare.jxl", "image/jxl"));
} catch (e) {
	threw = e;
}
check("bare codestream does not throw", threw === null, threw ? String(threw.message) : "");

// --- 3. the regression: other formats must still work, and unknown must pass through
console.log("\n=== dispatch / delegation ===");
reset();
await fakeApp.handleFile(fileFrom(join(SAVED, "f_png_00001_.png"), "f.png", "image/png"));
check("png still loads via getPngMetadata", calls.loadGraphData.length === 1);

reset();
await fakeApp.handleFile(fileFrom(join(SAVED, "f_avif_00001_.avif"), "f.avif", "image/avif"));
check("avif EXIF path works (the mime test used to match avif only)",
	calls.loadGraphData.length === 1, `loadGraphData x${calls.loadGraphData.length}`);

reset();
await fakeApp.handleFile(fileFrom(join(SAVED, "f_jpeg_00001_.jpeg"), "f.jpeg", "image/jpeg"));
check("jpeg now reaches the EXIF branch (previously unreachable)", calls.loadGraphData.length === 1,
	`loadGraphData x${calls.loadGraphData.length}`);

reset();
const unknown = join(OUT, "movie.webm");
writeFileSync(unknown, Buffer.from("not an image"));
await fakeApp.handleFile(fileFrom(unknown, "movie.webm", "video/webm"));
check("unknown type is delegated to the previous handler", calls.previous.length === 1,
	`previous x${calls.previous.length}`);
check("unknown type loads no graph", calls.loadGraphData.length === 0);

// --- 4. the parser in isolation --------------------------------------------
console.log("\n=== parser unit checks ===");
const buf = readFileSync(join(SAVED, "jxl_q90_00001_.jxl"));
const tiff = jxlMod.findExifTiff(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
check("findExifTiff returns a TIFF stream", tiff && tiff.byteLength > 50, `${tiff?.byteLength} B`);
check("readUserComment returns JSON", (() => {
	const s = jxlMod.readUserComment(tiff);
	try { return JSON.parse(s).workflow.nodes[0].type === "KSampler"; } catch { return false; }
})());
check("readJxlMetadata accepts an ArrayBuffer too",
	(await jxlMod.readJxlMetadata(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)))?.workflow?.nodes?.[0]?.type === "KSampler");
check("readJxlMetadata on a PNG buffer returns null",
	(await jxlMod.readJxlMetadata(readFileSync(join(SAVED, "f_png_00001_.png")))) === null);

console.log("\n" + (fail.length === 0 ? "ALL FRONTEND CHECKS PASSED" : `FAILURES (${fail.length}): ${fail}`));
process.exit(fail.length === 0 ? 0 : 1);
