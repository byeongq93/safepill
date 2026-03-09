import io
import logging
from functools import lru_cache

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

try:
    import easyocr  # type: ignore
except Exception:
    easyocr = None

try:
    from paddleocr import PaddleOCR  # type: ignore
except Exception:
    PaddleOCR = None

try:
    import pytesseract  # type: ignore
except Exception:
    pytesseract = None


logger = logging.getLogger(__name__)

_RESAMPLE = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
_KEYWORDS = ("유효성분", "유요성분", "성분", "원료약품", "원료 약품", "분량", "USP", "KP", "JP", "EP", "BP")
_STRONG_KEYWORDS = ("유효성분", "유요성분", "원료약품", "원료 약품")
_NOISE_HINTS = (
    "효능", "효과", "용법", "용량", "주의", "RFID",
    "초회용량", "권장 유지", "1일 1회", "1일 최대", "식이요법",
)


@lru_cache(maxsize=1)
def _get_easy_reader():
    if easyocr is None:
        raise RuntimeError("easyocr가 설치되어 있지 않습니다. pip install easyocr")
    return easyocr.Reader(["ko", "en"], gpu=False)


@lru_cache(maxsize=1)
def _get_paddle_reader():
    if PaddleOCR is None:
        return None
    tried = [
        {"use_angle_cls": True, "lang": "korean", "show_log": False},
        {"use_angle_cls": True, "lang": "korean"},
        {"use_angle_cls": True, "lang": "korean", "det": True, "rec": True},
    ]
    for kwargs in tried:
        try:
            return PaddleOCR(**kwargs)
        except Exception:
            continue
    return None


def _resize_for_ocr(image: Image.Image) -> Image.Image:
    base = ImageOps.exif_transpose(image).convert("RGB")
    long_side = max(base.size)
    if long_side > 2200:
        scale = 2200 / long_side
        base = base.resize((max(1, int(base.width * scale)), max(1, int(base.height * scale))), _RESAMPLE)
    elif long_side < 1400:
        scale = 1400 / max(1, long_side)
        base = base.resize((max(1, int(base.width * scale)), max(1, int(base.height * scale))), _RESAMPLE)
    return base



def _resize_for_tesseract(image: Image.Image) -> Image.Image:
    base = image.convert("RGB")
    long_side = max(base.size)
    if long_side > 1250:
        scale = 1250 / long_side
        base = base.resize((max(1, int(base.width * scale)), max(1, int(base.height * scale))), _RESAMPLE)
    return base


def _safe_mean(image: Image.Image) -> float:
    try:
        stat = ImageStat.Stat(image)
        return float(stat.mean[0]) if stat.mean else 128.0
    except Exception:
        return 128.0


def _prepare_light(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=0.5)

    mean = _safe_mean(gray)
    if mean < 105:
        gray = ImageEnhance.Brightness(gray).enhance(1.10)
    elif mean > 210:
        gray = ImageEnhance.Brightness(gray).enhance(0.96)

    gray = ImageEnhance.Contrast(gray).enhance(1.18)
    gray = gray.filter(ImageFilter.UnsharpMask(radius=1.1, percent=115, threshold=3))
    return gray


