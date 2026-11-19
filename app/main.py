import json
import os, io, csv, copy, shutil, datetime as dt
from urllib.parse import unquote
from typing import Optional
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Body, Request
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, object_session

from .db import (init_db, SessionLocal, DATA_DIR, ChainProfile, Project, Device, Rack, Scan, ApPlan, ApMarker, MaterialItem)
from . import naming, inventory as inv, vision, approcess as ap, siteplan, storage, auth
from PIL import Image, ImageOps

STATIC = os.path.join(os.path.dirname(__file__), "static")
app = FastAPI(title="Altavia · Idóneos — Site Deployment Kit", dependencies=[Depends(auth.auth_dep)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _project(db, pid):
    p = db.get(Project, pid)
    if not p:
        raise HTTPException(404, "Proyecto no encontrado")
    return p


@app.on_event("startup")
def startup():
    init_db()
    db = SessionLocal()
    for name, cfg in naming.DEFAULT_PROFILES.items():
        if not db.query(ChainProfile).filter_by(name=name).first():
            db.add(ChainProfile(name=name, config=cfg))
    db.commit()
    for pr in db.query(Project):                 # proyectos creados antes de existir floor_labels
        cfg = dict(pr.config); changed = False
        if "floor_labels" not in cfg: cfg["floor_labels"] = {"-1": "LL", "0": "GF"}; changed = True
        if "0" not in cfg["floor_labels"]: cfg["floor_labels"] = {**cfg["floor_labels"], "0": "GF"}; changed = True
        if "model_zones" not in cfg: cfg["model_zones"] = {}; changed = True
        if "AP_ZONE" not in cfg["templates"]:
            cfg["templates"] = {**cfg["templates"], "AP_ZONE": naming.HILTON["templates"]["AP_ZONE"]}; changed = True
        for k in ("AP", "AP_ZONE"):                  # los APs llevan 3 dígitos (AP010)
            t = cfg["templates"].get(k, "")
            if t.endswith("{ROLE}{N:02}"):
                cfg["templates"] = {**cfg["templates"], k: t[:-len("{N:02}")] + "{N:03}"}; changed = True
        if changed: pr.config = cfg
    db.commit()
    if not db.query(Project).count():
        db.add(Project(name="Maison Bram Paris Gare de l'Est", chain="Hilton", inn_code="PARGE",
                       brand="Tapestry Collection by Hilton", config=copy.deepcopy(naming.HILTON)))
        db.commit()
    db.close()


# ------------------------------------------------------------------ serialización
def dev_out(project, d, racks, by_id=None):
    r = racks.get(d.rack_id)
    by_id = by_id or {x.id: x for x in project.devices}
    sw = by_id.get(d.sw_id) if d.sw_id else None
    name, missing = naming.device_name(project, d, r)
    return {"id": d.id, "dtype": d.dtype, "box_code": d.box_code, "role": d.role, "seq": d.seq, "model": d.model,
            "model_short": naming.short_model(d.model, project.config.get("model_aliases")),
            "serial": d.serial, "mac": d.mac, "ip": d.ip, "cloud_id": d.cloud_id, "comment": d.comment,
            "spare": bool(d.spare), "status": d.status, "source": d.source, "issues": d.issues, "exp_model": d.exp_model,
            "template_name": d.template_name, "floor": d.floor, "room": d.room, "zone": d.zone or "", "rack_id": d.rack_id,
            "rack_code": r.code if r else "", "rack_u": d.rack_u, "rack_h": d.rack_h,
            "name": name, "name_missing": missing, "name_override": d.name_override,
            "serial_ok": inv.serial_ok(d.serial) if d.serial else None,
            "sw_id": d.sw_id, "sw_code": sw.box_code if sw else "", "sw_port": d.sw_port, "port_manual": bool(d.port_manual),
            "installed": bool(d.installed), "installed_at": d.installed_at.isoformat() if d.installed_at else None,
            "install_note": d.install_note or "", "installed_by": d.installed_by or "", "has_photo": bool(d.photo), "photo_v": d.photo or ""}


def proj_out(p):
    return {"has_logo": storage.exists(f"logo:{p.id}", object_session(p)), "logo_v": (p.config or {}).get("logo_v", ""), "id": p.id, "name": p.name, "chain": p.chain, "inn_code": p.inn_code, "brand": p.brand, "config": p.config,
            "n_devices": len(p.devices)}


def rack_out(r):
    return {"id": r.id, "code": r.code, "rtype": r.rtype, "location": r.location, "floor": r.floor, "units": r.units}


# ------------------------------------------------------------------ acceso
APP_VERSION = "2026.11.19-n"


@app.middleware("http")
async def no_cache(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"        # el navegador siempre comprueba si hay versión nueva
    return resp


@app.get("/api/config")
def public_config(request: Request):
    ok = (not auth.enabled()) or request.cookies.get(auth.COOKIE) == auth.session_token()
    return {"auth": auth.enabled(), "logged_in": ok, "team": auth.TEAM, "version": APP_VERSION}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/login")
def login(request: Request, body: dict = Body(...)):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
    if auth.enabled() and not auth.check_code(body.get("code"), ip):
        raise HTTPException(401, "Código incorrecto")
    r = JSONResponse({"ok": True})
    if auth.enabled():
        r.set_cookie(auth.COOKIE, auth.session_token(), max_age=60 * 60 * 24 * 60, httponly=True, samesite="lax",
                     secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return r


@app.post("/api/logout")
def logout():
    r = JSONResponse({"ok": True}); r.delete_cookie(auth.COOKIE); return r


@app.post("/api/projects/{pid}/logo")
async def upload_logo(pid: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    try:
        img = Image.open(io.BytesIO(await file.read())); img.thumbnail((600, 600))
        bio = io.BytesIO(); img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(bio, "PNG")
    except Exception:
        raise HTTPException(400, "No pude leer la imagen (usa PNG, JPG o SVG convertido a PNG)")
    storage.put(f"logo:{pid}", bio.getvalue(), "image/png", db)
    p.config = {**p.config, "logo_v": str(int(dt.datetime.utcnow().timestamp()))}; db.commit()
    return proj_out(p)


@app.get("/api/projects/{pid}/logo")
def get_logo(pid: int, db: Session = Depends(get_db)):
    data, ct = storage.get(f"logo:{pid}", db)
    if not data: raise HTTPException(404)
    return Response(data, media_type=ct, headers={"Cache-Control": "max-age=3600"})


# ------------------------------------------------------------------ estado / perfiles / proyectos
@app.get("/api/status")
def status():
    return {"ai": vision.ai_available(), "model": os.environ.get("LABEL_MODEL", "claude-sonnet-5-5"),
            "device_types": naming.DEVICE_TYPES}


@app.get("/api/profiles")
def profiles(db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "config": p.config} for p in db.query(ChainProfile).order_by(ChainProfile.name)]


@app.post("/api/profiles")
def save_profile(body: dict = Body(...), db: Session = Depends(get_db)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Falta el nombre de la cadena")
    p = db.query(ChainProfile).filter_by(name=name).first()
    if p: p.config = body["config"]
    else: db.add(ChainProfile(name=name, config=body["config"]))
    db.commit()
    return {"ok": True}


@app.get("/api/projects")
def projects(db: Session = Depends(get_db)):
    return [proj_out(p) for p in db.query(Project).order_by(Project.id)]


@app.post("/api/projects")
def new_project(body: dict = Body(...), db: Session = Depends(get_db)):
    prof = db.query(ChainProfile).filter_by(name=body.get("chain", "Hilton")).first()
    cfg = copy.deepcopy(prof.config if prof else naming.HILTON)
    p = Project(name=body["name"], chain=body.get("chain", "Hilton"), inn_code=body.get("inn_code", "").upper(),
                brand=body.get("brand", ""), config=cfg)
    db.add(p); db.commit()
    return proj_out(p)


@app.put("/api/projects/{pid}")
def update_project(pid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    for k in ("name", "inn_code", "brand", "chain"):
        if k in body: setattr(p, k, body[k].upper() if k == "inn_code" else body[k])
    if "config" in body:
        p.config = body["config"]
    db.commit()
    return proj_out(p)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, db: Session = Depends(get_db)):
    db.delete(_project(db, pid)); db.commit(); return {"ok": True}


@app.post("/api/projects/{pid}/reset")
def reset_project(pid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Vacía lo cargado (equipos, racks, escaneos, AP Plan, fotos) y conserva los ajustes del sitio. Para pasar de pruebas a datos reales."""
    if body.get("confirm") != "VACIAR": raise HTTPException(400, "Falta la confirmación")
    p = _project(db, pid)
    for d in p.devices:
        if d.photo: storage.delete(d.photo, db)
    for pl in db.query(ApPlan).filter_by(project_id=pid): storage.delete(f"plan:{pl.id}", db); db.delete(pl)
    for sc in db.query(Scan).filter_by(project_id=pid): db.delete(sc)
    for x in db.query(MaterialItem).filter_by(project_id=pid): db.delete(x)
    for d in list(p.devices): db.delete(d)
    for r in list(p.racks): db.delete(r)
    db.commit()
    return {"ok": True}


@app.get("/api/name-preview")
def name_preview(template: str, inn: str = "PARGE", model: str = "CW9172H", floor: str = "2", room: str = "202",
                 loc: str = "MDF", role: str = "AP", n: int = 23, zone: str = "CORR"):
    ctx = {"INN": inn, "MODEL": naming.short_model(model), "FLOOR": floor, "ROOM": room, "LOC": loc, "ROLE": role,
           "N": n, "CODE": f"{role}{n:02d}", "TYPE": "AP", "ZONE": zone}
    name, missing = naming.render_name(template, ctx)
    return {"name": name, "missing": missing}


# ------------------------------------------------------------------ inventario
@app.get("/api/projects/{pid}/devices")
def devices(pid: int, db: Session = Depends(get_db)):
    p = _project(db, pid)
    racks = {r.id: r for r in p.racks}
    ds = sorted(p.devices, key=lambda d: (d.dtype, naming.parse_box_code(d.box_code)[1] or 99999, d.id))
    by_id = {x.id: x for x in p.devices}
    return [dev_out(p, d, racks, by_id) for d in ds]


@app.get("/api/projects/{pid}/summary")
def summary(pid: int, db: Session = Depends(get_db)):
    p = _project(db, pid)
    by_status, by_model = {}, {}
    for d in p.devices:
        by_status[d.status] = by_status.get(d.status, 0) + 1
        k = (d.dtype, d.model or "?")
        e = by_model.setdefault(k, {"dtype": d.dtype, "model": d.model or "?", "total": 0, "with_box": 0, "received": 0})
        e["total"] += 1
        e["with_box"] += 1 if d.box_code else 0
        e["received"] += 1 if d.status != "expected" else 0
    return {"by_status": by_status, "models": sorted(by_model.values(), key=lambda e: (e["dtype"], e["model"])),
            "has_template": any(d.source == "template" for d in p.devices)}


def _autoplan(db, pid):
    """Reparte solo los números de caja entre las posiciones del AP Plan y rellena planta/habitación de cada AP.
    Se llama tras cualquier cambio de inventario o de plano, para que el técnico no tenga que pulsar nada."""
    try:
        plan = db.query(ApPlan).filter_by(project_id=pid).first()
        if plan and plan.color_map:
            _restore_plan(plan, db)
            ap.auto_assign(db, plan, db.get(Project, pid), inv.norm_model)
    except Exception:
        db.rollback()


@app.post("/api/projects/{pid}/devices")
def add_device(pid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    model, code = inv.canon_model(p, body.get("model", "").strip()), naming.norm_box_code(body.get("box_code", ""))
    dtype = body.get("dtype") or naming.guess_type(model, code, p.config["box_prefixes"])
    d = next((x for x in p.devices if code and x.box_code == code and x.status == "expected" and not x.serial and not x.mac), None)
    if d is None:                       # ¿hay un hueco esperado de ese modelo sin recibir? Se rellena en vez de duplicar
        d = next((x for x in sorted(p.devices, key=lambda x: x.id) if x.status == "expected" and not x.serial and not x.mac
                  and inv.norm_model(x.exp_model or x.model) == inv.norm_model(model) and (not code or not x.box_code)), None)
    if d is not None:
        d.serial = inv.norm_serial(body.get("serial")) or d.serial; d.mac = inv.norm_mac(body.get("mac")) or d.mac
        if code and not d.box_code: d.box_code = code
        d.status = "received"; d.source = d.source or "manual"
    else:
        d = Device(project_id=pid, model=model, box_code=code, serial=inv.norm_serial(body.get("serial")),
                   mac=inv.norm_mac(body.get("mac")), dtype=dtype, source="manual", status="received")
        db.add(d)
    if body.get("floor") not in (None, ""): d.floor = str(body["floor"])
    if body.get("room"): d.room = str(body["room"])
    if body.get("zone"): d.zone = str(body["zone"]).strip().upper()
    inv.meraki_fill(p, d)
    db.commit()
    _autoplan(db, pid)
    return dev_out(p, d, {r.id: r for r in p.racks})


EDITABLE = ("dtype", "box_code", "role", "seq", "model", "serial", "mac", "ip", "cloud_id", "comment", "floor", "room",
            "rack_id", "rack_u", "rack_h", "name_override", "status", "spare", "installed", "install_note", "sw_id", "sw_port", "zone")


@app.post("/api/projects/{pid}/meraki")
async def import_meraki(pid: int, files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    """CSV del dashboard de Meraki (access_points.csv y/o switches.csv): MAC y serial de cada equipo, para completar y validar."""
    p = _project(db, pid); out = {}
    if not any(d.dtype == "AP" for d in p.devices):          # primera carga: nace el inventario esperado de PARGE
        siteplan.seed_site(db, p)
        apply_numbering(pid, {"dtype": "AP", "order": [x[0] for x in siteplan.SEED if x[1] == "AP"], "spares": siteplan.SPARES}, db)
        apply_numbering(pid, {"dtype": "SW", "order": [x[0] for x in siteplan.SEED if x[1] == "SW"], "spares": {}}, db)
        apply_numbering(pid, {"dtype": "MX", "order": [x[0] for x in siteplan.SEED if x[1] == "MX"], "spares": {}}, db)
        p = _project(db, pid)
    for f in files:
        rows = inv.read_meraki_csv(await f.read())
        if not rows: continue
        kind = "aps" if "access" in (f.filename or "").lower() or "ap" == (f.filename or "").lower()[:2] else "sws"
        out[kind] = inv.meraki_apply(p, rows, kind)
    if not out: raise HTTPException(400, "No encontré seriales Meraki (Qxxx-xxxx-xxxx) en los archivos")
    for d in p.devices: inv.meraki_fill(p, d)
    db.commit()
    return out


def _mat_out(x):
    return {"id": x.id, "part_number": x.part_number, "description": x.description, "qty": x.qty, "status": x.status,
            "note": x.note, "checked_by": x.checked_by, "checked_at": x.checked_at.isoformat() if x.checked_at else None}


@app.get("/api/projects/{pid}/material")
def get_material(pid: int, db: Session = Depends(get_db)):
    _project(db, pid)
    rows = db.query(MaterialItem).filter_by(project_id=pid).order_by(MaterialItem.id).all()
    if not rows:                                   # primera vez: nace el material del pedido de PARGE
        from .material import SEED
        for pn, desc, qty in SEED: db.add(MaterialItem(project_id=pid, part_number=pn, description=desc, qty=qty))
        db.commit()
        rows = db.query(MaterialItem).filter_by(project_id=pid).order_by(MaterialItem.id).all()
    return [_mat_out(x) for x in rows]


@app.patch("/api/material/{mid}")
def patch_material(mid: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    x = db.get(MaterialItem, mid)
    if not x: raise HTTPException(404)
    if "description" in body: x.description = str(body["description"] or "")[:200]
    if "qty" in body and str(body["qty"]).isdigit(): x.qty = int(body["qty"])
    if "note" in body: x.note = str(body["note"] or "")[:300]
    if "status" in body:
        st = body["status"]
        if st not in ("pending", "ok", "nok"): raise HTTPException(400, "Estado no válido")
        if st == "nok" and not (body.get("note") or x.note or "").strip():
            raise HTTPException(400, "Si no ha llegado (NOK) escribe una nota: qué falta o cuántos llegaron")
        x.status = st
        x.checked_by = unquote(request.headers.get("x-user-name") or "")[:40] if st != "pending" else ""
        x.checked_at = dt.datetime.utcnow() if st != "pending" else None
    db.commit()
    return _mat_out(x)


@app.get("/api/projects/{pid}/models")
def site_models(pid: int, db: Session = Depends(get_db)):
    """Modelos exactos de este sitio (los del pedido) para elegirlos al añadir un equipo a mano."""
    p = _project(db, pid); out = {}
    for d in p.devices:
        m = d.exp_model or d.model
        if m and m not in out: out[m] = {"model": m, "dtype": d.dtype}
    return sorted(out.values(), key=lambda x: (["AP", "SW", "MX"].index(x["dtype"]) if x["dtype"] in ("AP", "SW", "MX") else 9, x["model"]))


@app.patch("/api/devices/{did}")
def patch_device(did: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    for k, v in body.items():
        if k not in EDITABLE: continue
        if k == "installed":
            v = bool(v)
            d.installed_at = (d.installed_at if d.installed and d.installed_at else dt.datetime.utcnow()) if v else None
            if not (v and d.installed): d.installed_by = unquote(request.headers.get("x-user-name") or "")[:40] if v else ""
        if k in ("sw_id", "sw_port"): v = int(v) if v not in ("", None) else None
        if k == "box_code": v = naming.norm_box_code(v)
        if k == "zone": v = str(v or "").strip().upper()
        if k == "model": v = inv.canon_model(d.project, v)
        if k == "serial": v = inv.norm_serial(v)
        if k == "mac": v = inv.norm_mac(v) or str(v or "")
        if k in ("seq", "rack_id", "rack_u") and v in ("", None): v = None
        setattr(d, k, v)
    if "sw_id" in body or "sw_port" in body:               # conexión a un switch: validar y fijarla a mano
        if d.sw_id and d.sw_port:
            sw = db.get(Device, d.sw_id)
            if not sw or sw.project_id != d.project_id:
                db.rollback(); raise HTTPException(400, "Ese switch no existe")
            if d.sw_port < 1 or d.sw_port > naming.switch_ports(sw.model):
                db.rollback(); raise HTTPException(400, f"{sw.box_code} tiene {naming.switch_ports(sw.model)} puertos")
            other = db.query(Device).filter(Device.sw_id == d.sw_id, Device.sw_port == d.sw_port, Device.id != d.id).first()
            if other:
                msg = f"El puerto {d.sw_port} de {sw.box_code} ya lo usa {other.box_code or other.model}"
                db.rollback(); raise HTTPException(400, msg)
        d.port_manual = bool(d.sw_id or d.sw_port)
        if not d.port_manual: d.sw_id = d.sw_port = None
    if "serial" in body or "mac" in body:
        d.issues = " · ".join(x for x in (d.issues or "").split(" · ") if x and not x.startswith(("serial no está", "la MAC no corresponde", "el serial es de un")))
        inv.meraki_fill(d.project, d)
    db.commit()
    if any(k in body for k in ("model", "box_code", "dtype", "spare")): _autoplan(db, d.project_id)
    p = d.project
    return dev_out(p, d, {r.id: r for r in p.racks})


@app.delete("/api/devices/{did}")
def delete_device(did: int, db: Session = Depends(get_db)):
    d = db.get(Device, did)
    if d:
        for m in db.query(ApMarker).filter_by(device_id=did): m.device_id = None
        pid_ = d.project_id
        db.delete(d); db.commit(); _autoplan(db, pid_)
    return {"ok": True}


@app.post("/api/projects/{pid}/inventory/import")
async def import_inventory(pid: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    data = await file.read()
    try:
        recs, report, skipped = inv.read_template(data, file.filename or "x.xlsx")
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer el archivo: {e}")
    if not recs:
        raise HTTPException(400, "No encontré equipos activos (AP, switch, firewall) en el archivo. Necesito columnas como Part Number/Model y Serial/MAC/Qty.")
    res = inv.import_into_project(db, p, recs)
    siteplan.apply(db, p)
    _autoplan(db, pid)
    return {**res, "sheets": report, "total": len(recs), "skipped": skipped}


# ------------------------------------------------------------------ numeración correlativa de cajas
@app.get("/api/projects/{pid}/numbering")
def get_numbering(pid: int, db: Session = Depends(get_db)):
    p = _project(db, pid)
    out = {}
    for t in ("AP", "SW", "MX"):
        ds = [d for d in p.devices if d.dtype == t]
        if not ds: continue
        models = {}
        for d in ds:
            e = models.setdefault(d.model, {"model": d.model, "count": 0, "spares": 0, "coded": 0})
            e["count"] += 1; e["spares"] += 1 if d.spare else 0; e["coded"] += 1 if d.box_code else 0
        saved = (p.config.get("numbering") or {}).get(t, {})
        order = [m_ for m_ in saved.get("order", []) if m_ in models] + [m_ for m_ in models if m_ not in saved.get("order", [])]
        out[t] = {"models": [models[m_] for m_ in order], "prefix": p.config["box_prefixes"].get(t, t)}
    return out


@app.post("/api/projects/{pid}/numbering")
def apply_numbering(pid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Asigna códigos de caja correlativos (AP01, AP02…) siguiendo un orden de modelos; los últimos N de cada modelo quedan como spare."""
    p = _project(db, pid)
    t, order, spares, renumber = body["dtype"], body.get("order", []), body.get("spares", {}), bool(body.get("renumber"))
    pre = p.config["box_prefixes"].get(t, t)
    ds = [d for d in p.devices if d.dtype == t]
    rank = {m_: i for i, m_ in enumerate(order)}
    ds.sort(key=lambda d: (rank.get(d.model, 999), d.serial or d.cloud_id or "~", d.id))
    if renumber:
        for d in ds: d.box_code = ""
    used = [naming.parse_box_code(d.box_code)[1] or 0 for d in ds if d.box_code]
    n = max(used) if used else 0
    groups = {}
    for d in ds: groups.setdefault(d.model, []).append(d)
    for d in ds:
        if not d.box_code:
            n += 1; d.box_code = f"{pre}{n:02d}"
    for m_, g in groups.items():
        k = int(spares.get(m_, 0) or 0)
        g.sort(key=lambda d: naming.parse_box_code(d.box_code)[1] or 0)
        for i, d in enumerate(g): d.spare = i >= len(g) - k if k else False
    cfg = {**p.config}; cfg["numbering"] = {**(cfg.get("numbering") or {}), t: {"order": order, "spares": spares}}
    p.config = cfg
    db.commit()
    siteplan.apply(db, p)
    _autoplan(db, pid)
    return {"ok": True, "assigned": len(ds)}


@app.get("/api/projects/{pid}/numbering.xlsx")
def numbering_xlsx(pid: int, db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    p = _project(db, pid)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Etiquetas de caja"
    ws.append(["CÓDIGO (rotular en la caja)", "TIPO", "MODELO", "SERIAL", "SPARE", "ESTADO"])
    for d in sorted([d for d in p.devices if d.box_code], key=lambda d: (d.dtype, naming.parse_box_code(d.box_code)[1] or 0)):
        ws.append([d.box_code, d.dtype, d.model, d.serial, "SPARE" if d.spare else "", STATUS_ES.get(d.status, d.status)])
    for c in ws[1]: c.font = Font(bold=True, color="0B3A63"); c.fill = PatternFill("solid", fgColor="CFE3F7")
    for col, w in zip("ABCDEF", (26, 8, 26, 22, 9, 16)): ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO(); wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{p.inn_code or "site"}_box_labels.xlsx"'})


STATUS_ES = {"expected": "Esperado", "received": "Recibido", "confirmed": "Confirmado", "mismatch": "No coincide", "added": "Fuera de plantilla"}


@app.get("/api/inventory/template.xlsx")
def inv_template():
    return Response(inv.template_workbook(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="inventory_template.xlsx"'})


@app.post("/api/projects/{pid}/scan")
async def scan(pid: int, files: list[UploadFile] = File(...), detailed: bool = True, db: Session = Depends(get_db)):
    p = _project(db, pid)
    imgs = [(f.filename or "foto.jpg", await f.read()) for f in files]
    known = [d.exp_model or d.model for d in p.devices if (d.exp_model or d.model)]
    try:
        provider, items = vision.extract(imgs, known, detailed)
    except Exception as e:
        raise HTTPException(500, f"Error leyendo las fotos: {e}")
    sc = Scan(project_id=pid, filename=", ".join(n for n, _ in imgs)[:250], provider=provider, raw=items)
    db.add(sc); db.commit()
    for it in items:
        it["box_code"] = naming.norm_box_code(it.get("box_code", "")) if it.get("box_code") else ""
        it["mac"] = inv.norm_mac(it.get("mac")) or it.get("mac", "")
        it["serial"] = inv.norm_serial(it.get("serial"))
        it["dtype"] = naming.guess_type(it.get("model", ""), it["box_code"], p.config["box_prefixes"])
    return {"scan_id": sc.id, "provider": provider, "items": inv.preview_match(p, items)}


@app.post("/api/projects/{pid}/scan/commit")
def scan_commit(pid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    res = inv.apply_scan_items(db, p, body.get("items", []), body.get("scan_id"))
    return res


@app.get("/api/projects/{pid}/export.xlsx")
def export_xlsx(pid: int, db: Session = Depends(get_db)):
    p = _project(db, pid)
    return Response(inv.export_workbook(p), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{p.inn_code or "site"}_inventory.xlsx"'})


@app.get("/api/projects/{pid}/export.csv")
def export_csv(pid: int, db: Session = Depends(get_db)):
    p = _project(db, pid); racks = {r.id: r for r in p.racks}
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["name", "type", "box_code", "model", "serial", "mac", "floor", "room", "rack", "u", "status", "issues"])
    for d in p.devices:
        r = racks.get(d.rack_id)
        w.writerow([naming.device_name(p, d, r)[0], d.dtype, d.box_code, d.model, d.serial, d.mac, d.floor, d.room,
                    r.code if r else "", d.rack_u or "", d.status, d.issues])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{p.inn_code or "site"}_inventory.csv"'})


# ------------------------------------------------------------------ racks y ubicación
@app.get("/api/projects/{pid}/racks")
def get_racks(pid: int, db: Session = Depends(get_db)):
    return [rack_out(r) for r in sorted(_project(db, pid).racks, key=lambda r: (r.rtype != "MDF", r.code))]


@app.put("/api/projects/{pid}/racks")
def put_racks(pid: int, body: list = Body(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    keep = set()
    for row in body:
        r = db.get(Rack, row["id"]) if row.get("id") else None
        if r is None:
            r = Rack(project_id=pid); db.add(r)
        r.code = (row.get("code") or "").strip().upper() or "RACK"
        r.rtype = row.get("rtype", "MDF"); r.location = row.get("location", ""); r.floor = str(row.get("floor", "") or "")
        r.units = int(row.get("units") or 42)
        db.flush(); keep.add(r.id)
    for r in list(p.racks):
        if r.id not in keep:
            for d in p.devices:
                if d.rack_id == r.id: d.rack_id, d.rack_u = None, None
            db.delete(r)
    db.commit()
    return [rack_out(r) for r in p.racks]


@app.put("/api/projects/{pid}/placements")
def put_placements(pid: int, body: list = Body(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    by_id = {d.id: d for d in p.devices}
    for row in body:
        d = by_id.get(row["id"])
        if not d: continue
        d.rack_id = row.get("rack_id") or None
        d.rack_u = int(row["rack_u"]) if row.get("rack_u") not in (None, "") else None
        d.rack_h = int(row.get("rack_h") or 1)
        if "role" in row: d.role = row["role"] or ""
        if "seq" in row: d.seq = int(row["seq"]) if row["seq"] not in (None, "") else None
        if "floor" in row and row["floor"] not in (None,): d.floor = str(row["floor"])
    db.commit()
    warnings = []
    racks = {r.id: r for r in p.racks}
    occ = {}
    for d in p.devices:
        if d.rack_id and d.rack_u and d.dtype != "AP":
            r = racks.get(d.rack_id)
            if r and d.rack_u + d.rack_h - 1 > r.units:
                warnings.append(f"{naming.device_name(p, d, r)[0]}: se sale del rack {r.code} ({r.units}U)")
            for u in range(d.rack_u, d.rack_u + d.rack_h):
                other = occ.get((d.rack_id, u))
                if other and other != d.id:
                    warnings.append(f"Solape en {r.code if r else '?'} U{u}: {naming.device_name(p, d, r)[0]} y {naming.device_name(p, by_id[other], r)[0]}")
                occ[(d.rack_id, u)] = d.id
    return {"warnings": sorted(set(warnings))}


# ------------------------------------------------------------------ módulo 3: instalación, puertos y fotos


@app.post("/api/projects/{pid}/site-plan")
def apply_site_plan(pid: int, db: Session = Depends(get_db)):
    """Racks MDF/IDF, ubicación de switches y puertos de los APs (recalcula lo que no se haya fijado a mano)."""
    return siteplan.apply(db, _project(db, pid))


def _portmap(p):
    by_id = {d.id: d for d in p.devices}
    racks = {r.id: r for r in p.racks}
    plan = (p.config.get("site_plan") or siteplan.DEFAULT_PLAN)["switches"]
    out = []
    for sw in sorted([d for d in p.devices if d.dtype == "SW"], key=lambda d: (naming.parse_box_code(d.box_code)[1] or 999, d.id)):
        n = naming.switch_ports(sw.model)
        ports = {d.sw_port: d for d in p.devices if d.sw_id == sw.id and d.sw_port}
        r = racks.get(sw.rack_id)
        out.append({"id": sw.id, "box_code": sw.box_code, "model": sw.model, "name": naming.device_name(p, sw, r)[0],
                    "rack": r.code if r else "", "n_ports": n, "feeds": (plan.get(sw.box_code) or {}).get("feeds"),
                    "installed": bool(sw.installed),
                    "ports": [{"port": i, "device_id": ports[i].id, "box_code": ports[i].box_code, "installed": bool(ports[i].installed),
                               "name": naming.device_name(p, ports[i], racks.get(ports[i].rack_id))[0], "room": ports[i].room,
                               "floor": ports[i].floor} if i in ports else {"port": i} for i in range(1, n + 1)]})
    unassigned = [d.id for d in p.devices if d.dtype == "AP" and not d.spare and not d.sw_id]
    return {"switches": out, "unassigned": unassigned}


@app.get("/api/projects/{pid}/portmap")
def portmap(pid: int, db: Session = Depends(get_db)):
    return _portmap(_project(db, pid))


@app.get("/api/projects/{pid}/portmap.xlsx")
def portmap_xlsx(pid: int, db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    p = _project(db, pid); pm = _portmap(p)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Port Map"
    ws.append(["SWITCH", "SWITCH NAME", "MODEL", "RACK", "PORT", "CONNECTED DEVICE", "BOX CODE", "FLOOR", "ROOM", "INSTALLED"])
    for c in ws[1]: c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="0B3A63")
    for sw in pm["switches"]:
        for pt in sw["ports"]:
            ws.append([sw["box_code"], sw["name"], sw["model"], sw["rack"], pt["port"], pt.get("name", ""), pt.get("box_code", ""),
                       pt.get("floor", ""), pt.get("room", ""), ("YES" if pt["installed"] else "NO") if pt.get("device_id") else ""])
    for col, w in zip("ABCDEFGHIJ", (9, 30, 20, 8, 7, 36, 11, 8, 9, 11)): ws.column_dimensions[col].width = w
    bio = io.BytesIO(); wb.save(bio)
    return Response(bio.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{p.inn_code or "site"}_port_map.xlsx"'})


@app.post("/api/devices/{did}/photo")
async def upload_photo(did: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(await file.read()))).convert("RGB")
    except Exception:
        raise HTTPException(400, "No pude leer la imagen")
    img.thumbnail((1600, 1600))
    bio = io.BytesIO(); img.save(bio, "JPEG", quality=82)
    if d.photo: storage.delete(d.photo, db)
    d.photo = f"photo:{d.id}:{int(dt.datetime.utcnow().timestamp())}"
    storage.put(d.photo, bio.getvalue(), "image/jpeg", db); db.commit()
    p = d.project
    return dev_out(p, d, {r.id: r for r in p.racks})


@app.get("/api/devices/{did}/photo")
def get_photo(did: int, w: int = 0, db: Session = Depends(get_db)):
    d = db.get(Device, did)
    data, _ = storage.get(d.photo, db) if d and d.photo else (None, None)
    if not data: raise HTTPException(404)
    if w:
        img = Image.open(io.BytesIO(data)); img.thumbnail((w, w)); bio = io.BytesIO(); img.save(bio, "JPEG", quality=75); data = bio.getvalue()
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.delete("/api/devices/{did}/photo")
def delete_photo(did: int, db: Session = Depends(get_db)):
    d = db.get(Device, did)
    if d and d.photo:
        storage.delete(d.photo, db); d.photo = ""; db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ AP plan
def _ap_models(p):
    out = {}
    for d in p.devices:
        if d.dtype != "AP": continue
        m = d.model or d.exp_model
        if not m: continue
        e = out.setdefault(inv.norm_model(m), {"model": m, "devices": 0, "boxes": 0, "spares": 0})
        if d.spare: e["spares"] += 1; continue
        e["devices"] += 1; e["boxes"] += 1 if d.box_code else 0
    return list(out.values())


def _restore_plan(plan, db=None):
    """El PDF vive en la base de datos; se copia a disco temporal si el contenedor se ha reiniciado."""
    if not os.path.exists(plan.path):
        data, _ = storage.get(f"plan:{plan.id}", db)
        if not data: raise HTTPException(404, "No encuentro el PDF del AP Plan, vuelve a subirlo")
        os.makedirs(os.path.dirname(plan.path), exist_ok=True)
        with open(plan.path, "wb") as f: f.write(data)


def _plan_state(db, plan):
    _restore_plan(plan, db)
    p = db.get(Project, plan.project_id)
    doc_pages = ap.fitz.open(plan.path)
    dims = [{"w": pg.rect.width, "h": pg.rect.height} for pg in doc_pages]; doc_pages.close()
    by_id = {d.id: d for d in p.devices}
    groups = {}
    for m in plan.markers:
        g = groups.setdefault(m.color, {"color": m.color, "count": 0, "ignored": 0, "pages": {}})
        if m.ignored: g["ignored"] += 1; continue
        g["count"] += 1; g["pages"][str(m.page)] = g["pages"].get(str(m.page), 0) + 1
    for c, g in groups.items():
        g["model"] = (plan.color_map or {}).get(c, "")
    return {"expected_per_page": ap.expected_per_page(plan.path),
            "plan": {"id": plan.id, "filename": plan.filename, "pages": plan.pages, "mode": plan.mode,
                     "page_floors": {str(i): (plan.page_floors or {}).get(str(i), str(i + 1)) for i in range(plan.pages)},
                     "color_map": plan.color_map or {}},
            "dims": dims, "colors": sorted(groups.values(), key=lambda g: -g["count"]), "models": _ap_models(p),
            "markers": [{"id": m.id, "page": m.page, "x": m.x, "y": m.y, "size": m.size, "color": m.color, "room": m.room,
                         "ignored": m.ignored, "locked": m.locked, "device_id": m.device_id,
                         "box_code": by_id[m.device_id].box_code if m.device_id in by_id else ""} for m in plan.markers],
            "devices": [{"id": d.id, "box_code": d.box_code, "model": d.model} for d in p.devices if d.dtype == "AP" and d.box_code]}


def _plan_cache(pdf_bytes):
    import hashlib
    try:
        with open(os.path.join(os.path.dirname(__file__), "seed", "plan_cache.json"), encoding="utf-8") as f:
            return json.load(f).get(hashlib.sha256(pdf_bytes).hexdigest())
    except Exception:
        return None


@app.post("/api/projects/{pid}/applan")
async def upload_plan(pid: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = _project(db, pid)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "El AP Plan debe ser un PDF")
    folder = os.path.join(DATA_DIR, "uploads", f"p{pid}"); os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"applan_{int(dt.datetime.utcnow().timestamp())}.pdf")
    pdf_bytes = await file.read()
    with open(path, "wb") as f: f.write(pdf_bytes)
    cached = _plan_cache(pdf_bytes)           # plano ya analizado de antemano: instantáneo y sin gastar memoria
    try:
        if cached:
            mode, pages, markers = cached["mode"], [None] * cached["pages"], cached["markers"]
        else:
            mode, pages, markers = ap.detect(path)
    except Exception as e:
        raise HTTPException(400, f"No pude leer el PDF: {e}")
    for old in db.query(ApPlan).filter_by(project_id=pid): storage.delete(f"plan:{old.id}", db); db.delete(old)
    db.flush()
    floors = cached["floors"] if cached else ap.guess_floors(path)          # una sola vez
    plan = ApPlan(project_id=pid, filename=file.filename, path=path, pages=len(pages), mode=mode,
                  page_floors={str(i): floors.get(str(i), str(i + 1)) for i in range(len(pages))}, color_map={})
    db.add(plan); db.flush()
    storage.put(f"plan:{plan.id}", pdf_bytes, "application/pdf", db)
    for m in markers:
        db.add(ApMarker(plan_id=plan.id, page=int(m["page"]), x=float(m["x"]), y=float(m["y"]), size=float(m["size"]),
                        color=str(m["color"]), room=str(m["room"] or ""), ignored=bool(m["legend"])))
    db.flush(); db.refresh(plan)
    # propuesta de mapeo color -> modelo cuando las cantidades coinciden de forma única
    counts = {}
    for m in plan.markers:
        if not m.ignored: counts[m.color] = counts.get(m.color, 0) + 1
    models = _ap_models(p)
    cmap = {}
    for color in counts:                     # leyenda fija del plano: el color decide el modelo
        hint = ap.color_model_hint(color)
        mod = next((x["model"] for x in models if hint and inv.norm_model(x["model"]).startswith(inv.norm_model(hint))), None)
        if mod: cmap[color] = mod
    if not cmap:                             # plano con otros colores: proponer por cantidades
      for color, n in counts.items():
        cand = [m["model"] for m in models if m["devices"] == n and m["model"] not in cmap.values()]
        if len(cand) == 1: cmap[color] = cand[0]
    if not cmap and len(cmap) < len(counts):          # sin coincidencias exactas: proponer por orden de cantidad si es razonable
        cs = sorted(counts.items(), key=lambda kv: -kv[1])
        ms = sorted(models, key=lambda x: -x["devices"])
        pairs = list(zip(cs, ms))
        if pairs and all(abs(n - mo["devices"]) <= max(4, 0.3 * mo["devices"]) for (c, n), mo in pairs):
            cmap = {c: mo["model"] for (c, n), mo in pairs}
    plan.color_map = cmap
    db.commit()
    _autoplan(db, pid)
    db.refresh(plan)
    return _plan_state(db, plan)


@app.get("/api/projects/{pid}/applan")
def get_plan(pid: int, db: Session = Depends(get_db)):
    plan = db.query(ApPlan).filter_by(project_id=pid).first()
    return _plan_state(db, plan) if plan else None


@app.put("/api/applan/{plan_id}")
def update_plan(plan_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    plan = db.get(ApPlan, plan_id)
    if "page_floors" in body: plan.page_floors = body["page_floors"]
    if "color_map" in body: plan.color_map = {k: v for k, v in body["color_map"].items() if v}
    db.commit()
    if "color_map" in body: _autoplan(db, plan.project_id); db.refresh(plan)
    return _plan_state(db, plan)


@app.post("/api/applan/{plan_id}/assign")
def assign(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(ApPlan, plan_id); _restore_plan(plan, db)
    p = db.get(Project, plan.project_id)
    report = ap.auto_assign(db, plan, p, inv.norm_model)
    return {"report": report, "state": _plan_state(db, plan)}


@app.post("/api/applan/{plan_id}/markers")
def add_marker(plan_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    """Alta manual de un marcador (AP tapado por otro símbolo en el plano)."""
    plan = db.get(ApPlan, plan_id); _restore_plan(plan, db)
    size = body.get("size") or next((m.size for m in plan.markers if m.color == body["color"]), 14)
    pg = int(body["page"])
    doc = ap.fitz.open(plan.path); words = ap._page_words(doc[pg]); doc.close()
    db.add(ApMarker(plan_id=plan_id, page=pg, x=float(body["x"]), y=float(body["y"]), size=size, color=body["color"],
                    room=ap.nearest_room(words, float(body["x"]), float(body["y"]), size)))
    db.commit(); db.refresh(plan)
    return _plan_state(db, plan)


@app.delete("/api/ap-markers/{mid}")
def delete_marker(mid: int, db: Session = Depends(get_db)):
    m = db.get(ApMarker, mid); plan = m.plan
    db.delete(m); db.commit(); db.refresh(plan)
    return _plan_state(db, plan)


@app.patch("/api/ap-markers/{mid}")
def patch_marker(mid: int, body: dict = Body(...), db: Session = Depends(get_db)):
    m = db.get(ApMarker, mid)
    if "ignored" in body: m.ignored = bool(body["ignored"])
    if "room" in body:
        m.room = str(body["room"] or "")
        if m.device_id:
            d = db.get(Device, m.device_id)
            if d: d.room = m.room
    if "device_id" in body:
        new = body["device_id"] or None
        if new:
            for o in db.query(ApMarker).filter(ApMarker.plan_id == m.plan_id, ApMarker.device_id == new, ApMarker.id != m.id):
                o.device_id, o.locked = None, False
        m.device_id, m.locked = new, bool(new)
        if new:
            d = db.get(Device, new); plan = m.plan
            d.floor = str((plan.page_floors or {}).get(str(m.page), m.page + 1))
            if m.room: d.room = m.room
    db.commit()
    return _plan_state(db, m.plan)


@app.get("/api/applan/{plan_id}/page/{n}.png")
def plan_page(plan_id: int, n: int, db: Session = Depends(get_db)):
    plan = db.get(ApPlan, plan_id); _restore_plan(plan, db)
    return Response(ap.render_page(plan.path, n), media_type="image/png", headers={"Cache-Control": "max-age=300"})


@app.get("/api/applan/{plan_id}/annotated.pdf")
def annotated(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(ApPlan, plan_id); p = db.get(Project, plan.project_id); _restore_plan(plan, db)
    data, missing = ap.annotate(plan, p, naming.device_name)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{p.inn_code or "site"}_AP_plan_numbered.pdf"',
                             "X-Unassigned": str(missing)})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
