"""Reglas específicas del sitio PARGE (Maison Bram Paris Gare de l'Est): racks, switches y puertos de los APs."""
import re
from .db import Rack
from . import naming

DEFAULT_PLAN = {
    "switches": {   # código de caja -> rack y qué APs alimenta
        "SW01": {"rack": "MDF", "feeds": None},         # core: sin APs
        "SW02": {"rack": "MDF", "feeds": "corridor"},   # C9300-24U: APs de pasillos (CORR)
        "SW03": {"rack": "IDF", "feeds": "rooms"},      # C9300-48U: APs de habitaciones
        "SW04": {"rack": "IDF", "feeds": None},         # cuarto switch: sin conexiones por ahora
    }
}
RACKS = {"MDF": ("MDF", "MDF"), "IDF": ("IDF", "IDF")}   # código -> (tipo, ubicación)


def _num(code):
    return naming.parse_box_code(code)[1] or 0


def _switch_rank(model):
    m = str(model or "").upper()
    if "9300X" in m: return 0
    if m.startswith("MS"): return 4
    if "24" in m: return 1
    if "48" in m: return 2
    return 3


def apply(db, p):
    """Idempotente: crea MDF/IDF, numera y ubica los switches y asigna puerto a cada AP. Respeta los puertos fijados a mano."""
    cfg = dict(p.config)
    plan = cfg.get("site_plan") or DEFAULT_PLAN
    cfg["site_plan"] = plan
    report = {"warnings": [], "ports": {}}

    # zonas por modelo: los CW9172I y CW9174I son de pasillo (CORR)
    zones = dict(cfg.get("model_zones") or {})
    for d in p.devices:
        if d.dtype == "AP" and d.model and d.model not in zones and re.search(r"9172I|9174I", d.model.upper()):
            zones[d.model] = "CORR"
    cfg["model_zones"] = zones
    p.config = cfg

    # racks
    racks = {r.code: r for r in p.racks}
    for code, (rtype, loc) in RACKS.items():
        if code not in racks:
            r = Rack(project_id=p.id, code=code, rtype=rtype, location=loc, units=42); db.add(r); db.flush(); racks[code] = r

    # switches: numeración (core, 24 puertos, 48 puertos, resto) y rack
    sws = [d for d in p.devices if d.dtype == "SW"]
    pending = [d for d in sws if not d.box_code]
    if pending:
        pending.sort(key=lambda d: (_switch_rank(d.model), d.id))
        r24 = [d for d in pending if _switch_rank(d.model) == 1]
        order = [d for d in pending if _switch_rank(d.model) == 0] + r24[:1] + [d for d in pending if _switch_rank(d.model) == 2] \
            + r24[1:] + [d for d in pending if _switch_rank(d.model) in (3, 4)]
        pre = p.config["box_prefixes"].get("SW", "SW")
        n = max([_num(d.box_code) for d in sws if d.box_code] or [0])
        for d in order:
            n += 1; d.box_code = f"{pre}{n:02d}"
    by_code = {d.box_code: d for d in sws if d.box_code}
    for code, rule in plan["switches"].items():
        d = by_code.get(code)
        if d and not d.rack_id and rule.get("rack") in racks:
            d.rack_id = racks[rule["rack"]].id
    db.flush()

    # puertos de los APs
    aps = [d for d in p.devices if d.dtype == "AP"]
    no_code = [d for d in aps if not d.box_code and not d.spare]
    if no_code: report["warnings"].append(f"{len(no_code)} AP(s) sin código de caja: numera las cajas primero.")
    feeds = {"corridor": [], "rooms": []}
    for d in aps:
        if d.spare or not d.box_code: continue
        feeds["corridor" if zones.get(d.model) else "rooms"].append(d)
    for k in feeds: feeds[k].sort(key=lambda d: (_num(d.box_code), d.id))
    for d in aps:                                           # los que no entran en ninguna lista pierden su puerto automático
        if not d.port_manual and (d.spare or not d.box_code): d.sw_id = d.sw_port = None
    for code, rule in plan["switches"].items():
        sw, feed = by_code.get(code), rule.get("feeds")
        if not feed: continue
        if sw is None:
            report["warnings"].append(f"No encuentro el switch {code}: sin él no puedo asignar puertos de {feed}."); continue
        total = naming.switch_ports(sw.model)
        taken = {d.sw_port for d in aps if d.sw_id == sw.id and d.port_manual and d.sw_port}
        port, count = 1, 0
        for d in feeds[feed]:
            if d.port_manual and d.sw_id: continue
            while port in taken: port += 1
            if port > total:
                d.sw_id = d.sw_port = None
                report["warnings"].append(f"{d.box_code}: no cabe en {code} ({total} puertos)"); continue
            d.sw_id, d.sw_port = sw.id, port; port += 1; count += 1
        report["ports"][code] = count
    db.commit()
    return report
