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

    p.config = cfg
    apply_fixed(p)
    cfg = dict(p.config)
    # la zona (CORR…) ya no se fija por modelo: se escribe por equipo
    cfg["model_zones"] = {}
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
        feeds["corridor" if re.search(r"9172I|9174I", (d.model or "").upper()) else "rooms"].append(d)
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
            if d.port_manual: continue                      # fijado a mano (o liberado a propósito)
            while port in taken: port += 1
            if port > total:
                d.sw_id = d.sw_port = None
                report["warnings"].append(f"{d.box_code}: no cabe en {code} ({total} puertos)"); continue
            d.sw_id, d.sw_port = sw.id, port; port += 1; count += 1
        report["ports"][code] = count
    db.commit()
    return report


# ---------------------------------------------------------------- equipos esperados de PARGE (sin necesidad del pedido)
SEED = [("CW9174I-CFG-HILU", "AP", 2), ("CW9172I-CFG-HILU", "AP", 16), ("CW9172H-RTG-HILU", "AP", 34),
        ("C9300X-12Y-M-HILU", "SW", 1), ("C9300-24U-M-HILU", "SW", 2), ("C9300-48U-M-HILU", "SW", 1), ("MX105-HW-HILU", "MX", 1)]
SPARES = {"CW9172I-CFG-HILU": 1, "CW9172H-RTG-HILU": 1}


def seed_site(db, p):
    """Crea los huecos de equipos de PARGE (52 APs, 4 switches, 1 firewall) con su modelo exacto. Los seriales y MAC los traen
    los listados de Meraki y la foto de cada caja (el rotulador AP05 decide qué hueco ocupa)."""
    from .db import Device
    from . import naming
    for model, dtype, qty in SEED:
        for _ in range(qty):
            db.add(Device(project_id=p.id, dtype=dtype, model=model, exp_model=model, status="expected", source="template",
                          role=naming.default_role(model, dtype)))
    db.commit(); db.refresh(p)


# serial (Cloud ID) y MAC fijos de los equipos que no vienen en los CSV de APs o que se identifican por modelo único
FIXED = {
    "C9300X-12Y-M-HILU": [("Q5JC-3YLU-85CJ", "30:FE:FA:45:88:80")],
    "C9300-48U-M-HILU": [("Q5KG-SFNN-7MLK", "44:1A:5C:3B:F6:00")],
    "MX105-HW-HILU": [("Q3LB-8AXY-SLWS", "6C:7D:B7:38:B0:44")],
}
# los dos C9300-24U (b8:c9:24:9e:8c:80 y 14:e2:2a:8c:a6:00) se identifican con la foto de la caja (SW02 o SW04)


def apply_fixed(p):
    """Idempotente: da serial y MAC a los equipos únicos (core, 48U, MX105) y los añade al listado de validación."""
    from . import inventory as inv
    cfg = dict(p.config); m = dict(cfg.get("meraki") or {})
    fixed = {}
    for model, lst in FIXED.items():
        for s_, mac in lst: fixed[s_] = inv.norm_mac(mac)
    m["fixed"] = fixed
    pm = dict(m.get("prefix_model") or {})
    for model, lst in FIXED.items():
        for s_, mac in lst: pm[s_[:4]] = model
    m["prefix_model"] = pm
    cfg["meraki"] = m; p.config = cfg
    for d in p.devices:
        lst = FIXED.get(d.exp_model or d.model)
        if lst and not d.serial and d.status in ("expected", "received"):
            d.serial, d.mac = lst[0][0], inv.norm_mac(lst[0][1])
            d.exp_serial, d.exp_mac = d.serial, d.mac
