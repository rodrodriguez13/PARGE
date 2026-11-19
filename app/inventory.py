"""Importación de plantillas, validación de lo recibido vs. lo esperado y exportación."""
import io, re, csv
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from .naming import guess_type, norm_box_code, parse_box_code, short_model, device_name, is_active, default_role

# ---------------------------------------------------------------- normalización
def norm_mac(v):
    h = re.sub(r"[^0-9A-Fa-f]", "", str(v or ""))
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).lower() if len(h) == 12 else ""


def norm_serial(v):
    return re.sub(r"\s+", "", str(v or "")).upper().replace("_", "-")


def norm_model(v):
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def canon_model(project, model):
    """Devuelve el modelo exacto del pedido (CW9174I-CFG-HILU) cuando lo escriben corto (CW9174I)."""
    sm = norm_model(model)
    if not sm: return (model or "").strip()
    known = sorted({d.exp_model or d.model for d in project.devices if (d.exp_model or d.model)}, key=len, reverse=True)
    for k in known:
        if norm_model(k) == sm: return k
    for k in known:
        if norm_model(k).startswith(sm) and len(sm) >= 5: return k
    return (model or "").strip()


def serial_ok(s):
    s = norm_serial(s)
    return bool(re.match(r"^Q[0-9A-Z]{3}-[0-9A-Z]{4}-[0-9A-Z]{4}$", s)      # Meraki AP / switches
                or re.match(r"^Q[0-9A-Z]{11}$", s)                           # Meraki MX sin guiones
                or re.match(r"^[A-Z]{3}\d{4}[A-Z0-9]{4}$", s)                # Cisco Catalyst
                or re.match(r"^[A-Z0-9]{8,20}$", s) and False)


def clean_serial(v):
    """Solo para lo leído de la etiqueta de la caja (el OCR/código de barras puede anteponer una «S»).
    OJO: el código con «S» del Hardware Order / packing list NO es el serial, es el Cloud ID (ver read_template)."""
    s = norm_serial(v)
    if s and not serial_ok(s) and s[0] == "S" and serial_ok(s[1:]):
        return s[1:]
    return s


def models_equal(a, b):
    na, nb = norm_model(a), norm_model(b)
    if not na or not nb:
        return True
    return na == nb or na.startswith(nb) or nb.startswith(na) or na in nb or nb in na


# ---------------------------------------------------------------- lectura de plantillas
ALIASES = {
    "name":   ["name", "ap name", "device name", "hostname", "nombre"],
    "model":  ["hardware model", "model", "modelo", "pid", "part number", "part no", "part no.", "pn", "sku"],
    "serial": ["serial number", "serial", "sn", "s/n", "nº serie", "numero de serie"],
    "mac":    ["eth. mac address", "eth mac address", "mac address", "mac", "ethernet mac"],
    "ip":     ["ip address", "ip"],
    "cloud_id": ["cloud id"],
    "location": ["location", "ubicacion", "ubicación"],
    "comment": ["comment", "comentario", "notes", "notas"],
    "box":    ["box code", "box", "caja", "codigo caja", "código caja"],
    "type":   ["type", "tipo", "device type"],
    "qty":    ["qty", "quantity", "cantidad", "units"],
    "status": ["status"],
}


def _find_header(rows):
    for i, r in enumerate(rows[:8]):
        cells = [str(c).strip().lower() if c is not None else "" for c in r]
        cols = {}
        for key, names in ALIASES.items():
            for j, c in enumerate(cells):
                if c in names and key not in cols:
                    cols[key] = j
        if "model" in cols and ("serial" in cols or "mac" in cols or "qty" in cols or "name" in cols):
            return i, cols
    return None, None


