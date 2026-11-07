from nodes import SaveImage
import json
from PIL import Image
import folder_paths
import imageio
import numpy as np
from PIL.PngImagePlugin import PngInfo
import os
from comfy.cli_args import args


avif_supported = False
jxl_supported = False
jxl_metadata = False

# Avif
try:
    import pillow_avif  # noqa: F401
except:  # noqa: E722
    pass
else:
    avif_supported = True

# Jxl
# pillow-jxl-plugin writes EXIF and exposes quality/lossless; jxlpy cannot write EXIF
# at all (its own support table lists "Support EXIF metadata: Failed"). jxlpy is kept
# only so an existing install keeps working, without metadata.
try:
    import pillow_jxl  # noqa: F401
except:  # noqa: E722
    try:
        from jxlpy import JXLImagePlugin  # noqa: F401
    except:  # noqa: E722
        pass
    else:
        jxl_supported = True
else:
    jxl_supported = True
    jxl_metadata = True


def encoder_quality_args(format, quality):
    """Map the single quality slider onto each encoder's real options.

    Measured against the installed encoders, not assumed:
      jxl  - quality=100 raises "The encoder API is used in an incorrect way", so
             lossless has to be requested explicitly with lossless=True (bit-exact).
      webp - quality=100 on its own is still lossy; lossless=True is bit-exact.
      avif - pillow-avif-plugin has no lossless mode; quality=100 with 4:4:4 chroma
             is as close as it gets, and is not bit-exact.
      jpeg - always lossy, 100 is just its best setting.
    png and dds never reach here: png keeps ComfyUI's compress_level, and dds has no
    quality knob.
    """
    quality = max(1, min(100, int(quality)))
    if format in ("jxl", "webp"):
        return {"lossless": True} if quality >= 100 else {"quality": quality}
    if format == "avif":
        if quality >= 100:
            return {"quality": 100, "subsampling": "4:4:4"}
        return {"quality": quality}
    if format == "jpeg":
        return {"quality": quality}
    return {}


