import { app } from "../../scripts/app.js";
import { getPngMetadata, getWebpMetadata, importA1111, getLatentMetadata } from "../../scripts/pnginfo.js";
import exifr from "./exifr.js";
import { readJxlMetadata } from "./jxl_metadata.js";

// Loads a workflow / API prompt out of an {"prompt":..., "workflow":...} object.
async function loadFromMetadata(ctx, info) {
	if (!info) return false;
	if (info.workflow) {
		await ctx.loadGraphData(info.workflow);
		return true;
	}
	if (info.prompt) {
		await ctx.loadApiJson(info.prompt);
		return true;
	}
	return false;
}

// Returns true when this extension handled the file, so other handlers can run otherwise.
async function handleFile(file) {
	if (file.type === "image/png") {
		const pngInfo = await getPngMetadata(file);
		if (pngInfo) {
			if (pngInfo.workflow) {
				await this.loadGraphData(JSON.parse(pngInfo.workflow));
			} else if (pngInfo.prompt) {
				this.loadApiJson(JSON.parse(pngInfo.prompt));
			} else if (pngInfo.parameters) {
				importA1111(this.graph, pngInfo.parameters);
			}
		}
		return true;
	} else if (file.type === "image/webp") {
		const pngInfo = await getWebpMetadata(file);
		if (pngInfo) {
			if (pngInfo.workflow) {
				await this.loadGraphData(JSON.parse(pngInfo.workflow));
			} else if (pngInfo.Workflow) {
				await this.loadGraphData(JSON.parse(pngInfo.Workflow));
				// Support loading workflows from that webp custom node.
			} else if (pngInfo.prompt) {
				this.loadApiJson(JSON.parse(pngInfo.prompt));
			} else if (pngInfo.Prompt) {
				this.loadApiJson(JSON.parse(pngInfo.Prompt)); // Support loading prompts from that webp custom node.
			}
		}
		return true;
	} else if (file.type === "image/jxl" || file.name?.toLowerCase().endsWith(".jxl")) {
		// jxlpy/jpeg-xl output: metadata lives in the Exif box, which exifr cannot read.
		await loadFromMetadata(this, await readJxlMetadata(file));
		return true;
	} else if (["image/avif", "image/jpeg"].includes(file.type)) {
		// ("image/avif" || "image/jpeg" || ...) used to evaluate to "image/avif" alone,
		// so jpeg and jxl never reached this branch at all.
		const parsed = await exifr.parse(file);
		const userComment = parsed?.[0x9286];
		if (userComment) {
			try {
				await loadFromMetadata(this, JSON.parse(userComment));
			} catch (e) {
				console.warn("Extended Image Formats: unreadable EXIF metadata", e);
			}
		}
		return true;
	} else if (file.type === "application/json" || file.name?.endsWith(".json")) {
		const reader = new FileReader();
		reader.onload = async () => {
			const jsonContent = JSON.parse(reader.result);
			if (jsonContent?.templates) {
				this.loadTemplateData(jsonContent);
			} else if (this.isApiJson(jsonContent)) {
				this.loadApiJson(jsonContent);
			} else {
				await this.loadGraphData(jsonContent);
			}
		};
		reader.readAsText(file);
		return true;
	} else if (file.name?.endsWith(".latent") || file.name?.endsWith(".safetensors")) {
		const info = await getLatentMetadata(file);
		if (info.workflow) {
			await this.loadGraphData(JSON.parse(info.workflow));
		} else if (info.prompt) {
			this.loadApiJson(JSON.parse(info.prompt));
		}
		return true;
	}
	return false;
}


const ext = {
	// Unique name for the extension
	name: "Comfyui_ExtendedImageFormats",
	async init(app) {
		// Any initial setup to run as soon as the page loads
		const previous = app.handleFile;
		app.handleFile = async function (file, ...rest) {
			// Anything we don't recognise goes to whatever handled it before, instead of
			// being swallowed (this hook is what loads dropped files in the first place).
			if (await handleFile.call(this, file)) return;
			if (previous) return previous.call(this, file, ...rest);
		};
	}
};

app.registerExtension(ext);
