"""Verify the forked node by running it inside real ComfyUI on CPU.

Not a simulation: ComfyUI's own `SaveImage`, its real `folder_paths` output
plumbing, its real `--disable-metadata` flag, and the real encoders.

Checks, per format: the file is written, it decodes, quality actually moves the
size/diff, lossless is bit-exact where claimed, and the workflow comes back out of
the file by the same route the browser will use.
"""
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMFY = os.environ.get("COMFY_DIR") or os.path.join(os.path.dirname(REPO), "ComfyUI")
NODE = os.environ.get("NODE_DIR") or REPO
WORK = os.environ.get("WORK_DIR") or tempfile.mkdtemp(prefix="eif-test-")
OUTDIR = os.path.join(WORK, "comfy_output")

# Fail with a usable message: an inherited-but-stale NODE_DIR/COMFY_DIR otherwise
# surfaces as a bare FileNotFoundError deep inside importlib.
if not os.path.isfile(os.path.join(NODE, "__init__.py")):
    sys.exit(f"NODE_DIR does not look like the custom node ({NODE}/__init__.py missing)")
if not os.path.isfile(os.path.join(COMFY, "nodes.py")):
    sys.exit(f"COMFY_DIR does not look like a ComfyUI checkout ({COMFY}/nodes.py missing)")

shutil.rmtree(OUTDIR, ignore_errors=True)
os.makedirs(OUTDIR, exist_ok=True)

# --- ComfyUI boot order (see skill: device is chosen at import time) -----------
sys.path.insert(0, COMFY)
os.chdir(COMFY)
sys.argv = ["main.py", "--cpu", "--output-directory", OUTDIR]
import comfy.options  # noqa: E402
comfy.options.enable_args_parsing()
import comfy.model_management  # noqa: E402,F401
import folder_paths  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from comfy.cli_args import args  # noqa: E402

# --- load the node the way ComfyUI's nodes.py loader does ---------------------
spec = importlib.util.spec_from_file_location(
    "eif_pkg", os.path.join(NODE, "__init__.py"), submodule_search_locations=[NODE]
)
mod = importlib.util.module_from_spec(spec)
sys.modules["eif_pkg"] = mod
spec.loader.exec_module(mod)

fail, warn = [], []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fail.append(name)


print("=== registration ===")
check("NODE_CLASS_MAPPINGS has both nodes", set(mod.NODE_CLASS_MAPPINGS) == {"ExtendedSaveImage", "DDSSaveImage"},
      str(sorted(mod.NODE_CLASS_MAPPINGS)))
check("WEB_DIRECTORY declared for the frontend extension", mod.WEB_DIRECTORY == "./web/")
check("jxl registered in the file_drop path", os.path.exists(os.path.join(NODE, "web", "jxl_metadata.js")))

EIF = mod.NODE_CLASS_MAPPINGS["ExtendedSaveImage"]
it = EIF.INPUT_TYPES()["required"]
print(f"  formats offered: {it['format'][0]}")
check("quality widget present", "quality" in it, str(it.get("quality")))
check("quality default is 90", it["quality"][1]["default"] == 90)
check("quality range 1..100", (it["quality"][1]["min"], it["quality"][1]["max"]) == (1, 100))
check("jxl offered (pillow-jxl-plugin importable)", "jxl" in it["format"][0])

print("\n=== pure function: encoder_quality_args (both directions) ===")
eq = mod.extended_image_formats.encoder_quality_args
check("jxl q=100 -> lossless=True", eq("jxl", 100) == {"lossless": True}, str(eq("jxl", 100)))
check("jxl q=99  -> quality=99", eq("jxl", 99) == {"quality": 99})
check("jxl q=100 never emits quality=100 (that raises in the encoder)",
      "quality" not in eq("jxl", 100))
check("webp q=100 -> lossless=True", eq("webp", 100) == {"lossless": True})
check("webp q=50 -> quality=50", eq("webp", 50) == {"quality": 50})
check("avif q=100 -> quality=100 + 4:4:4", eq("avif", 100) == {"quality": 100, "subsampling": "4:4:4"},
      str(eq("avif", 100)))
check("jpeg q=100 -> quality=100 (always lossy)", eq("jpeg", 100) == {"quality": 100})
check("png returns no encoder args", eq("png", 100) == {})
check("dds returns no encoder args", eq("dds", 50) == {})
check("quality is clamped low", eq("jpeg", 0) == {"quality": 1} and eq("jpeg", -9) == {"quality": 1})
check("quality is clamped high", eq("jpeg", 101) == {"quality": 100})
check("quality accepts a float from a widget round-trip", eq("jpeg", 88.7) == {"quality": 88})