def read_template(file_bytes, filename):
    """Devuelve lista de dicts con los dispositivos de la plantilla. Admite xlsx/csv.
    Sirve para el archivo de provisioning de Hilton (hojas '2a' y '2b') o una plantilla genérica."""
    sheets = []
    if filename.lower().endswith((".csv", ".txt")):
        text = file_bytes.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t") if text.strip() else csv.excel
        sheets.append(("csv", [list(r) for r in csv.reader(io.StringIO(text), dialect)]))
    else:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        for ws in wb.worksheets:
            if ws.title.lower() in ("port map",):
                continue
            sheets.append((ws.title, [list(r) for r in ws.iter_rows(values_only=True)]))
    out, report, skipped = [], [], {}
    PN_HEADERS = {"part number", "part no", "part no.", "pn", "sku"}
    for title, rows in sheets:
        h, cols = _find_header(rows)
        if h is None:
            continue
        # Hardware Order / packing list (cabecera «Part Number»): su columna «Serial Number» en realidad es el Cloud ID.
        # El serial real solo sale de la etiqueta de la caja (foto del técnico).
        mh = str(rows[h][cols["model"]] or "").strip().lower()
        serial_is_cloud = mh in PN_HEADERS and "cloud_id" not in cols
        n = 0
        for r in rows[h + 1:]:
            def g(k):
                j = cols.get(k)
                v = r[j] if j is not None and j < len(r) else None
                return "" if v is None else str(v).strip()
            rec = {k: g(k) for k in ALIASES}
            if not (rec["model"] or rec["serial"] or rec["mac"]):
                continue
            qty = int(rec["qty"]) if rec["qty"].isdigit() else 1
            if serial_is_cloud:
                rec["cloud_id"], rec["serial"] = norm_serial(rec["serial"]), ""
            else:
                rec["serial"] = norm_serial(rec["serial"])
            if not is_active(rec["model"]):
                skipped[rec["model"]] = skipped.get(rec["model"], 0) + qty
                continue
            hint = "AP" if re.search(r"\bap\b", title.lower()) else ""
            for _ in range(max(1, min(qty, 500))):
                rec2 = dict(rec)
                rec2["dtype"] = (rec["type"].upper() if rec["type"].upper() in ("AP", "SW", "MX", "WLC", "SRV", "TV", "OTHER")
                                 else (hint or guess_type(rec["model"], rec["box"])))
                rec2["sheet"] = title
                out.append(rec2)
                n += 1
        report.append({"sheet": title, "rows": n})
    return out, report, [{"model": k, "qty": v} for k, v in skipped.items()]


# ---------------------------------------------------------------- reconciliación
def find_existing(devices, serial="", mac="", cloud_id=""):
    s, m, c = norm_serial(serial), norm_mac(mac), norm_serial(cloud_id)
    for d in devices:
        if s and norm_serial(d.serial) == s:
            return d
    for d in devices:
        if c and norm_serial(d.cloud_id) == c:
            return d
    for d in devices:
        if m and norm_mac(d.mac) == m:
            return d
    return None


def _flag(dev, msg):
    dev.issues = (dev.issues + " | " if dev.issues else "") + msg


def import_into_project(db, project, records):
    from .db import Device
    existing = list(project.devices)
    added = matched = 0
    for rec in records:
        serial, mac, cid = norm_serial(rec["serial"]), norm_mac(rec["mac"]), norm_serial(rec.get("cloud_id"))
        d = find_existing(existing, serial, mac, cid)
        if d is None and cid:                  # importación antigua que guardó el Cloud ID como serial -> corregir
            for old in existing:
                if old.source == "template" and old.status == "expected" and old.serial and norm_serial(old.serial) in (cid, cid[1:]):
                    old.cloud_id, old.serial, old.exp_serial = cid, "", ""
                    d = old; break
        if d:                                  # ya había sido recibido/escaneado -> validar
            d.exp_model, d.exp_mac = rec["model"], mac
            if serial: d.exp_serial = serial
            d.template_name = d.template_name or rec["name"]
            d.ip = d.ip or rec["ip"]
            d.cloud_id = d.cloud_id or cid
            if d.status in ("received", "added"):     # los «expected» siguen esperando su foto
                d.status = "confirmed"
            if not models_equal(d.model, rec["model"]):
                d.status = "mismatch"; _flag(d, f"modelo recibido {d.model} ≠ esperado {rec['model']}")
            matched += 1
            continue
        d = Device(project_id=project.id, dtype=rec["dtype"], model=rec["model"], serial=serial, mac=mac,
                   ip=rec["ip"], cloud_id=cid, comment=rec["comment"], status="expected",
                   source="template", exp_model=rec["model"], exp_serial=serial, exp_mac=mac,
                   template_name=rec["name"], box_code=norm_box_code(rec["box"]) if rec["box"] else "",
                   role=default_role(rec["model"], rec["dtype"]))
        db.add(d); existing.append(d); added += 1
    db.commit()
    return {"added": added, "matched": matched}


