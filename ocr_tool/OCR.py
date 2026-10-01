# OCR.py — Robust GLM-OCR local pipeline (Transformers)
#
# Default layout:
#   .\OCR.py
#   .\input\   (put 1 PDF OR images OR multiple PDFs if using --mode all_pdfs)
#
# Install (venv):
#   pip install -U git+https://github.com/huggingface/transformers.git accelerate torch torchvision pillow pdf2image
#
# Windows PDF note:
#   pdf2image requires Poppler. Either add Poppler bin to PATH or pass --poppler_path.

from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_ROOT = SCRIPT_DIR / "cache"


def _set_default_cache_env(cache_root: Path) -> None:
    hf_home = cache_root / "huggingface"
    hub_cache = hf_home / "hub"
    transformers_cache = hf_home / "transformers"
    torch_home = cache_root / "torch"
    for path in (cache_root, hf_home, hub_cache, transformers_cache, torch_home):
        path.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hub_cache))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(transformers_cache))
    os.environ.setdefault("TORCH_HOME", str(torch_home))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


_set_default_cache_env(CACHE_ROOT)

print = partial(builtins.print, flush=True)

import torch
from PIL import Image

try:
    from pdf2image import convert_from_path, pdfinfo_from_path
except Exception:
    convert_from_path = None
    pdfinfo_from_path = None

try:
    import fitz
except Exception:
    fitz = None

from transformers import AutoProcessor, AutoModelForImageTextToText


# ---------------- Defaults ----------------
DEFAULT_MODEL_ID = "zai-org/GLM-OCR"
DEFAULT_INPUT_DIRNAME = "input"
DEFAULT_OUTPUT_DIRNAME = "output"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

TASK_PREFIX = {
    "text": "Text Recognition:",
    "formula": "Formula Recognition:",
    "table": "Table Recognition:",
}

DEFAULT_TASK = "text"
DEFAULT_OUTPUT_FORMAT = "md"
# -----------------------------------------


@dataclass
class WorkItem:
    kind: str                     # "pdf_page" | "image"
    src_label: str                # for logs/headings
    page_num: Optional[int]       # for pdf pages
    media_path: Path              # actual file to pass to model in {"url": ...}


class PageSelectionError(RuntimeError):
    pass


class PageSelectionBoundsError(PageSelectionError):
    pass


def eprint(*a):
    print(*a, file=sys.stderr)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def list_images(folder: Path) -> List[Path]:
    imgs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(imgs, key=lambda p: natural_key(p.name))


def list_pdfs(folder: Path) -> List[Path]:
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"], key=lambda p: p.name.lower())


def file_sig(p: Path) -> str:
    st = p.stat()
    return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"


def dir_sig_images(folder: Path) -> str:
    imgs = list_images(folder)
    parts = [file_sig(p) for p in imgs]
    return "imgs::" + "|".join(parts)


def dir_sig_pdfs(folder: Path) -> str:
    pdfs = list_pdfs(folder)
    parts = [file_sig(p) for p in pdfs]
    return "pdfs::" + "|".join(parts)


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state_path: Path, state: dict):
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def pillow_lanczos():
    try:
        return Image.Resampling.LANCZOS
    except Exception:
        return Image.LANCZOS


def downscale_to_png(src_path: Path, dst_path: Path, max_side: Optional[int]) -> Path:
    img = Image.open(src_path).convert("RGB")
    if max_side and max_side > 0:
        w, h = img.size
        m = max(w, h)
        if m > max_side:
            scale = max_side / float(m)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            img = img.resize((nw, nh), pillow_lanczos())
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst_path, "PNG")
    return dst_path


def pdf_page_count(pdf_path: Path, poppler_path: Optional[str]) -> int:
    if pdfinfo_from_path is not None:
        try:
            info = pdfinfo_from_path(str(pdf_path), poppler_path=poppler_path)
            pages = int(info.get("Pages", 0))
            if pages > 0:
                return pages
        except Exception as exc:
            eprint(f"[WARN] pdfinfo failed for {pdf_path.name}; falling back to PyMuPDF: {exc}")

    if fitz is None:
        raise RuntimeError(
            "Could not determine PDF page count. Install Poppler for pdf2image or ensure PyMuPDF is available."
        )

    doc = fitz.open(str(pdf_path))
    try:
        return doc.page_count
    finally:
        doc.close()


def _render_pdf_pages_batch_fitz(
    pdf_path: Path,
    out_dir: Path,
    first_page: int,
    last_page: int,
    dpi: int,
) -> List[Tuple[int, Path]]:
    if fitz is None:
        raise RuntimeError(
            "No PDF renderer available. Install pdf2image + Poppler or ensure PyMuPDF is available."
        )

    ensure_dir(out_dir)
    scale = max(float(dpi) / 72.0, 0.1)
    matrix = fitz.Matrix(scale, scale)
    rendered: List[Tuple[int, Path]] = []

    doc = fitz.open(str(pdf_path))
    try:
        for page_num in range(first_page, last_page + 1):
            page = doc.load_page(page_num - 1)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            path = out_dir / f"page_{page_num:04d}.png"
            pix.save(str(path))
            rendered.append((page_num, path))
    finally:
        doc.close()

    return rendered