print("\n=== real save_images runs ===")
rng = np.random.default_rng(7)
base = np.linspace(0, 255, 64, dtype=np.float32)
src = np.zeros((64, 64, 3), dtype=np.uint8)
src[..., 0] = base[None, :]
src[..., 1] = base[:, None]
src[..., 2] = rng.integers(0, 256, (64, 64))
tensor = torch.from_numpy(src.astype(np.float32) / 255.0)[None]

PROMPT = {"1": {"class_type": "KSampler", "inputs": {"seed": 12345}}}
WORKFLOW = {"nodes": [{"id": 1, "type": "KSampler"}], "links": []}
EXTRA = {"workflow": WORKFLOW}

node = EIF()
# folder_paths in this ComfyUI revision ignores --output-directory for its output
# root, so pin the node's own output dir to keep the run sandboxed.
node.output_dir = OUTDIR


def find_saved(res):
    name = res["ui"]["images"][0]["filename"]
    return os.path.join(OUTDIR, name)


made = {}
print("\n  -- jxl: quality sweep + metadata --")
for q in (1, 50, 90, 100):
    res = node.save_images(tensor, filename_prefix=f"jxl_q{q}", format="jxl", quality=q,
                           prompt=PROMPT, extra_pnginfo=EXTRA)
    path = find_saved(res)
    made[("jxl", q)] = path
    from PIL import Image
    with Image.open(path) as im:
        back = np.asarray(im.convert("RGB"), dtype=np.int16)
    diff = int(np.abs(back - src.astype(np.int16)).max())
    print(f"     q={q:<3} {os.path.getsize(path):>6} B  max|diff|={diff}")
    if q == 100:
        check("jxl q=100 lossless is bit-exact", diff == 0, f"max|diff|={diff}")
    if q == 1:
        check("jxl q=1 is lossy", diff > 0, f"max|diff|={diff}")

from PIL import Image  # noqa: E402

sizes = {q: os.path.getsize(made[("jxl", q)]) for q in (1, 50, 90)}
check("jxl size grows with quality", sizes[1] < sizes[50] < sizes[90], str(sizes))

# metadata via Pillow
for q in (90, 100):
    got = Image.open(made[("jxl", q)]).getexif().get(0x9286)
    parsed = json.loads(got) if got else {}
    check(f"jxl q={q}: EXIF UserComment round-trips", bool(got))
    check(f"jxl q={q}: workflow survives", parsed.get("workflow") == WORKFLOW)
    check(f"jxl q={q}: prompt survives", parsed.get("prompt") == PROMPT)


def jxl_boxes(data):
    if data[:2] == b"\xff\x0a":
        return [("<bare>", 0, len(data))]
    out, off = [], 12
    while off + 8 <= len(data):
        size = struct.unpack(">I", data[off:off + 4])[0]
        t = data[off + 4:off + 8].decode("latin-1")
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            hdr = 16
        elif size == 0:
            size = len(data) - off
        if size < hdr or off + size > len(data):
            break
        out.append((t, off, size))
        off += size
    return out


blob = open(made[("jxl", 90)], "rb").read()
types = [b[0] for b in jxl_boxes(blob)]
check("jxl file is a boxed container with an Exif box", "Exif" in types, str(types))

print("\n  -- other formats at q=90 --")
for fmt in ("png", "jpeg", "webp", "avif", "dds"):
    try:
        res = node.save_images(tensor, filename_prefix=f"f_{fmt}", format=fmt, quality=90,
                               prompt=PROMPT, extra_pnginfo=EXTRA)
        path = find_saved(res)
        made[(fmt, 90)] = path
        size = os.path.getsize(path)
        if fmt == "png":
            info = Image.open(path).info
            wf = json.loads(info.get("workflow", "null"))
            check("png: workflow text chunk present", wf == WORKFLOW)
            check("png: prompt text chunk present", json.loads(info.get("prompt", "null")) == PROMPT)
        elif fmt == "dds":
            check("dds: written (no metadata by design)", size > 0, f"{size} B")
        else:
            got = Image.open(path).getexif().get(0x9286)
            parsed = json.loads(got) if got else {}
            check(f"{fmt}: EXIF UserComment round-trips", bool(got), f"{size} B")
            check(f"{fmt}: workflow survives", parsed.get("workflow") == WORKFLOW)
    except Exception as exc:
        check(f"{fmt}: save runs", False, f"{type(exc).__name__}: {exc}")

