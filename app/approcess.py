"""Procesado del AP Plan (PDF): detección de marcadores por color, asignación de números de AP y PDF anotado."""
import re, io, colorsys, collections
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import pymupdf as fitz
import numpy as np
import cv2

ROOM_RE = re.compile(r"^(?:R(?:M|OOM)?[-\s]?)?(\d{3,4}[A-Z]?)$", re.I)
LEGEND_RE = re.compile(r"\b(MR|CW|MX|MS)\s?-?\d{2,4}[A-Z]?\b", re.I)


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in rgb[:3])


def _colored(rgb, min_sat=0.28, min_val=0.25):
    if rgb is None:
        return False
    h, s, v = colorsys.rgb_to_hsv(*rgb[:3])
    return s >= min_sat and v >= min_val


def _cdist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# --------------------------------------------------------------------- detección vectorial
def _vector_candidates(page, allow_stroke=True):
    rot = page.rotation_matrix
    found = []
    for d in page.get_drawings():
        fill, stroke, typ = d.get("fill"), d.get("color"), d.get("type")
        col = fill if _colored(fill) else (stroke if (allow_stroke and fill is None and typ == "s" and _colored(stroke)) else None)
        if col is None:
            continue
        r = d["rect"]
        size = max(r.width, r.height)
        if size < 3 or size > 48 or min(r.width, r.height) / max(size, 0.01) < 0.55:
            continue
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2) * rot
        found.append({"x": c.x, "y": c.y, "size": size, "rgb": col})
    return found


def _dedupe(cands):
    out = []
    for c in sorted(cands, key=lambda c: -c["size"]):
        if any(_cdist(c["rgb"], o["rgb"]) < 0.12 and abs(c["x"] - o["x"]) < max(c["size"], o["size"]) * 0.45
               and abs(c["y"] - o["y"]) < max(c["size"], o["size"]) * 0.45 for o in out):
            continue
        out.append(c)
    return out


def _cluster_colors(cands, tol=0.12):
    """Agrupa candidatos con colores casi iguales; devuelve {hex: [cand...]}."""
    centers, groups = [], []
    for c in cands:
        for i, ce in enumerate(centers):
            if _cdist(ce, c["rgb"]) < tol:
                groups[i].append(c); break
        else:
            centers.append(c["rgb"]); groups.append([c])
    return {_hex(tuple(np.mean([m["rgb"][k] for m in g]) for k in range(3))): g for g in groups}


def _modal_size_filter(group):
    bins = collections.Counter(round(m["size"] / 1.5) for m in group)
    best, _ = bins.most_common(1)[0]
    ref = np.median([m["size"] for m in group if round(m["size"] / 1.5) == best])
    return [m for m in group if 0.65 * ref <= m["size"] <= 1.4 * ref]


# --------------------------------------------------------------------- detección raster (PDF escaneado / imagen)
def _free_cache():
    """MuPDF guarda en caché hasta ~256 MB; en servidores pequeños (512 MB) hay que vaciarla."""
    try:
        fitz.TOOLS.store_shrink(100)
    except Exception:
        pass


def _raster_candidates(page, dpi=150):
    try:
        return _raster_candidates_(page, dpi)
    finally:
        _free_cache()