def render_pdf_pages_batch(
    pdf_path: Path,
    out_dir: Path,
    first_page: int,
    last_page: int,
    dpi: int,
    poppler_path: Optional[str],
) -> List[Tuple[int, Path]]:
    if convert_from_path is not None:
        try:
            ensure_dir(out_dir)
            pil_pages = convert_from_path(
                pdf_path=str(pdf_path),
                dpi=dpi,
                first_page=first_page,
                last_page=last_page,
                fmt="png",
                poppler_path=poppler_path,
            )
            out: List[Tuple[int, Path]] = []
            page = first_page
            for pil_img in pil_pages:
                p = out_dir / f"page_{page:04d}.png"
                pil_img.save(p, "PNG")
                out.append((page, p))
                page += 1
            return out
        except Exception as exc:
            eprint(f"[WARN] pdf2image render failed; falling back to PyMuPDF: {exc}")

    return _render_pdf_pages_batch_fitz(
        pdf_path=pdf_path,
        out_dir=out_dir,
        first_page=first_page,
        last_page=last_page,
        dpi=dpi,
    )


def init_model(
    model_id: str,
    force_fp16: bool,
    trust_remote_code: bool,
    use_fast: Optional[bool],
):
    if not torch.cuda.is_available():
        raise RuntimeError(
            "GPU-only OCR is enabled, but CUDA is not available in this environment. "
            "Install a CUDA-enabled PyTorch build in .venv and verify the NVIDIA GPU is visible."
        )

    device = torch.device("cuda:0")
    kwargs = {"trust_remote_code": trust_remote_code}
    if use_fast is not None:
        kwargs["use_fast"] = use_fast

    print(f"[MODEL] loading processor for {model_id} (use_fast={use_fast if use_fast is not None else 'auto'})")
    processor = AutoProcessor.from_pretrained(model_id, **kwargs)

    torch_dtype = torch.float16
    dtype_reason = "forced" if force_fp16 else "gpu_default"
    print(f"[MODEL] loading model weights for {model_id} (torch_dtype={torch_dtype}, reason={dtype_reason}, device={device})")
    model = AutoModelForImageTextToText.from_pretrained(
        pretrained_model_name_or_path=model_id,
        dtype=torch_dtype,
        trust_remote_code=trust_remote_code,
    )
    model = model.to(device)
    model.eval()

    model_device = getattr(model, "device", None)
    if model_device is None or model_device.type != "cuda":
        raise RuntimeError(f"GPU-only OCR requires a CUDA model device, but got {model_device}.")

    hf_device_map = getattr(model, "hf_device_map", None)
    if isinstance(hf_device_map, dict):
        non_cuda_targets = {name: target for name, target in hf_device_map.items() if "cuda" not in str(target)}
        if non_cuda_targets:
            raise RuntimeError(
                "GPU-only OCR forbids CPU/disk offload, but the model device map contains non-CUDA targets: "
                f"{non_cuda_targets}"
            )

    return processor, model


@torch.inference_mode()
def run_glm_ocr(
    media_path: Path,
    processor,
    model,
    prompt: str,
    max_new_tokens: int,
) -> str:
    # GLM-OCR official style: {"type": "image", "url": "..."} + text prompt.
    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "url": str(media_path)},
            {"type": "text", "text": prompt},
        ],
    }]

    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
    ).to(model.device)

    inputs.pop("token_type_ids", None)

    out_ids = model.generate(
        **inputs,
        max_new_tokens=max_new_tokens,
        do_sample=False,
    )

    gen_only = out_ids[0][inputs["input_ids"].shape[1]:]
    return processor.decode(gen_only, skip_special_tokens=True)


def build_prompt(task: str, output_format: str, custom_prompt: Optional[str], schema_path: Optional[str]) -> str:
    """
    output_format: md|txt|json|html
    task: text|table|formula|extract|custom
    """
    fmt_hint = {
        "md": "Return Markdown.",
        "txt": "Return plain text only.",
        "json": "Return JSON only (no Markdown fences, no extra commentary).",
        "html": "Return HTML only (no Markdown fences, no extra commentary).",
    }.get(output_format, "Return Markdown.")

    if task == "custom":
        if not custom_prompt:
            raise RuntimeError("--task custom requires --custom_prompt.")
        return f"{custom_prompt.strip()}\n{fmt_hint}"

    if task == "extract":
        if not schema_path:
            raise RuntimeError("--task extract requires --schema (path to JSON schema/instructions).")
        schema_text = Path(schema_path).read_text(encoding="utf-8")
        # Keep it simple: you provide the schema/instructions; we ask for strict JSON.
        return (
            "Information Extraction: Extract structured information from the document.\n"
            "You must follow this schema/instructions strictly and output JSON only.\n\n"
            f"{schema_text}\n\n"
            f"{fmt_hint}"
        )

    prefix = TASK_PREFIX.get(task)
    if not prefix:
        raise RuntimeError(f"Unknown task: {task}")

    # For tables, docs often expect HTML; but we let user choose output_format.
    return f"{prefix} {fmt_hint}"