print("\n  -- webp/avif/jpeg quality response --")
for fmt in ("webp", "avif", "jpeg"):
    try:
        lo = find_saved(node.save_images(tensor, filename_prefix=f"q1_{fmt}", format=fmt,
                                         quality=1, prompt=PROMPT, extra_pnginfo=EXTRA))
        hi = find_saved(node.save_images(tensor, filename_prefix=f"q100_{fmt}", format=fmt,
                                         quality=100, prompt=PROMPT, extra_pnginfo=EXTRA))
        check(f"{fmt}: q=1 smaller than q=100",
              os.path.getsize(lo) < os.path.getsize(hi),
              f"{os.path.getsize(lo)} < {os.path.getsize(hi)}")
    except Exception as exc:
        check(f"{fmt}: quality sweep runs", False, f"{type(exc).__name__}: {exc}")

try:
    wl = Image.open(find_saved(node.save_images(tensor, filename_prefix="wl", format="webp",
                                                quality=100, prompt=PROMPT, extra_pnginfo=EXTRA)))
    d = int(np.abs(np.asarray(wl.convert("RGB"), np.int16) - src.astype(np.int16)).max())
    check("webp q=100 is bit-exact (lossless)", d == 0, f"max|diff|={d}")
except Exception as exc:
    check("webp q=100 is bit-exact (lossless)", False, f"{type(exc).__name__}: {exc}")

print("\n=== negative controls / regressions upstream had ===")
try:
    res = node.save_images(tensor, filename_prefix="no_extra", format="jxl", quality=90,
                           prompt=PROMPT, extra_pnginfo=None)
    got = Image.open(find_saved(res)).getexif().get(0x9286)
    check("jxl with extra_pnginfo=None does not crash (upstream raised KeyError)",
          json.loads(got).get("prompt") == PROMPT)
except Exception as exc:
    check("jxl with extra_pnginfo=None does not crash (upstream raised KeyError)", False,
          f"{type(exc).__name__}: {exc}")

try:
    res = node.save_images(tensor, filename_prefix="nothing", format="jpeg", quality=90,
                           prompt=None, extra_pnginfo=None)
    check("jpeg with no metadata at all still saves", os.path.getsize(find_saved(res)) > 0)
except Exception as exc:
    check("jpeg with no metadata at all still saves", False, f"{type(exc).__name__}: {exc}")

try:
    res = node.save_images(tensor, filename_prefix="api_mode", format="jxl", quality=90,
                           prompt=PROMPT, extra_pnginfo={"workflow": None})
    check("jxl with workflow=None (api-mode prompt only) saves",
          os.path.getsize(find_saved(res)) > 0)
except Exception as exc:
    check("jxl with workflow=None (api-mode prompt only) saves", False,
          f"{type(exc).__name__}: {exc}")

saved_flag = args.disable_metadata
try:
    args.disable_metadata = True
    res = node.save_images(tensor, filename_prefix="nometa", format="jxl", quality=90,
                           prompt=PROMPT, extra_pnginfo=EXTRA)
    p = find_saved(res)
    got = Image.open(p).getexif().get(0x9286)
    check("--disable-metadata writes no EXIF", got is None, f"exif={got!r}")
    check("--disable-metadata still writes the image", os.path.getsize(p) > 0)
finally:
    args.disable_metadata = saved_flag

print("\n=== batch of 3 images ===")
batch = torch.cat([tensor, tensor, tensor], dim=0)
res = node.save_images(batch, filename_prefix="batch", format="jxl", quality=90,
                       prompt=PROMPT, extra_pnginfo=EXTRA)
names = [i["filename"] for i in res["ui"]["images"]]
check("batch writes 3 files", len(names) == 3 and len({*names}) == 3, str(names))
check("all batch files carry metadata",
      all(json.loads(Image.open(os.path.join(OUTDIR, n)).getexif().get(0x9286))["workflow"] == WORKFLOW
          for n in names))

print("\n" + ("ALL CHECKS PASSED" if not fail else f"FAILURES ({len(fail)}): {fail}"))
# The frontend harness consumes the files this run produced.
print(f"OUTPUT_DIR={OUTDIR}")
sys.exit(0 if not fail else 1)
