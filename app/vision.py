"""Lectura de etiquetas desde fotos de cajas.

Proveedor 1 (recomendado): Claude con visión (ANTHROPIC_API_KEY). Lee varias cajas por foto, incluido el
código escrito a rotulador (AP01, SW02...).
Proveedor 2 (sin clave): OCR local con Tesseract + expresiones regulares. Solo extrae pegatinas; el código de
caja hay que escribirlo en la pantalla de revisión.
"""
import os, io, re, json, base64
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageOps

PROVIDER_OVERRIDE = None   # para pruebas: función (list[(name, bytes)], hints) -> list[dict]

PROMPT = """La foto muestra varias cajas de equipos de red (Cisco / Meraki / similares). Cada caja tiene una pegatina
del fabricante con el modelo (PID), la dirección MAC y el número de serie, y el técnico ha escrito a rotulador un código
de caja (AP01, AP02 ... AP100, SW01 ... SW60, MX01, WLC01, SRV01, TV01...).

Devuelve SOLO un array JSON, un objeto por caja visible:
{"box_code": "código a rotulador en mayúsculas o ''", "model": "modelo tal como figura", "mac": "MAC tal como figura",
 "serial": "número de serie tal como figura", "confidence": 0.0-1.0, "notes": "lo dudoso: pegatina cortada, reflejo..."}

Reglas:
- No inventes nada. Si un dato no se lee con seguridad, deja '' y explícalo en notes.
- La MAC son 12 dígitos hexadecimales (0-9, A-F). Serial Meraki: Qxxx-xxxx-xxxx. Serial Catalyst: 3 letras + 4 dígitos + 4 alfanuméricos.
- Distingue con cuidado 0/O, 1/I/L, 5/S, 8/B usando esos formatos.
- Si una caja aparece cortada por el borde de la foto y no se leen los datos, no la incluyas.
{hints}"""


def _prep(img_bytes, max_edge=2000):
    im = Image.open(io.BytesIO(img_bytes)); im = ImageOps.exif_transpose(im).convert("RGB")
    return im


def _jpeg(im, max_edge=1900):
    im = im.copy(); im.thumbnail((max_edge, max_edge))
    b = io.BytesIO(); im.save(b, "JPEG", quality=88); return b.getvalue()


def _tiles(im, overlap=0.18):
    w, h = im.size
    tw, th = int(w * (0.5 + overlap / 2)), int(h * (0.5 + overlap / 2))
    return [im.crop((x, y, min(w, x + tw), min(h, y + th))) for x in (0, w - tw) for y in (0, h - th)]


def _parse_json(text):
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b < 0:
        return []
    try:
        data = json.loads(text[a:b + 1])
    except Exception:
        return []
    return [d for d in data if isinstance(d, dict)]


def ai_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _claude_call(jpeg_bytes, hints):
    import anthropic
    client = anthropic.Anthropic()
    model = os.environ.get("LABEL_MODEL", "claude-sonnet-5-5")
    msg = client.messages.create(
        model=model, max_tokens=4096,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                          "data": base64.b64encode(jpeg_bytes).decode()}},
            {"type": "text", "text": PROMPT.format(hints=hints)}]}])
    return _parse_json("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))


def _key(it):
    from .inventory import norm_serial, norm_mac
    return norm_serial(it.get("serial")) or norm_mac(it.get("mac")) or (str(it.get("box_code", "")) + str(it.get("model", "")))


def _merge(lists):
    """Une lecturas de la foto completa y de los recortes: conserva el dato más completo."""
    out = {}
    for lst in lists:
        for it in lst:
            it = {k: (str(it.get(k) or "").strip() if k != "confidence" else float(it.get(k) or 0))
                  for k in ("box_code", "model", "mac", "serial", "notes", "confidence")}
            k = _key(it)
            if not k:
                continue
            cur = out.get(k)
            if cur is None:
                out[k] = it; continue
            for f in ("box_code", "model", "mac", "serial", "notes"):
                if not cur[f] and it[f]:
                    cur[f] = it[f]
            cur["confidence"] = max(cur["confidence"], it["confidence"])
    return list(out.values())


def extract_ai(images, hints="", detailed=True):
    results = []
    for name, data in images:
        im = _prep(data)
        jobs = [_jpeg(im)]
        if detailed and max(im.size) >= 2400:
            jobs += [_jpeg(t) for t in _tiles(im)]
        with ThreadPoolExecutor(max_workers=5) as ex:
            parts = list(ex.map(lambda j: _claude_call(j, hints), jobs))
        merged = _merge(parts)
        for m in merged:
            m["source_file"] = name
        results += merged
    return results


# ---------------------------------------------------------------- Tesseract (sin clave)
MAC_RE = re.compile(r"(?<![0-9A-Fa-f])((?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}|[0-9A-Fa-f]{12})(?![0-9A-Fa-f])")
MERAKI_SN = re.compile(r"\bQ[0-9A-Z]{3}-[0-9A-Z]{4}-[0-9A-Z]{4}\b")
CATALYST_SN = re.compile(r"\b[A-Z]{3}\d{4}[A-Z0-9]{4}\b")
MODEL_RE = re.compile(r"\b(MR\d{2,3}[A-Z]?|MX\d{2,3}[A-Z\-]*|MS\d{3}[A-Z0-9\-]*|CW9\d{3}[A-Z]?|C9[2-3]\d{2}[A-Z0-9\-]*|C98\d{2}[A-Z0-9\-]*)\b")


def extract_ocr(images, hints=""):
    import pytesseract
    out = []
    for name, data in images:
        im = _prep(data)
        text = pytesseract.image_to_string(im.convert("L"), config="--psm 11")
        text = text.upper().replace("S/N", "SN ")
        macs = list(dict.fromkeys(m.group(1) for m in MAC_RE.finditer(text)
                                  if re.search(r"[A-F]", m.group(1)) or ":" in m.group(1) or "-" in m.group(1)))
        sns = list(dict.fromkeys(MERAKI_SN.findall(text) + CATALYST_SN.findall(text)))
        models = [m.group(1) for m in MODEL_RE.finditer(text)]
        for i in range(max(len(macs), len(sns), 0)):
            out.append({"box_code": "", "model": models[i] if i < len(models) else (models[0] if models else ""),
                        "mac": macs[i] if i < len(macs) else "", "serial": sns[i] if i < len(sns) else "",
                        "confidence": 0.4, "notes": "OCR local: revisa los datos y escribe el código de caja",
                        "source_file": name})
    return out


def extract(images, known_models=None, detailed=True):
    """images: lista de (nombre, bytes). Devuelve (proveedor, items)."""
    hints = ""
    if known_models:
        hints = "- Modelos esperados en este proyecto (úsalos para desambiguar): " + ", ".join(sorted(set(known_models)))
    if PROVIDER_OVERRIDE:
        return "test", PROVIDER_OVERRIDE(images, hints)
    if ai_available():
        return "claude", extract_ai(images, hints, detailed)
    return "tesseract", extract_ocr(images, hints)