class ExtendedSaveImage(SaveImage):
    @classmethod
    def INPUT_TYPES(s):
        output = {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "format": (["png", "jpeg", "webp", "dds"],),
                "quality": (
                    "INT",
                    {
                        "default": 90,
                        "min": 1,
                        "max": 100,
                        "step": 1,
                        "tooltip": "Encoder quality: 1 is smallest and most lossy, 100 is "
                        "lossless where the format supports it (jxl, webp). png and dds ignore it.",
                    },
                ),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

        if avif_supported:
            output["required"]["format"][0].append("avif")
        if jxl_supported:
            output["required"]["format"][0].append("jxl")

        return output

    RETURN_TYPES = ()

    FUNCTION = "save_images"

    CATEGORY = "image"

    def save_images(
        self,
        images,
        filename_prefix="ComfyUI",
        format="png",
        quality=90,
        prompt=None,
        extra_pnginfo=None,
    ):
        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = (
            folder_paths.get_save_image_path(
                filename_prefix, self.output_dir, images[0].shape[1], images[0].shape[0]
            )
        )
        results = list()
        for batch_number, image in enumerate(images):
            i = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
            metadata = None
            exif_bytes = None
            if not args.disable_metadata:
                metadata = PngInfo()
                if prompt is not None:
                    metadata.add_text("prompt", json.dumps(prompt))
                if extra_pnginfo is not None:
                    for x in extra_pnginfo:
                        metadata.add_text(x, json.dumps(extra_pnginfo[x]))

                # Every non-png format carries its metadata as an EXIF UserComment.
                # Build it as *bytes*: the jxl plugin rejects a Pillow Exif object,
                # while bytes work for jxl, jpeg, webp and avif alike.
                exif_payload = {}
                if prompt is not None:
                    exif_payload["prompt"] = prompt
                if extra_pnginfo is not None:
                    for key in extra_pnginfo:
                        exif_payload[key] = extra_pnginfo[key]
                if exif_payload:
                    exif = Image.Exif()
                    exif[0x9286] = json.dumps(exif_payload)  # UserComment
                    exif_bytes = exif.tobytes()

            filename_with_batch_num = filename.replace("%batch_num%", str(batch_number))
            file = f"{filename_with_batch_num}_{counter:05}_.{format}"

            if format == "png":
                img.save(
                    os.path.join(full_output_folder, file),
                    pnginfo=metadata,
                    compress_level=self.compress_level,
                )

            elif format == "dds":
                # DDS format doesn't support metadata, so we just save the image.
                imageio.imwrite(os.path.join(full_output_folder, file), np.array(img))  # DDS saving
                print("DDS format does not support metadata.")

            else:
                save_args = encoder_quality_args(format, quality)
                if exif_bytes is not None:
                    save_args["exif"] = exif_bytes
                img.save(os.path.join(full_output_folder, file), **save_args)
                if format == "jxl" and not jxl_metadata:
                    print(
                        "JXL metadata needs pillow-jxl-plugin; jxlpy cannot write EXIF, "
                        "so this file was saved without its workflow."
                    )

            results.append(
                {"filename": file, "subfolder": subfolder, "type": self.type}
            )
            counter += 1

        return {"ui": {"images": results}}

class DDSSaveImage(SaveImage):
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "dds_compression": (["none", "bc1", "bc3", "bc5"], {"default": "none"}),
                "generate_mipmaps": (["true", "false"], {"default": "false"}),
                "sidecar_format": (["json", "xmp"], {"default": "json"}),  # Sidecar format option
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ()

    FUNCTION = "save_images"

    CATEGORY = "image"

    def save_images(
        self,
        images,
        filename_prefix="ComfyUI",
        dds_compression="none",
        generate_mipmaps="false",
        sidecar_format="json",  # Sidecar format choice (json or xmp)
        prompt=None,
        extra_pnginfo=None,
    ):
        filename_prefix += self.prefix_append
        full_output_folder, filename, counter, subfolder, filename_prefix = (
            folder_paths.get_save_image_path(
                filename_prefix, self.output_dir, images[0].shape[1], images[0].shape[0]
            )
        )
        results = list()
        for batch_number, image in enumerate(images):
            i = 255.0 * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            # Convert the image to an array for DDS format saving
            img_array = np.asarray(img)

            # DDS file handling

            filename_with_batch_num = filename.replace("%batch_num%", str(batch_number))
            file = f"{filename_with_batch_num}_{counter:05}_.dds"

            # DDS-specific options
            dds_settings = {
                'compression': dds_compression,
                'mipmaps': generate_mipmaps == "true"
            }

            # Use imageio to save the image in DDS format
            imageio.imwrite(os.path.join(full_output_folder, file), img_array, format='dds', **dds_settings)

            # Prepare metadata for sidecar file
            metadata = {}
            if prompt:
                metadata["prompt"] = prompt
            if extra_pnginfo:
                for key in extra_pnginfo:
                    metadata[key] = extra_pnginfo[key]

            # Save metadata to a sidecar file
            sidecar_file = f"{filename_with_batch_num}_{counter:05}.{sidecar_format}"

            # Save as JSON or XMP based on user selection
            sidecar_path = os.path.join(full_output_folder, sidecar_file)
            if sidecar_format == "json":
                with open(sidecar_path, 'w') as f:
                    json.dump(metadata, f, indent=4)
            elif sidecar_format == "xmp":
                xmp_data = self.convert_to_xmp(metadata)
                with open(sidecar_path, 'w') as f:
                    f.write(xmp_data)
            print(file)
            results.append(
                {"filename": file, "sidecar_file": sidecar_file, "subfolder": subfolder, "type": self.type}
            )
            counter += 1

        return {"ui": {"images": results}}

    def convert_to_xmp(self, metadata):
        """
        Convert the given metadata dictionary to an XMP format string.
        This is a basic implementation. You can extend it to conform to full XMP specifications.
        """
        xmp_template = """<?xpacket begin="ï»¿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
   <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <rdf:Description rdf:about=""
           xmlns:dc="http://purl.org/dc/elements/1.1/"
           xmlns:xmp="http://ns.adobe.com/xap/1.0/">
{entries}
      </rdf:Description>
   </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

        xmp_entries = []
        for key, value in metadata.items():
            xmp_entries.append(f'         <dc:{key}>{value}</dc:{key}>')

        return xmp_template.format(entries="\n".join(xmp_entries))