def _prepare_gray(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.equalize(gray)
    gray = ImageOps.autocontrast(gray, cutoff=1)

    mean = _safe_mean(gray)
    if mean < 105:
        gray = ImageEnhance.Brightness(gray).enhance(1.18)
    elif mean > 205:
        gray = ImageEnhance.Brightness(gray).enhance(0.92)

    gray = ImageEnhance.Contrast(gray).enhance(1.42)
    gray = gray.filter(ImageFilter.MedianFilter(size=3))
    gray = gray.filter(ImageFilter.UnsharpMask(radius=1.5, percent=170, threshold=3))
    return gray


def _dynamic_binary_variants(gray: Image.Image):
    arr = np.asarray(gray, dtype=np.uint8)
    if arr.size == 0:
        return [gray.convert("RGB")]

    mean = float(arr.mean())
    std = float(arr.std())
    base_threshold = int(round(max(118, min(208, mean + std * 0.10))))
    soft_threshold = max(104, base_threshold - 16)

    binary_main = gray.point(lambda p: 255 if p > base_threshold else 0)
    binary_soft = gray.point(lambda p: 255 if p > soft_threshold else 0)
    inverted = ImageOps.invert(binary_main)

    return [
        binary_main.convert("RGB"),
        binary_soft.convert("RGB"),
        inverted.convert("RGB"),
    ]


def _rotated_variants(image: Image.Image, fillcolor=None):
    yield image
    for angle in (-1.2, 1.2):
        try:
            rotated = image.rotate(angle, expand=True, resample=Image.BICUBIC, fillcolor=fillcolor)
            yield rotated
        except Exception:
            continue


def _dedupe_variants(images, limit=None):
    deduped = []
    seen = set()
    for variant in images:
        try:
            key = (variant.mode, variant.size, hash(variant.tobytes()[:4096]))
        except Exception:
            key = (variant.mode, variant.size, id(variant))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(variant)
        if limit and len(deduped) >= limit:
            break
    return deduped


# full image용: 원본+약한 보정 위주로 빠르게 스캔
# crop용: 필요할 때만 강한 보정/이진화까지 확장

def _variants(image: Image.Image, include_rotations: bool = False, strong: bool = False):
    base = image.convert("RGB")
    light = _prepare_light(base).convert("RGB")
    variants = [base, light]

    if strong:
        gray = _prepare_gray(base)
        gray_rgb = gray.convert("RGB")
        variants.append(gray_rgb)
        variants.extend(_dynamic_binary_variants(gray)[:2])

        if include_rotations:
            for rotated in _rotated_variants(gray, fillcolor=255):
                rgb = rotated.convert("RGB")
                variants.append(rgb)
                variants.extend(_dynamic_binary_variants(rotated)[:1])

    return _dedupe_variants(variants, limit=6)


def _clean_text(text):
    return " ".join(str(text or "").split()).strip()


def _read_easy_lines(reader, image: Image.Image, offset=(0, 0)):
    ox, oy = offset
    try:
        results = reader.readtext(np.array(image), detail=1, paragraph=False)
    except Exception:
        return []

    rows = []
    for entry in results:
        if not entry or len(entry) < 3:
            continue
        box, text, conf = entry
        text = _clean_text(text)
        if not text:
            continue
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        rows.append(
            {
                "text": text,
                "conf": float(conf or 0.0),
                "box": (
                    ox + max(0, int(min(xs))),
                    oy + max(0, int(min(ys))),
                    ox + max(0, int(max(xs))),
                    oy + max(0, int(max(ys))),
                ),
                "engine": "easyocr",
            }
        )
    return rows


def _iter_paddle_items(result):
    if not result:
        return []
    if isinstance(result, tuple):
        result = list(result)
    if isinstance(result, list) and result and isinstance(result[0], dict):
        items = []
        for entry in result:
            rec_texts = entry.get("rec_texts") or []
            rec_scores = entry.get("rec_scores") or []
            rec_boxes = entry.get("rec_boxes") or []
            for idx, text in enumerate(rec_texts):
                box = rec_boxes[idx] if idx < len(rec_boxes) else None
                score = rec_scores[idx] if idx < len(rec_scores) else 0.0
                items.append((box, text, score))
        return items
    if isinstance(result, list) and result and isinstance(result[0], list):
        if result and len(result) == 1 and isinstance(result[0], list):
            result = result[0]
        items = []
        for entry in result:
            if isinstance(entry, list) and len(entry) >= 2:
                box = entry[0]
                rec = entry[1]
                if isinstance(rec, (list, tuple)) and len(rec) >= 2:
                    items.append((box, rec[0], rec[1]))
        return items
    return []


def _read_paddle_lines(reader, image: Image.Image, offset=(0, 0)):
    if reader is None:
        return []
    ox, oy = offset
    try:
        result = reader.ocr(np.array(image), cls=True)
    except Exception:
        return []

    rows = []
    for box, text, conf in _iter_paddle_items(result):
        text = _clean_text(text)
        if not text or box is None:
            continue
        try:
            xs = [pt[0] for pt in box]
            ys = [pt[1] for pt in box]
        except Exception:
            continue
        rows.append(
            {
                "text": text,
                "conf": float(conf or 0.0),
                "box": (
                    ox + max(0, int(min(xs))),
                    oy + max(0, int(min(ys))),
                    ox + max(0, int(max(xs))),
                    oy + max(0, int(max(ys))),
                ),
                "engine": "paddleocr",
            }
        )
    return rows


def _line_score(text: str, conf: float) -> float:
    score = float(conf or 0.0)
    t = str(text or "")
    if any(k in t for k in _STRONG_KEYWORDS):
        score += 0.65
    elif any(k in t for k in _KEYWORDS):
        score += 0.25
    if any(u in t for u in ("USP", "KP", "JP", "EP", "BP")):
        score += 0.25
    if any(u in t for u in ("mg", "mL", "ml", "㎎")):
        score += 0.12
    if any(n in t for n in _NOISE_HINTS):
        score -= 0.18
    if len(t) > 90:
        score -= 0.15
    return score


def _merge_lines(rows):
    best = {}
    for row in rows:
        text = _clean_text(row.get("text"))
        if not text:
            continue
        x1, y1, x2, y2 = row.get("box", (0, 0, 0, 0))
        key = text.lower()
        score = _line_score(text, row.get("conf", 0.0))
        current = best.get(key)
        payload = {
            "text": text,
            "score": score,
            "conf": float(row.get("conf", 0.0)),
            "x": x1,
            "y": y1,
            "box": (x1, y1, x2, y2),
        }
        if current is None:
            best[key] = payload
        else:
            if (y1, x1) < (current["y"], current["x"]):
                current["x"], current["y"] = x1, y1
            if score > current["score"]:
                current.update(payload)
    ordered = sorted(best.values(), key=lambda item: (item["y"], item["x"], -item["score"]))
    return [item["text"] for item in ordered[:60]]


def _context_crop(base: Image.Image, box):
    x1, y1, x2, y2 = box
    bw = max(1, x2 - x1)
    bh = max(1, y2 - y1)
    pad_x = max(36, int(bw * 0.18))
    pad_y = max(24, int(bh * 0.8))
    left = max(0, x1 - pad_x)
    right = min(base.size[0], x2 + pad_x)
    top = max(0, y1 - pad_y)
    bottom = min(base.size[1], y2 + int(pad_y * 4.0))
    return base.crop((left, top, right, bottom)), (left, top)


def _title_crop(base: Image.Image):
    w, h = base.size
    if w >= h:
        box = (0, 0, int(w * 0.62), int(h * 0.46))
    else:
        box = (0, 0, w, int(h * 0.4))
    return base.crop(box), (box[0], box[1])


def _lower_left_crop(base: Image.Image):
    w, h = base.size
    box = (0, int(h * 0.18), int(w * 0.75), min(h, int(h * 0.78)))
    return base.crop(box), (box[0], box[1])


def _center_crop(base: Image.Image):
    w, h = base.size
    box = (int(w * 0.08), int(h * 0.08), int(w * 0.92), int(h * 0.86))
    return base.crop(box), (box[0], box[1])


def _read_tesseract_lines(image: Image.Image):
    if pytesseract is None:
        return []

    lines = []
    configs = ["--oem 1 --psm 11"]
    seen = set()
    for config in configs:
        try:
            text = pytesseract.image_to_string(image, lang="Hangul+eng", config=config, timeout=7)
        except Exception:
            continue

        for raw_line in str(text or "").replace("\r", "\n").split("\n"):
            line = _clean_text(raw_line)
            if not line or line in seen:
                continue
            seen.add(line)
            lines.append(line)
    return lines


def _extract_text_with_tesseract(base: Image.Image) -> str:
    collected = []
    base = _resize_for_tesseract(base)
    regions = [base, _lower_left_crop(base)[0]]
    for region in regions:
        # 원본 우선, 약한 보정 1개만 추가
        for variant in _variants(region, include_rotations=False, strong=False)[:2]:
            collected.extend(_read_tesseract_lines(variant))

    merged = []
    seen = set()
    for line in collected:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(line)
    return "\n".join(merged[:60]).strip()


def _scan_engine(reader, engine: str, image: Image.Image, offset=(0, 0)):
    if engine == "easyocr":
        return _read_easy_lines(reader, image, offset=offset)
    if engine == "paddleocr":
        return _read_paddle_lines(reader, image, offset=offset)
    return []


def _run_full_scan(base: Image.Image, easy_reader, paddle_reader):
    rows = []
    base_variants = _variants(base, include_rotations=False, strong=False)

    if easy_reader is not None:
        for variant in base_variants[:2]:
            rows.extend(_read_easy_lines(easy_reader, variant))

    if paddle_reader is not None:
        # Paddle은 원본+약한 보정 둘 다 보되, full scan에서는 여기까지만
        for variant in base_variants[:2]:
            rows.extend(_read_paddle_lines(paddle_reader, variant))

    return rows


def _needs_deep_scan(rows) -> bool:
    if not rows:
        return True
    texts = [r.get("text", "") for r in rows if r.get("text")]
    if len(texts) < 6:
        return True
    keyword_hits = sum(1 for t in texts if any(k in t for k in _KEYWORDS))
    dosage_hits = sum(1 for t in texts if any(u in t for u in ("mg", "mL", "ml", "㎎")))
    avg_len = sum(len(t) for t in texts) / max(1, len(texts))
    return keyword_hits == 0 and dosage_hits == 0 and avg_len < 12


def _build_regions(base: Image.Image, rows):
    keyword_rows = [row for row in rows if any(k in row.get("text", "") for k in _KEYWORDS)]
    keyword_rows = sorted(keyword_rows, key=lambda r: _line_score(r.get("text", ""), r.get("conf", 0.0)), reverse=True)

    seen_regions = set()
    regions = []
    for row in keyword_rows[:2]:
        crop, offset = _context_crop(base, row["box"])
        sig = (*offset, *crop.size)
        if sig not in seen_regions:
            seen_regions.add(sig)
            regions.append((crop, offset, True))

    if not regions:
        regions.extend([
            (*_title_crop(base), False),
            (*_lower_left_crop(base), False),
        ])

    if len(regions) < 3:
        regions.append((*_center_crop(base), False))

    return regions[:3]


def _run_region_scan(base: Image.Image, easy_reader, paddle_reader, seed_rows):
    extra_rows = []
    for crop, offset, prefer_strong in _build_regions(base, seed_rows):
        region_variants = _variants(crop, include_rotations=prefer_strong, strong=prefer_strong)

        if easy_reader is not None:
            # EasyOCR는 crop에서는 최대 2개만
            for variant in region_variants[:2]:
                extra_rows.extend(_read_easy_lines(easy_reader, variant, offset=offset))

        if paddle_reader is not None:
            # Paddle은 crop에서 원본 1회 + 필요시 강한 후보 1회만
            extra_rows.extend(_read_paddle_lines(paddle_reader, region_variants[0], offset=offset))
            if prefer_strong and len(region_variants) > 2:
                extra_rows.extend(_read_paddle_lines(paddle_reader, region_variants[2], offset=offset))

    return extra_rows


async def extract_text_from_image(image):
    contents = await image.read()
    base = _resize_for_ocr(Image.open(io.BytesIO(contents)))

    try:
        easy_reader = _get_easy_reader()
    except Exception:
        easy_reader = None
    paddle_reader = _get_paddle_reader()

    engine_summary = f"easyocr={easy_reader is not None}, paddleocr={paddle_reader is not None}, tesseract={pytesseract is not None}"
    logger.info("OCR engines: %s", engine_summary)

    if easy_reader is None and paddle_reader is None:
        logger.info("OCR mode: tesseract_fallback")
        return _extract_text_with_tesseract(base)

    seed_rows = _run_full_scan(base, easy_reader, paddle_reader)

    if _needs_deep_scan(seed_rows):
        logger.info("OCR mode: hybrid_deep_scan")
        extra_rows = _run_region_scan(base, easy_reader, paddle_reader, seed_rows)
        merged = _merge_lines(seed_rows + extra_rows)
    else:
        logger.info("OCR mode: hybrid_fast_scan")
        merged = _merge_lines(seed_rows)

    if merged:
        return "\n".join(merged).strip()

    logger.info("OCR mode: hybrid_empty_fallback_tesseract")
    return _extract_text_with_tesseract(base)
