// Minimal JPEG XL metadata reader for ComfyUI.
//
// exifr (the library the rest of this extension uses) cannot read .jxl: it parses
// JPEG/AVIF/HEIC containers and fails with "Unknown file format" even when handed a
// bare TIFF payload. So this walks the JXL container boxes down to the Exif box and
// reads tag 0x9286 (UserComment) out of the TIFF IFD, which is where
// extended_image_formats.py writes {"prompt": ..., "workflow": ...}.

// Exif box payloads differ between encoders: raw TIFF, a 4-byte pad, or a 4-byte
// offset pointing at the TIFF header (the HEIF-style layout).
export function findExifTiff(buffer) {
	if (buffer.byteLength < 12) return null;
	const dv = new DataView(buffer);
	const u32 = (o) => dv.getUint32(o, false);
	const str4 = (o) =>
		String.fromCharCode(dv.getUint8(o), dv.getUint8(o + 1), dv.getUint8(o + 2), dv.getUint8(o + 3));
	const isTiff = (o) =>
		o + 4 <= buffer.byteLength &&
		((dv.getUint8(o) === 0x49 && dv.getUint8(o + 1) === 0x49 && dv.getUint16(o + 2, true) === 42) ||
			(dv.getUint8(o) === 0x4d && dv.getUint8(o + 1) === 0x4d && dv.getUint16(o + 2, false) === 42));

	// 0xFF0A = bare codestream, which has no boxes and therefore no metadata.
	if (dv.getUint8(0) === 0xff && dv.getUint8(1) === 0x0a) return null;
	if (str4(4) !== "JXL ") return null;

	let off = 12; // past the 12-byte "JXL " signature box
	while (off + 8 <= buffer.byteLength) {
		let size = u32(off);
		const box = str4(off + 4);
		let header = 8;
		if (size === 1) {
			size = Number(dv.getBigUint64(off + 8, false));
			header = 16;
		} else if (size === 0) {
			size = buffer.byteLength - off;
		}
		if (size < header || off + size > buffer.byteLength) break;
		if (box === "Exif") {
			const start = off + header;
			const end = off + size;
			for (const candidate of [start, start + 4, start + 4 + u32(start)]) {
				if (candidate < end && isTiff(candidate)) return buffer.slice(candidate, end);
			}
			return null;
		}
		off += size;
	}
	return null;
}

export function readUserComment(tiff) {
	const dv = new DataView(tiff);
	if (tiff.byteLength < 8) return null;
	const little = dv.getUint8(0) === 0x49; // "II" = little endian
	const u16 = (o) => dv.getUint16(o, little);
	const u32 = (o) => dv.getUint32(o, little);
	const ifd = u32(4);
	if (ifd + 2 > tiff.byteLength) return null;
	const count = u16(ifd);
	const typeSize = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8 };
	for (let i = 0; i < count; i++) {
		const entry = ifd + 2 + i * 12;
		if (entry + 12 > tiff.byteLength) break;
		if (u16(entry) !== 0x9286) continue; // UserComment
		const total = u32(entry + 4) * (typeSize[u16(entry + 2)] ?? 1);
		const from = total <= 4 ? entry + 8 : u32(entry + 8);
		let out = "";
		for (let k = 0; k < total && from + k < tiff.byteLength; k++) {
			out += String.fromCharCode(dv.getUint8(from + k));
		}
		// A1111-style writers prefix an 8-byte character-code header; ComfyUI's own
		// PNG info does not, but tolerate both.
		if (/^(ASCII|UNICODE|JIS|\x00{8})/.test(out)) out = out.slice(8);
		return out.replace(/\x00+$/, "");
	}
	return null;
}

// Returns the parsed {"prompt":..., "workflow":...} object, or null.
export async function readJxlMetadata(blob) {
	let buffer;
	if (blob instanceof ArrayBuffer) {
		buffer = blob;
	} else if (ArrayBuffer.isView(blob)) {
		// Uint8Array / Node Buffer
		buffer = blob.buffer.slice(blob.byteOffset, blob.byteOffset + blob.byteLength);
	} else if (typeof blob?.arrayBuffer === "function") {
		buffer = await blob.arrayBuffer();
	} else {
		return null;
	}
	const tiff = findExifTiff(buffer);
	if (!tiff) return null;
	const raw = readUserComment(tiff);
	if (!raw) return null;
	try {
		return JSON.parse(raw);
	} catch (e) {
		console.warn("Extended Image Formats: unreadable JXL metadata", e);
		return null;
	}
}