def apply_scan_items(db, project, items, scan_id=None):
    """Aplica lo leído en las fotos (ya revisado por el técnico) al inventario."""
    from .db import Device
    devices = list(project.devices)
    has_template = any(d.source == "template" for d in devices)
    prefixes = project.config["box_prefixes"]
    res = {"confirmed": 0, "received": 0, "added": 0, "mismatch": 0, "duplicates": 0}
    for it in items:
        serial, mac = clean_serial(it.get("serial")), norm_mac(it.get("mac"))
        model, code = canon_model(project, (it.get("model") or "").strip()), norm_box_code(it.get("box_code") or "")
        dtype = it.get("dtype") or guess_type(model, code, prefixes)
        d = find_existing(devices, serial, mac)
        if d is not None and d.status != "expected":
            res["duplicates"] += 1
            d.box_code = d.box_code or code
            continue
        if d is None and code:                 # el técnico rotula cada caja (AP05): ese código manda sobre el orden de llegada
            d = next((s for s in devices if s.status == "expected" and not s.serial and not s.mac and s.box_code == code), None)
        if d is None:                          # ¿hay un hueco de plantilla sin serial para este modelo?
            for slot in devices:
                if slot.status == "expected" and not slot.serial and not slot.mac and models_equal(slot.exp_model, model):
                    d = slot; break
        if d is not None:                      # coincide con la plantilla
            was_template = d.status == "expected"
            d.model = model or d.model
            d.serial, d.mac = serial or d.serial, mac or d.mac
            if code and d.box_code and code != d.box_code:
                _flag(d, f"en la caja pone {code}, el código asignado es {d.box_code}")
            d.box_code = d.box_code or code
            meraki_fill(project, d)
            d.scan_id = scan_id
            d.source = "template" if d.source == "template" else "scan"
            bad = []
            if d.exp_model and not models_equal(model, d.exp_model): bad.append(f"modelo leído {model} ≠ esperado {d.exp_model}")
            if d.exp_serial and serial and d.exp_serial != serial: bad.append("serial distinto al esperado")
            if d.exp_mac and mac and d.exp_mac != mac: bad.append("MAC distinta a la esperada")
            if bad:
                d.status = "mismatch"; _flag(d, "; ".join(bad)); res["mismatch"] += 1
            else:
                d.status = "confirmed" if (was_template or d.exp_model or d.exp_serial or d.exp_mac) else "received"
                res["confirmed" if d.status == "confirmed" else "received"] += 1
            continue
        d = Device(project_id=project.id, dtype=dtype, model=model, serial=serial, mac=mac, box_code=code,
                   role=default_role(model, dtype), source="scan", scan_id=scan_id, status="added" if has_template else "received")
        if has_template:
            _flag(d, "no estaba en la plantilla")
        if serial and not serial_ok(serial): _flag(d, "formato de serial no reconocido, revisar")
        if it.get("mac") and not mac: _flag(d, "MAC con formato inválido, revisar")
        meraki_fill(project, d)
        db.add(d); devices.append(d)
        res["added" if has_template else "received"] += 1
    db.commit()
    return res