def write_output(
    out_path: Path,
    output_format: str,
    header_meta: dict,
    items_out: List[dict],
    combined_text: Optional[str],
    append_json: bool = False,
):
    ensure_dir(out_path.parent)

    if output_format in ("md", "txt", "html"):
        out_path.write_text(combined_text or "", encoding="utf-8")
        return

    existing_meta: dict = {}
    existing_items: List[dict] = []
    if append_json and out_path.exists():
        try:
            existing_payload = json.loads(out_path.read_text(encoding="utf-8"))
            loaded_meta = existing_payload.get("meta", {})
            loaded_items = existing_payload.get("items", [])
            if isinstance(loaded_meta, dict):
                existing_meta = loaded_meta
            if isinstance(loaded_items, list):
                existing_items = [item for item in loaded_items if isinstance(item, dict)]
        except Exception:
            existing_meta = {}
            existing_items = []

    payload = {
        "meta": {**existing_meta, **header_meta},
        "items": existing_items + items_out,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def mk_output_paths(output_dir: Path, job_name: str, output_format: str) -> Tuple[Path, Path]:
    ext = {"md": ".md", "txt": ".txt", "html": ".html", "json": ".json"}.get(output_format, ".md")
    out_file = output_dir / f"{job_name}{ext}"
    state_file = output_dir / f"{job_name}.state.json"
    return out_file, state_file


def choose_from_input_folder(input_dir: Path, mode: str) -> Tuple[str, Union[Path, List[Path]]]:
    """
    mode:
      auto      -> if exactly 1 PDF exists use it, else use images if present, else error
      pdf       -> require exactly 1 PDF
      images    -> require images
      all_pdfs  -> require >=1 PDF
    """
    pdfs = list_pdfs(input_dir)
    imgs = list_images(input_dir)

    if mode == "auto":
        if len(pdfs) == 1 and not imgs:
            return "pdf", pdfs[0]
        if len(pdfs) == 1 and imgs:
            raise RuntimeError("input folder contains both 1 PDF and images; remove one type or choose --mode explicitly.")
        if len(pdfs) > 1:
            # user wants “complete pdf (input folder)” via args
            raise RuntimeError("multiple PDFs found; use --mode all_pdfs or keep only one PDF.")
        if imgs:
            return "images", imgs
        raise RuntimeError("input folder has no PDF and no images.")

    if mode == "pdf":
        if len(pdfs) != 1:
            raise RuntimeError(f"--mode pdf requires exactly 1 PDF in input folder (found {len(pdfs)}).")
        if imgs:
            raise RuntimeError("--mode pdf: remove images from input folder.")
        return "pdf", pdfs[0]

    if mode == "images":
        if not imgs:
            raise RuntimeError("--mode images requires at least 1 image in input folder.")
        if pdfs:
            raise RuntimeError("--mode images: remove PDFs from input folder.")
        return "images", imgs

    if mode == "all_pdfs":
        if not pdfs:
            raise RuntimeError("--mode all_pdfs requires at least 1 PDF in input folder.")
        if imgs:
            raise RuntimeError("--mode all_pdfs: remove images from input folder.")
        return "all_pdfs", pdfs

    raise RuntimeError(f"Unknown --mode: {mode}")


def parse_page_selection_arg(selection: str, page_count: int) -> List[int]:
    """
    Parse 1-based page selections like:
      2
      1-3
      2-
      1,3,5-7
    Returns an ordered unique list of 1-based page numbers.
    """
    if page_count <= 0:
        raise PageSelectionBoundsError("Page selection cannot be used on an empty PDF.")

    if not selection or not selection.strip():
        raise PageSelectionError("Page selection text is empty.")

    seen: set[int] = set()
    ordered: List[int] = []

    for token in selection.split(","):
        item = token.strip()
        if not item:
            continue

        if "-" in item:
            start_text, end_text = item.split("-", 1)
            try:
                start = 1 if start_text == "" else int(start_text)
                end = page_count if end_text == "" else int(end_text)
            except ValueError as exc:
                raise PageSelectionError(f"Invalid page range token: '{item}'.") from exc

            if start < 1 or end < 1 or start > page_count or end > page_count:
                raise PageSelectionBoundsError(
                    f"Page range '{item}' is out of bounds for {page_count} pages."
                )
            if end < start:
                raise PageSelectionError(f"Page range '{item}' has reversed bounds.")

            for page in range(start, end + 1):
                if page not in seen:
                    seen.add(page)
                    ordered.append(page)
            continue

        try:
            page = int(item)
        except ValueError as exc:
            raise PageSelectionError(f"Invalid page token: '{item}'.") from exc

        if page < 1 or page > page_count:
            raise PageSelectionBoundsError(
                f"Page '{page}' is out of bounds for {page_count} pages."
            )
        if page not in seen:
            seen.add(page)
            ordered.append(page)

    if not ordered:
        raise PageSelectionError("No valid pages were selected.")

    return ordered


def process_pdf_pages_mode(
    pdf_path: Path,
    processor,
    model,
    prompt: str,
    output_format: str,
    out_file: Path,
    state_file: Path,
    work_dir: Path,
    poppler_path: Optional[str],
    dpi: int,
    batch_pages: int,
    max_pages: int,
    max_side: Optional[int],
    max_new_tokens: int,
    per_page_files: bool,
    page_selection: Optional[str],
):
    total_pdf_pages = pdf_page_count(pdf_path, poppler_path=poppler_path)
    selected_pages: Optional[List[int]] = None
    if page_selection and page_selection.strip():
        selected_pages = parse_page_selection_arg(page_selection, total_pdf_pages)
        total_pages = len(selected_pages)
    else:
        total_pages = min(total_pdf_pages, max_pages)

    state = load_state(state_file)
    if selected_pages is not None:
        next_index = int(state.get("next_index", 0))
        if "next_index" not in state and "next_page" in state:
            try:
                legacy_next_page = int(state.get("next_page", 1))
                next_index = 0
                while next_index < len(selected_pages) and selected_pages[next_index] < legacy_next_page:
                    next_index += 1
            except Exception:
                next_index = 0

        if next_index >= total_pages:
            print(f"[INFO] {pdf_path.name}: already done (next_index={next_index} >= total_selected={total_pages})")
            return

        start_page = selected_pages[next_index]
        print(
            f"[INFO] PDF: {pdf_path.name} selected_pages={total_pages} "
            f"start_index={next_index} start_page={start_page} out={out_file.name}"
        )
    else:
        next_page = int(state.get("next_page", 1))
        if next_page > total_pages:
            print(f"[INFO] {pdf_path.name}: already done (next_page={next_page} > total_pages={total_pages})")
            return
        print(f"[INFO] PDF: {pdf_path.name} pages={total_pages} start_page={next_page} out={out_file.name}")

    # Write/append combined output
    if selected_pages is not None:
        mode = "a" if out_file.exists() and next_index > 0 else "w"
    else:
        mode = "a" if out_file.exists() and next_page > 1 else "w"

    if output_format == "txt":
        header_line = f"OCR Output - {pdf_path.name} - {now_iso()}\n\n"
    elif output_format == "html":
        header_line = f"<!-- OCR Output - {pdf_path.name} - {now_iso()} -->\n"
    elif output_format == "md":
        header_line = f"# OCR Output\n\n- Source PDF: {pdf_path.name}\n- Started: {now_iso()}\n\n"
    else:
        header_line = ""  # json handled later

    combined_lines: List[str] = []
    items_json: List[dict] = []

    # If resuming and writing md/txt/html, append separator
    if mode == "a" and output_format in ("md", "txt", "html"):
        combined_lines.append("\n\n---\n\n")

    if mode == "w" and output_format in ("md", "txt", "html"):
        combined_lines.append(header_line)

    if selected_pages is not None:
        index = next_index
        while index < total_pages:
            batch = selected_pages[index:index + batch_pages]
            print(f"[PDF] Render selected pages {batch} (DPI={dpi})")

            for page_num in batch:
                rendered = render_pdf_pages_batch(
                    pdf_path=pdf_path,
                    out_dir=work_dir,
                    first_page=page_num,
                    last_page=page_num,
                    dpi=dpi,
                    poppler_path=poppler_path,
                )
                if not rendered:
                    eprint(f"[WARN] Render returned no pages for page {page_num}; skipping.")
                    state["next_index"] = index + 1
                    state["next_page"] = page_num + 1
                    save_state(state_file, state)
                    index += 1
                    continue

                for rendered_page_num, raw_img_path in rendered:
                    t0 = time.time()
                    img_path = work_dir / f"{pdf_path.stem}_p{rendered_page_num:04d}.png"
                    downscale_to_png(raw_img_path, img_path, max_side=max_side)
                    try:
                        raw_img_path.unlink(missing_ok=True)
                    except Exception:
                        pass

                    print(f"[OCR] Page {rendered_page_num} ({index + 1}/{total_pages}): {img_path.name}")
                    try:
                        text = run_glm_ocr(img_path, processor, model, prompt=prompt, max_new_tokens=max_new_tokens)
                        status = "OK"
                    except Exception as e:
                        status = "ERROR"
                        text = f"[OCR ERROR] {e}"
                        eprint(f"[ERROR] Page {rendered_page_num} failed: {e}")

                    if output_format == "md":
                        combined_lines.append(f"## Page {rendered_page_num}\n\n{text}\n")
                    elif output_format == "txt":
                        combined_lines.append(f"\n\n=== Page {rendered_page_num} ===\n\n{text}\n")
                    elif output_format == "html":
                        combined_lines.append(f"\n<!-- Page {rendered_page_num} -->\n{text}\n")
                    else:
                        items_json.append({
                            "page": rendered_page_num,
                            "image_file": str(img_path),
                            "status": status,
                            "text": text,
                        })

                    if per_page_files and output_format in ("md", "txt", "html"):
                        ext = {"md": ".md", "txt": ".txt", "html": ".html"}[output_format]
                        per = out_file.parent / f"{out_file.stem}_p{rendered_page_num:04d}{ext}"
                        per.write_text(text, encoding="utf-8")

                    state["next_index"] = index + 1
                    state["next_page"] = rendered_page_num + 1
                    save_state(state_file, state)

                    try:
                        img_path.unlink(missing_ok=True)
                    except Exception:
                        pass

                    print(
                        f"[{status}] Page {rendered_page_num} done in {time.time() - t0:.1f}s; "
                        f"next_index={state['next_index']}"
                    )
                    index += 1
    else:
        page = next_page
        while page <= total_pages:
            b_first = page
            b_last = min(page + batch_pages - 1, total_pages)
            print(f"[PDF] Render {b_first}..{b_last} (DPI={dpi})")

            rendered = render_pdf_pages_batch(
                pdf_path=pdf_path,
                out_dir=work_dir,
                first_page=b_first,
                last_page=b_last,
                dpi=dpi,
                poppler_path=poppler_path,
            )

            for page_num, raw_img_path in rendered:
                t0 = time.time()
                # normalize/downscale to stable path for model
                img_path = work_dir / f"{pdf_path.stem}_p{page_num:04d}.png"
                downscale_to_png(raw_img_path, img_path, max_side=max_side)
                try:
                    raw_img_path.unlink(missing_ok=True)
                except Exception:
                    pass

                print(f"[OCR] Page {page_num}/{total_pages}: {img_path.name}")
                try:
                    text = run_glm_ocr(img_path, processor, model, prompt=prompt, max_new_tokens=max_new_tokens)
                    status = "OK"
                except Exception as e:
                    status = "ERROR"
                    text = f"[OCR ERROR] {e}"
                    eprint(f"[ERROR] Page {page_num} failed: {e}")

                # Collect outputs
                if output_format == "md":
                    combined_lines.append(f"## Page {page_num}\n\n{text}\n")
                elif output_format == "txt":
                    combined_lines.append(f"\n\n=== Page {page_num} ===\n\n{text}\n")
                elif output_format == "html":
                    combined_lines.append(f"\n<!-- Page {page_num} -->\n{text}\n")
                else:
                    items_json.append({
                        "page": page_num,
                        "image_file": str(img_path),
                        "status": status,
                        "text": text,
                    })

                if per_page_files and output_format in ("md", "txt", "html"):
                    ext = {"md": ".md", "txt": ".txt", "html": ".html"}[output_format]
                    per = out_file.parent / f"{out_file.stem}_p{page_num:04d}{ext}"
                    per.write_text(text, encoding="utf-8")

                # Update state
                state["next_page"] = page_num + 1
                save_state(state_file, state)

                # Cleanup
                try:
                    img_path.unlink(missing_ok=True)
                except Exception:
                    pass

                print(f"[{status}] Page {page_num} done in {time.time() - t0:.1f}s; next_page={state['next_page']}")

            page = b_last + 1

    # Write output
    meta = {
        "source": str(pdf_path),
        "model": getattr(model, "name_or_path", DEFAULT_MODEL_ID),
        "task_prompt": prompt,
        "output_format": output_format,
        "finished_at": now_iso(),
        "pages_processed": total_pages if selected_pages is not None else min(total_pages, max_pages),
    }
    if selected_pages is not None:
        meta["selected_pages"] = selected_pages

    if output_format in ("md", "txt", "html"):
        existing = out_file.read_text(encoding="utf-8") if out_file.exists() and mode == "a" else ""
        out_file.write_text(existing + "".join(combined_lines), encoding="utf-8")
    else:
        write_output(out_file, output_format, meta, items_json, combined_text=None, append_json=(mode == "a"))

    print(f"[DONE] Wrote: {out_file}")


def process_pdf_direct_mode(
    pdf_path: Path,
    processor,
    model,
    prompt: str,
    output_format: str,
    out_file: Path,
    state_file: Path,
    max_new_tokens: int,
):
    """
    Attempts to send the PDF path directly as {"type":"image","url": "...pdf..."}.
    This may or may not work depending on the current processor implementation.
    """
    if state_file.exists():
        print(f"[INFO] direct-pdf mode: ignoring resume and overwriting output (job state may not apply).")
    print(f"[INFO] PDF direct mode: {pdf_path.name}")

    t0 = time.time()
    try:
        text = run_glm_ocr(pdf_path, processor, model, prompt=prompt, max_new_tokens=max_new_tokens)
        status = "OK"
    except Exception as e:
        status = "ERROR"
        text = f"[PDF DIRECT MODE FAILED] {e}"
        eprint(f"[ERROR] direct pdf failed: {e}")

    meta = {
        "source": str(pdf_path),
        "model": getattr(model, "name_or_path", DEFAULT_MODEL_ID),
        "task_prompt": prompt,
        "output_format": output_format,
        "finished_at": now_iso(),
        "mode": "pdf_direct",
        "status": status,
        "elapsed_s": round(time.time() - t0, 2),
    }

    if output_format == "md":
        out_file.write_text(f"# OCR Output\n\n- Source PDF: {pdf_path.name}\n- Mode: direct\n- Status: {status}\n\n{text}\n", encoding="utf-8")
    elif output_format == "txt":
        out_file.write_text(f"OCR Output\nSource: {pdf_path.name}\nMode: direct\nStatus: {status}\n\n{text}\n", encoding="utf-8")
    elif output_format == "html":
        out_file.write_text(f"<!-- OCR Output {pdf_path.name} mode=direct status={status} -->\n{text}\n", encoding="utf-8")
    else:
        write_output(out_file, output_format, meta, [{"status": status, "text": text}], combined_text=None)

    print(f"[DONE] Wrote: {out_file}")


def process_images_mode(
    images: List[Path],
    processor,
    model,
    prompt: str,
    output_format: str,
    out_file: Path,
    state_file: Path,
    work_dir: Path,
    max_items: int,
    max_side: Optional[int],
    max_new_tokens: int,
    per_page_files: bool,
):
    state = load_state(state_file)
    next_index = int(state.get("next_index", 0))

    images = images[:max_items]
    total = len(images)

    if next_index >= total:
        print(f"[INFO] images: already done (next_index={next_index} >= total={total})")
        return

    print(f"[INFO] Images: total={total} start_index={next_index} out={out_file.name}")

    mode = "a" if out_file.exists() and next_index > 0 else "w"

    combined_lines: List[str] = []
    items_json: List[dict] = []

    if mode == "a" and output_format in ("md", "txt", "html"):
        combined_lines.append("\n\n---\n\n")

    if mode == "w" and output_format in ("md", "txt", "html"):
        if output_format == "md":
            combined_lines.append(f"# OCR Output\n\n- Source: images\n- Started: {now_iso()}\n\n")
        elif output_format == "txt":
            combined_lines.append(f"OCR Output - images - {now_iso()}\n\n")
        elif output_format == "html":
            combined_lines.append(f"<!-- OCR Output images started {now_iso()} -->\n")

    for i in range(next_index, total):
        src = images[i]
        t0 = time.time()
        normalized = work_dir / f"img_{i+1:04d}.png"
        downscale_to_png(src, normalized, max_side=max_side)

        label = src.name
        page_num = i + 1
        print(f"[OCR] Image {page_num}/{total}: {label}")

        try:
            text = run_glm_ocr(normalized, processor, model, prompt=prompt, max_new_tokens=max_new_tokens)
            status = "OK"
        except Exception as e:
            status = "ERROR"
            text = f"[OCR ERROR] {e}"
            eprint(f"[ERROR] Image {label} failed: {e}")

        if output_format == "md":
            combined_lines.append(f"## Page {page_num} ({label})\n\n{text}\n")
        elif output_format == "txt":
            combined_lines.append(f"\n\n=== Page {page_num} ({label}) ===\n\n{text}\n")
        elif output_format == "html":
            combined_lines.append(f"\n<!-- Page {page_num} {label} -->\n{text}\n")
        else:
            items_json.append({
                "index": i,
                "label": label,
                "image_file": str(src),
                "status": status,
                "text": text,
            })

        if per_page_files and output_format in ("md", "txt", "html"):
            ext = {"md": ".md", "txt": ".txt", "html": ".html"}[output_format]
            per = out_file.parent / f"{out_file.stem}_p{page_num:04d}{ext}"
            per.write_text(text, encoding="utf-8")

        state["next_index"] = i + 1
        save_state(state_file, state)

        try:
            normalized.unlink(missing_ok=True)
        except Exception:
            pass

        print(f"[{status}] Image {page_num} done in {time.time() - t0:.1f}s; next_index={state['next_index']}")

    meta = {
        "source": "images",
        "count": total,
        "model": getattr(model, "name_or_path", DEFAULT_MODEL_ID),
        "task_prompt": prompt,
        "output_format": output_format,
        "finished_at": now_iso(),
    }

    if output_format in ("md", "txt", "html"):
        existing = out_file.read_text(encoding="utf-8") if out_file.exists() and mode == "a" else ""
        out_file.write_text(existing + "".join(combined_lines), encoding="utf-8")
    else:
        write_output(out_file, output_format, meta, items_json, combined_text=None, append_json=(mode == "a"))

    print(f"[DONE] Wrote: {out_file}")


def parse_args():
    p = argparse.ArgumentParser(description="Robust local OCR with zai-org/GLM-OCR (Transformers).")

    # Paths / modes
    p.add_argument("--input_dir", default=None, help="Path to input folder (default: script_dir\\input).")
    p.add_argument("--output_dir", default=None, help="Path to output folder (default: script_dir\\output).")
    p.add_argument("--mode", choices=["auto", "pdf", "images", "all_pdfs"], default="auto",
                   help="How to interpret input_dir contents. Default auto: one PDF else images.")
    p.add_argument("--pdf_mode", choices=["pages", "direct"], default="pages",
                   help="PDF handling: pages=render per page (default), direct=try sending PDF as a single input.")
    p.add_argument("--job_name", default=None, help="Base name for output files (default derived from inputs).")

    # Model / runtime
    p.add_argument("--model", default=DEFAULT_MODEL_ID)
    p.add_argument("--trust_remote_code", action="store_true", help="Allow custom model code if required.")
    p.add_argument("--use_fast", choices=["true", "false", "auto"], default="true",
                   help="Processor fast/slow. true=default, false forces slow, auto defers to Transformers.")
    p.add_argument("--force_fp16", action="store_true")

    # PDF rendering
    p.add_argument("--poppler_path", default=None, help="Windows: Poppler bin path for pdf2image.")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--batch_pages", type=int, default=8)
    p.add_argument("--max_pages", type=int, default=None, help="Maximum pages from page 1 (PDF/image modes).")
    p.add_argument(
        "--pages",
        default=None,
        help="Optional 1-based PDF page selection, e.g. '2', '1-3,8', or '2-'. Cannot be combined with --pdf_mode direct.",
    )

    # Image normalization
    p.add_argument("--max_side", type=int, default=1800, help="Downscale so max(image side)<=this; 0 disables.")

    # OCR generation / task & output
    p.add_argument("--task", choices=["text", "table", "formula", "extract", "custom"], default=DEFAULT_TASK)
    p.add_argument("--output_format", choices=["md", "txt", "json", "html"], default=DEFAULT_OUTPUT_FORMAT)
    p.add_argument("--max_new_tokens", type=int, default=2048)

    p.add_argument("--custom_prompt", default=None, help="Used when --task custom.")
    p.add_argument("--schema", default=None, help="Path to schema/instructions text for --task extract.")

    # Output controls
    p.add_argument("--per_page_files", action="store_true", help="Also write per-page outputs (md/txt/html).")

    return p.parse_args()


def main():
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    input_dir = Path(args.input_dir).expanduser().resolve() if args.input_dir else (script_dir / DEFAULT_INPUT_DIRNAME)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else (script_dir / DEFAULT_OUTPUT_DIRNAME)
    work_dir = output_dir / "_tmp_work"

    ensure_dir(input_dir)
    ensure_dir(output_dir)
    ensure_dir(work_dir)

    use_fast = None if args.use_fast == "auto" else (args.use_fast == "true")

    print(f"[INFO] time={now_iso()}")
    print(f"[INFO] input_dir={input_dir}")
    print(f"[INFO] output_dir={output_dir}")
    print(f"[INFO] hf_home={os.environ.get('HF_HOME')}")
    print(f"[INFO] torch_home={os.environ.get('TORCH_HOME')}")
    print(f"[INFO] mode={args.mode} pdf_mode={args.pdf_mode}")
    print(f"[INFO] model={args.model}")
    print(f"[INFO] cuda={torch.cuda.is_available()} torch={torch.__version__}")
    if not torch.cuda.is_available():
        raise RuntimeError(
            "GPU-only OCR is enabled and no CUDA device is available. "
            "This tool will not run on CPU."
        )
    print(f"[INFO] gpu_name={torch.cuda.get_device_name(0)}")

    kind, payload = choose_from_input_folder(input_dir, mode=args.mode)
    page_selection = (args.pages or "").strip()
    max_pages = args.max_pages if args.max_pages is not None else 1500

    if page_selection and kind == "images":
        raise RuntimeError("--pages is only supported for PDF inputs.")
    if page_selection and kind in {"pdf", "all_pdfs"} and args.pdf_mode == "direct":
        raise RuntimeError("--pages cannot be used with --pdf_mode direct. Use --pdf_mode pages.")
    if page_selection and kind in {"pdf", "all_pdfs"} and args.max_pages is not None:
        raise RuntimeError("--pages cannot be combined with --max_pages.")

    # Job signature (so resume files don't collide across settings)
    max_side = None if args.max_side == 0 else args.max_side
    pages_sig = page_selection if page_selection else "-"
    max_pages_sig = str(max_pages if not page_selection else "none")
    settings_sig = (
        f"task={args.task}|fmt={args.output_format}|pdf_mode={args.pdf_mode}|dpi={args.dpi}|batch={args.batch_pages}|"
        f"max_pages={max_pages_sig}|pages={pages_sig}|max_side={max_side}|mnt={args.max_new_tokens}|model={args.model}"
    )
    if kind == "pdf":
        sig = f"{file_sig(payload)}|{settings_sig}"  # type: ignore[arg-type]
        base_name = Path(payload).stem  # type: ignore[arg-type]
    elif kind == "images":
        sig = f"{dir_sig_images(input_dir)}|{settings_sig}"
        base_name = input_dir.name
    else:  # all_pdfs
        sig = f"{dir_sig_pdfs(input_dir)}|{settings_sig}"
        base_name = input_dir.name

    job_name = args.job_name or f"{base_name}_{sha1(sig)[:10]}"

    # Init model once
    prompt = build_prompt(args.task, args.output_format, args.custom_prompt, args.schema)
    processor, model = init_model(
        model_id=args.model,
        force_fp16=args.force_fp16,
        trust_remote_code=args.trust_remote_code,
        use_fast=use_fast,
    )
    print(f"[MODEL] ready device={model.device}")

    if kind == "pdf":
        pdf_path: Path = payload  # type: ignore[assignment]
        out_file, state_file = mk_output_paths(output_dir, job_name, args.output_format)
        if args.pdf_mode == "direct":
            process_pdf_direct_mode(
                pdf_path=pdf_path,
                processor=processor,
                model=model,
                prompt=prompt,
                output_format=args.output_format,
                out_file=out_file,
                state_file=state_file,
                max_new_tokens=args.max_new_tokens,
            )
        else:
            process_pdf_pages_mode(
                pdf_path=pdf_path,
                processor=processor,
                model=model,
                prompt=prompt,
                output_format=args.output_format,
                out_file=out_file,
                state_file=state_file,
                work_dir=work_dir,
                poppler_path=args.poppler_path,
                dpi=args.dpi,
                batch_pages=args.batch_pages,
                max_pages=max_pages,
                max_side=max_side,
                max_new_tokens=args.max_new_tokens,
                per_page_files=args.per_page_files,
                page_selection=page_selection or None,
            )

    elif kind == "images":
        images: List[Path] = payload  # type: ignore[assignment]
        out_file, state_file = mk_output_paths(output_dir, job_name, args.output_format)
        process_images_mode(
            images=images,
            processor=processor,
            model=model,
            prompt=prompt,
            output_format=args.output_format,
            out_file=out_file,
            state_file=state_file,
            work_dir=work_dir,
            max_items=max_pages,
            max_side=max_side,
            max_new_tokens=args.max_new_tokens,
            per_page_files=args.per_page_files,
        )

    else:  # all_pdfs
        pdfs: List[Path] = payload  # type: ignore[assignment]
        print(f"[INFO] all_pdfs: count={len(pdfs)}")
        for pdf_path in pdfs:
            per_sig = f"{file_sig(pdf_path)}|{settings_sig}"
            per_job = f"{pdf_path.stem}_{sha1(per_sig)[:10]}"
            out_file, state_file = mk_output_paths(output_dir, per_job, args.output_format)

            if args.pdf_mode == "direct":
                process_pdf_direct_mode(
                    pdf_path=pdf_path,
                    processor=processor,
                    model=model,
                    prompt=prompt,
                    output_format=args.output_format,
                    out_file=out_file,
                    state_file=state_file,
                    max_new_tokens=args.max_new_tokens,
                )
            else:
                try:
                    process_pdf_pages_mode(
                        pdf_path=pdf_path,
                        processor=processor,
                        model=model,
                        prompt=prompt,
                        output_format=args.output_format,
                        out_file=out_file,
                        state_file=state_file,
                        work_dir=work_dir,
                        poppler_path=args.poppler_path,
                        dpi=args.dpi,
                        batch_pages=args.batch_pages,
                        max_pages=max_pages,
                        max_side=max_side,
                        max_new_tokens=args.max_new_tokens,
                        per_page_files=args.per_page_files,
                        page_selection=page_selection or None,
                    )
                except PageSelectionBoundsError as exc:
                    if page_selection:
                        eprint(f"[WARN] Skipping {pdf_path.name}: {exc}")
                        continue
                    raise

    # best-effort cleanup
    try:
        if work_dir.exists() and not any(work_dir.iterdir()):
            work_dir.rmdir()
    except Exception:
        pass


if __name__ == "__main__":
    main()