def _raster_candidates_(page, dpi=150):
    z = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    mask = (hsv[..., 1] >= 85) & (hsv[..., 2] >= 80)
    px = img[mask]
    if len(px) < 20:
        return []
    ys, xs = np.nonzero(mask)
    pxf = px.astype(np.int16)
    del hsv
    q = (px // 32).astype(np.int16)
    keys, counts = np.unique(q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2], return_counts=True)
    del q
    centers = []
    for k in keys[np.argsort(-counts)][:24]:
        rgb = np.array([(k // 64) * 32 + 16, ((k // 8) % 8) * 32 + 16, (k % 8) * 32 + 16], float)
        if counts[list(keys).index(k)] < 25:
            continue
        if all(np.linalg.norm(rgb - c) > 70 for c in centers):
            centers.append(rgb)
    out = []
    for c in centers:
        sel = ((pxf.astype(np.int32) - c.astype(np.int32)) ** 2).sum(1) < 3600      # solo píxeles de la máscara (antes: imagen completa)
        m = np.zeros(mask.shape, np.uint8)
        m[ys[sel], xs[sel]] = 1
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _, stats, cent = cv2.connectedComponentsWithStats(m)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            size = max(w, h) / z
            if 7 <= size <= 48 and min(w, h) / max(w, h) >= 0.6 and area >= 0.55 * w * h:
                cx, cy, D = int(cent[i][0]), int(cent[i][1]), max(w, h)
                R = int(D * 1.0) + 2
                y0, x0 = max(0, cy - R), max(0, cx - R)
                crop = gray[y0:cy + R, x0:cx + R]
                yy, xx = np.ogrid[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]]
                dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
                ann = crop[(dist >= D * 0.62) & (dist <= D * 0.98)]
                dark = bool(ann.size and (ann < 70).mean() > 0.85)        # rodeado de negro por todos lados = barra de leyenda
                out.append({"x": cent[i][0] / z, "y": cent[i][1] / z, "size": size, "rgb": tuple(c / 255.0), "legend": dark})
    return out


# --------------------------------------------------------------------- API de alto nivel
def _page_words(page):
    """Palabras del plano en coordenadas visuales (ya con la rotación de la página aplicada)."""
    out = []
    for w in page.get_text("words"):
        r = fitz.Rect(w[:4]) * page.rotation_matrix
        r.normalize()
        out.append((r.x0, r.y0, r.x1, r.y1, w[4]))
    return out


def nearest_room(words, x, y, size=10, maxd=None):
    maxd = maxd or max(48.0, size * 5)
    best, bd = "", maxd
    for x0, y0, x1, y1, t in words:
        m = ROOM_RE.match(t.strip())
        if not m:
            continue
        d = ((x0 + x1) / 2 - x) ** 2 + ((y0 + y1) / 2 - y) ** 2
        if d ** 0.5 < bd:
            best, bd = m.group(1).upper(), d ** 0.5
    return best


def is_legend(words, x, y, size):
    for x0, y0, x1, y1, t in words:
        if x < x0 < x + size * 9 and abs((y0 + y1) / 2 - y) < max(size, 8) and LEGEND_RE.search(t):
            return True
    return False


def _image_coverage(page):
    area = 0.0
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            area += r.width * r.height
    return area / max(1.0, page.rect.width * page.rect.height)


def _dedupe_any(cands):
    """Un mismo símbolo no puede ser de dos colores: si dos candidatos se solapan, se queda el más grande."""
    out = []
    for c in sorted(cands, key=lambda c: -c["size"]):
        if any(abs(c["x"] - o["x"]) < o["size"] * 0.5 and abs(c["y"] - o["y"]) < o["size"] * 0.5 for o in out):
            continue
        out.append(c)
    return out


def _near(a, b):
    return abs(a["x"] - b["x"]) < max(a["size"], b["size"]) * 0.6 and abs(a["y"] - b["y"]) < max(a["size"], b["size"]) * 0.6


def detect(pdf_path):
    """Devuelve (modo, páginas[{w,h}], marcadores[{page,x,y,size,color,room,legend}]).
    Combina símbolos vectoriales y símbolos que estén dentro de imágenes incrustadas (planos exportados como imagen)."""
    doc = fitz.open(pdf_path)
    pages = [{"w": p.rect.width, "h": p.rect.height} for p in doc]
    per_page, used_raster, used_vector = [], False, False
    for p in doc:
        vec = _dedupe(_vector_candidates(p, allow_stroke=False))
        ras = []
        if _image_coverage(p) > 0.15 or not vec:
            ras = _dedupe_any(_raster_candidates(p))
            ras = [r for r in ras if not any(_near(r, v) and _cdist(r["rgb"], v["rgb"]) < 0.25 for v in vec)]
        used_vector |= bool(vec); used_raster |= bool(ras)
        per_page.append(vec + ras)
    if not any(per_page):                    # último recurso: contornos de color sin relleno
        per_page = [_dedupe(_vector_candidates(p, allow_stroke=True)) for p in doc]
        used_vector = any(per_page)
    mode = "mixto" if used_vector and used_raster else ("raster" if used_raster else "vector")
    markers = []
    allc = [dict(c, page=i) for i, cs in enumerate(per_page) for c in cs]
    groups = _cluster_colors(allc, tol=0.3 if used_raster else 0.12)
    if groups:                                # descarta colores cuyo tamaño no se parece al símbolo dominante (ruido del plano)
        big = max(groups.values(), key=len)
        ref = float(np.median([m["size"] for m in big]))
        groups = {k: g for k, g in groups.items() if 0.65 * ref <= float(np.median([m["size"] for m in g])) <= 1.5 * ref}
    for hexcol, g in groups.items():
        for m in _modal_size_filter(g):
            words = _page_words(doc[m["page"]])
            markers.append({"page": m["page"], "x": m["x"], "y": m["y"], "size": m["size"], "color": hexcol,
                            "room": nearest_room(words, m["x"], m["y"], m["size"]),
                            "legend": bool(m.get("legend")) or is_legend(words, m["x"], m["y"], m["size"])})
    doc.close()
    return mode, pages, markers


def render_page(pdf_path, page_no, width_px=1500):
    doc = fitz.open(pdf_path)
    page = doc[page_no]
    z = width_px / page.rect.width
    png = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False).tobytes("png")
    doc.close()
    return png


def marker_order(markers, pages):
    """Orden de lectura por planta: de arriba a abajo en bandas, y de izquierda a derecha."""
    def key(m):
        band = max(40.0, pages[m.page]["h"] / 14) if pages else 60.0
        return (m.page, int(m.y // band), m.x)
    return sorted(markers, key=key)


def auto_assign(db, plan, project, norm_model):
    """Reparte los números de caja (AP01...) entre las posiciones del plano, modelo por modelo."""
    from .db import Device
    pages = [{"h": 0}] * plan.pages
    try:
        doc = fitz.open(plan.path); pages = [{"h": p.rect.height} for p in doc]; doc.close()
    except Exception:
        pass
    markers = [m for m in plan.markers if not m.ignored]
    locked_ids = {m.device_id for m in markers if m.locked and m.device_id}
    for m in markers:
        if not m.locked:
            m.device_id = None
    aps = [d for d in project.devices if d.dtype == "AP" and d.box_code and not d.spare and d.id not in locked_ids]
    report = []
    for color, model in (plan.color_map or {}).items():
        if not model:
            continue
        nm = norm_model(model)
        pos = marker_order([m for m in markers if m.color == color and not m.locked], pages)
        pool = sorted([d for d in aps if norm_model(d.model) == nm],
                      key=lambda d: int(re.sub(r"\D", "", d.box_code) or 0))
        for m, d in zip(pos, pool):
            m.device_id = d.id
        n_locked = sum(1 for m in markers if m.color == color and m.locked)
        report.append({"color": color, "model": model, "positions": len(pos) + n_locked,
                       "boxes": len(pool) + sum(1 for m in markers if m.color == color and m.locked and m.device_id),
                       "assigned": min(len(pos), len(pool)) + n_locked,
                       "positions_without_box": max(0, len(pos) - len(pool)),
                       "boxes_without_position": max(0, len(pool) - len(pos))})
    by_id = {d.id: d for d in project.devices}
    for m in markers:
        d = by_id.get(m.device_id)
        if d:
            d.floor = str((plan.page_floors or {}).get(str(m.page), m.page + 1))
            if m.room:
                d.room = m.room
    db.commit()
    return report


# --------------------------------------------------------------------- PDF anotado
def annotate(plan, project, device_name_fn):
    doc = fitz.open(plan.path)
    by_id = {d.id: d for d in project.devices}
    placed = {i: [] for i in range(len(doc))}
    unassigned = 0
    for m in sorted(plan.markers, key=lambda m: (m.page, m.y, m.x)):
        if m.ignored:
            continue
        page = doc[m.page]
        d = by_id.get(m.device_id)
        text = d.box_code if d and d.box_code else "?"
        if d and d.dtype == "AP" and re.fullmatch(r"AP\d+", text or ""):
            text = f"AP{int(text[2:]):03d}"                       # en el plano los APs llevan 3 dígitos (AP004)
        if text == "?": unassigned += 1
        fs = max(6.5, min(30.0, m.size * 0.95))
        tw = fitz.get_text_length(text, fontname="hebo", fontsize=fs)
        c_rot = fitz.Point(m.x, m.y) * page.derotation_matrix            # a coordenadas sin rotar
        # candidatos en espacio ya rotado (visual), luego se des-rota
        cands = [(m.x + m.size * 0.6, m.y - fs * 0.87), (m.x - m.size * 0.6 - tw - 6, m.y - fs * 0.87),
                 (m.x - tw / 2 - 3, m.y - m.size * 0.6 - fs * 1.75 - 2), (m.x - tw / 2 - 3, m.y + m.size * 0.6 + 1)]
        best = None
        for (x0, y0) in cands:
            r = fitz.Rect(x0, y0, x0 + tw + 6, y0 + fs * 1.75)
            score = sum(1 for o in placed[m.page] if r.intersects(o))
            if best is None or score < best[0]:
                best = (score, r)
            if score == 0:
                break
        r = best[1]; placed[m.page].append(r)
        rr = fitz.Rect(*[fitz.Point(r.x0, r.y0) * page.derotation_matrix, fitz.Point(r.x1, r.y1) * page.derotation_matrix])
        rr.normalize()
        col = tuple(int(m.color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        page.draw_rect(rr, color=col, fill=(1, 1, 1), width=0.7, fill_opacity=0.9, stroke_opacity=1, radius=0.25)
        page.insert_textbox(rr, text, fontname="hebo", fontsize=fs, color=(0.04, 0.23, 0.39), align=1,
                            rotate=page.rotation if page.rotation else 0)
    # hoja resumen
    rows = []
    for m in plan.markers:
        d = by_id.get(m.device_id)
        if d and not m.ignored:
            n, _ = device_name_fn(project, d, None)
            rows.append((d.box_code, d.model, n, str((plan.page_floors or {}).get(str(m.page), m.page + 1)), m.room))
    rows.sort(key=lambda r: int(re.sub(r"\D", "", r[0]) or 0))
    per = 44
    for i in range(0, max(len(rows), 1), per):
        pg = doc.new_page(width=842, height=595)
        pg.insert_text((36, 40), f"AP list — {project.name} ({project.inn_code})", fontname="hebo", fontsize=14, color=(0.04, 0.23, 0.39))
        y = 66
        for hdr, x in (("Box", 36), ("Model", 100), ("Name", 200), ("Floor", 560), ("Room", 620)):
            pg.insert_text((x, y), hdr, fontname="hebo", fontsize=9, color=(0.3, 0.4, 0.5))
        y += 6
        for r in rows[i:i + per]:
            y += 11.5
            for val, x in zip(r, (36, 100, 200, 560, 620)):
                pg.insert_text((x, y), str(val), fontname="helv", fontsize=8.5, color=(0.1, 0.1, 0.1))
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out, unassigned


REQ_RE = re.compile(r"(\d+)\s*AP\s*requested", re.I)


def expected_per_page(pdf_path):
    """Si el plano indica «N AP requested» en cada página, lo devuelve como control de la detección."""
    doc = fitz.open(pdf_path)
    out = {}
    for i, p in enumerate(doc):
        m = REQ_RE.search(p.get_text())
        if m:
            out[str(i)] = int(m.group(1))
    doc.close()
    return out


def guess_floors(pdf_path):
    """Lee el rótulo «Plan: R+2» / «R-1» / «RDC» de cada página (aunque sea imagen) con OCR. {página: planta}."""
    out = {}
    try:
        import pytesseract
        from PIL import Image
        doc = fitz.open(pdf_path)
        for i, pg in enumerate(doc):
            txt = pg.get_text()
            if not re.search(r"Plan\s*:?\s*(R\s*[+\-]\s*\d+|RDC)", txt, re.I):
                r = pg.rect
                clip = fitz.Rect(r.x0, r.y0, r.x0 + r.width * 0.4, r.y0 + r.height * 0.8)   # solo la zona del rótulo
                pix = pg.get_pixmap(dpi=100, alpha=False, clip=clip)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                txt = pytesseract.image_to_string(img, config="--psm 11")
                del pix, img; _free_cache()
            m = re.search(r"Plan\s*:?\s*R\s*([+\-])\s*(\d+)", txt, re.I)
            if m:
                out[str(i)] = ("-" if m.group(1) == "-" else "") + str(int(m.group(2)))
            elif re.search(r"Plan\s*:?\s*(RDC|R\s*0|GROUND)", txt, re.I):
                out[str(i)] = "0"
        n = len(doc); doc.close()
        for i in range(1, n - 1):               # una planta que el OCR no lee, entre dos plantas consecutivas
            if str(i) not in out and str(i - 1) in out and str(i + 1) in out:
                a, b = int(out[str(i - 1)]), int(out[str(i + 1)])
                if b - a == 2: out[str(i)] = str(a + 1)
    except Exception:
        pass
    return out


PARGE_LEGEND = {"#f0d010": "CW9174I", "#10b050": "CW9172I", "#106d8a": "CW9172H"}   # amarillo / verde (pasillo) / azul (habitación)


def color_model_hint(hexcol):
    """Leyenda del plano de PARGE: cada color es un modelo. Solo vale si el color es (casi) el de la leyenda; el rojo es ruido."""
    try:
        c = [int(hexcol[i:i + 2], 16) for i in (1, 3, 5)]
    except Exception:
        return None
    for ref, model in PARGE_LEGEND.items():
        r = [int(ref[i:i + 2], 16) for i in (1, 3, 5)]
        if sum((a - b) ** 2 for a, b in zip(c, r)) ** 0.5 < 45: return model
    return None