def preview_match(project, items):
    """Para la pantalla de revisión: dice qué pasaría con cada elemento leído."""
    devices = list(project.devices)
    has_template = any(d.source == "template" for d in devices)
    seen = {}
    for it in items:
        s, m = norm_serial(it.get("serial")), norm_mac(it.get("mac"))
        d = find_existing(devices, s, m)
        flags = []
        if s and not serial_ok(s): flags.append("serial: formato no reconocido")
        if it.get("mac") and not m: flags.append("MAC inválida")
        if not it.get("box_code"): flags.append("sin código de caja")
        key = s or m
        if key and key in seen: flags.append("repetido en este lote")
        seen[key] = True
        if d is None:
            it["match"] = "nuevo (no está en plantilla)" if has_template else "nuevo"
        elif d.status != "expected":
            it["match"] = "ya escaneado"; flags.append("ya existe en el inventario")
        elif d.exp_model and not models_equal(it.get("model"), d.exp_model):
            it["match"] = "no coincide"; flags.append(f"esperado {d.exp_model}")
        else:
            it["match"] = "coincide con plantilla"
        if d is not None and d.box_code and it.get("box_code") and norm_box_code(it["box_code"]) != d.box_code:
            flags.append(f"el código asignado a este equipo es {d.box_code}")
        it["flags"] = flags
    return items


# ---------------------------------------------------------------- exportación
HDR_FILL = PatternFill("solid", fgColor="CFE3F7")


def _loc_text(d, racks):
    if d.spare:
        return "SPARE"
    if d.dtype in ("AP", "TV"):
        parts = []
        if d.floor: parts.append(f"Level {d.floor}")
        if d.room: parts.append(f"Room {d.room}")
        return " - ".join(parts)
    r = racks.get(d.rack_id)
    return " - ".join(x for x in ((r.code if r else ""), (r.location if r else "")) if x)


def export_workbook(project):
    racks = {r.id: r for r in project.racks}
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "2a. hardware inventory"
    ws.append(["STATUS", "NAME", "LOCATION", "HARDWARE MODEL", "IP ADDRESS", "ETH. MAC ADDRESS", "CLOUD ID",
               "SERIAL NUMBER", "COMMENT", "BOX CODE", "RACK", "U"])
    ws2 = wb.create_sheet("2b. AP inventory")
    ws2.append(["STATUS", "AP NAME", "LOCATION", "HARDWARE MODEL", "IP ADDRESS", "SERIAL NUMBER", "ETH MAC ADDRESS",
                "CHANNEL ", "SSID", "Sensitivity", "Power Setting", "Area", "BOX CODE"])
    def key(d):
        return (d.dtype, parse_box_code(d.box_code)[1] or 9999, d.id)
    n_hw = n_ap = 0
    for d in sorted(project.devices, key=key):
        name, _ = device_name(project, d, racks.get(d.rack_id))
        if d.dtype == "AP":
            ws2.append([n_ap + 1, name, _loc_text(d, racks), d.model, d.ip, d.serial, d.mac, None, None, None, None, None, d.box_code]); n_ap += 1
        else:
            r = racks.get(d.rack_id)
            ws.append([None, name, _loc_text(d, racks), d.model, d.ip, d.mac, d.cloud_id, d.serial, d.comment,
                       d.box_code, r.code if r else "", d.rack_u]); n_hw += 1
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True, color="0B3A63"); c.fill = HDR_FILL
        for col in w.columns:
            w.column_dimensions[col[0].column_letter].width = min(46, max(12, max(len(str(c.value or "")) for c in col) + 2))
        w.freeze_panes = "A2"
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


def template_workbook():
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Inventory"
    ws.append(["BOX CODE", "TYPE", "HARDWARE MODEL", "SERIAL NUMBER", "ETH MAC ADDRESS", "NAME", "QTY", "COMMENT"])
    ws.append(["", "AP", "CW9172H", "", "", "", 40, "Si no hay serial/MAC, indica la cantidad y se rellenará al escanear"])
    ws.append(["SW01", "SW", "C9300-48U", "FJC29301L9A", "08:0f:e5:28:5c:80", "", 1, "Si ya viene precargado, se valida al llegar"])
    for c in ws[1]:
        c.font = Font(bold=True, color="0B3A63"); c.fill = HDR_FILL
    for col, w in zip("ABCDEFGH", (12, 8, 22, 22, 22, 34, 8, 60)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO(); wb.save(buf); return buf.getvalue()


# ---------------------------------------------------------------- listados de Meraki (CSV del dashboard)
def read_meraki_csv(data):
    """CSV exportado del dashboard de Meraki (access_points.csv / switches.csv): MAC y serial (= Cloud ID) de cada equipo."""
    import csv, io
    txt = data.decode("utf-8-sig", errors="replace")
    out = []
    for row in csv.DictReader(io.StringIO(txt)):
        low = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}
        mac = norm_mac(low.get("mac address") or low.get("mac_address") or low.get("mac"))
        ser = norm_serial(low.get("serial_number") or low.get("serial") or low.get("cloud id") or low.get("cloud_id"))
        if ser and re.match(r"^Q[0-9A-Z]{3}-[0-9A-Z]{4}-[0-9A-Z]{4}$", ser):
            out.append({"serial": ser, "mac": mac})
    return out


def meraki_apply(project, rows, kind):
    """Guarda el listado en el proyecto, deduce el modelo de cada prefijo de serial por cantidades y rellena MAC/serial."""
    cfg = dict(project.config); m = dict(cfg.get("meraki") or {})
    m[kind] = {r["serial"]: r["mac"] for r in rows}
    groups = {}
    for r in rows: groups.setdefault(r["serial"][:4], []).append(r)
    dt = "AP" if kind == "aps" else "SW"
    counts = {}
    for d in project.devices:
        if d.dtype == dt and not d.spare or d.dtype == dt:
            mod = d.exp_model or d.model
            if mod: counts[mod] = counts.get(mod, 0) + 1
    pm = dict(m.get("prefix_model") or {})
    for pre, g in groups.items():
        cand = [mod for mod, n in counts.items() if n == len(g)]
        if len(cand) == 1 and len([1 for p2, g2 in groups.items() if len(g2) == len(g)]) == 1: pm[pre] = cand[0]
    m["prefix_model"] = pm
    cfg["meraki"] = m; project.config = cfg
    return {"loaded": len(rows), "prefixes": {k: {"count": len(g), "model": pm.get(k, "")} for k, g in groups.items()}}


def meraki_fill(project, d):
    """Completa MAC/serial de un equipo con el listado de Meraki y avisa si el serial no es de este pedido o no cuadra el modelo."""
    m = (project.config or {}).get("meraki") or {}
    book = {**(m.get("aps") or {}), **(m.get("sws") or {}), **(m.get("fixed") or {})}
    if not book: return
    msgs = []
    if d.serial and d.serial in book:
        d.cloud_id = d.serial                      # en Meraki el serial es el Cloud ID
        if not d.mac: d.mac = book[d.serial]
        elif norm_mac(d.mac) != book[d.serial]: msgs.append("la MAC no corresponde a ese serial en Meraki")
        exp = (m.get("prefix_model") or {}).get(d.serial[:4])
        if exp and d.model and norm_model(exp) != norm_model(d.model) and not norm_model(d.model).startswith(norm_model(exp)[:7]):
            msgs.append(f"el serial es de un {exp} y el modelo es {d.model}")
        if exp and not d.model: d.model = exp
    elif d.mac and not d.serial:
        for s_, mac in book.items():
            if mac == norm_mac(d.mac): d.serial = s_; break
    elif d.serial:
        msgs.append("serial no está en el listado de Meraki")
    if msgs:
        cur = [x for x in (d.issues or "").split(" · ") if x]
        for t in msgs:
            if t not in cur: cur.append(t)
        d.issues = " · ".join(cur)
