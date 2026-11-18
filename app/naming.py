"""Perfiles de cadena y motor de nombres de dispositivos.

Tokens disponibles en las plantillas de nombre:
  {INN}    Inn code del hotel (PARGE)
  {MODEL}  código corto del modelo (9172H, 9300, 105...)  -> se deduce o se fija en 'model_aliases'
  {FLOOR}  planta (viene del AP Plan o del rack)
  {ROOM}   habitación / zona (viene del AP Plan, editable)
  {LOC}    código del rack donde está (MDF, IDF01)
  {ROLE}   rol: AP, AS (access switch), CS (core), MX, WLC...
  {N}      número, por defecto el de la caja (AP23 -> 23). Con relleno: {N:02} {N:03}
  {CODE}   código tal cual escrito en la caja (AP23, SW02)
  {TYPE}   tipo de dispositivo (AP, SW, MX...)
  {ZONE}   zona común del modelo (CORR...), solo en la plantilla AP_ZONE
Todo lo demás (p. ej. "HLT-") es texto fijo.
"""
import re, copy

DEVICE_TYPES = ["AP", "SW", "MX", "WLC", "SRV", "TV", "OTHER"]

HILTON = {
    "label": "Hilton",
    "templates": {
        "AP":  "HLT-{INN}-{MODEL}-{FLOOR}-{ROOM}-{ROLE}{N:03}",
        "AP_ZONE": "HLT-{INN}-{MODEL}-{FLOOR}-{ROOM}-{ZONE}-{ROLE}{N:03}",   # APs de zona común (pasillo...): llevan {ZONE}, p. ej. CORR
        "SW":  "HLT-{INN}-{MODEL}-{LOC}-{ROLE}{N:02}",
        "MX":  "HLT-{INN}-{MODEL}-{LOC}-{ROLE}{N:02}",
        "WLC": "HLT-{INN}-{MODEL}-{LOC}-{ROLE}{N:02}",
        "SRV": "HLT-{INN}-{MODEL}-{LOC}-{ROLE}{N:02}",
        "TV":  "HLT-{INN}-{MODEL}-{FLOOR}-{ROOM}-{ROLE}{N:03}",
        "OTHER": "HLT-{INN}-{MODEL}-{LOC}-{CODE}",
    },
    # prefijo que el técnico escribe con rotulador en la caja
    "box_prefixes": {"AP": "AP", "SW": "SW", "MX": "MX", "WLC": "WLC", "SRV": "SRV", "TV": "TV"},
    # {ROLE} por defecto para cada tipo (en switches puede ser AS / CS / DS)
    "role_codes": {"AP": "AP", "SW": "AS", "MX": "MX", "WLC": "WLC", "SRV": "SRV", "TV": "TV", "OTHER": "DV"},
    "model_aliases": {},
    "model_zones": {},                      # modelo -> zona (p. ej. CW9172I -> CORR): usa la plantilla AP_ZONE
    "floor_labels": {"-1": "LL"},          # cómo se escribe cada planta en el nombre (-1 -> LL)
    "default_rack_units": 42,
}

DEFAULT_PROFILES = {"Hilton": HILTON}


def blank_profile(label="Nueva cadena", prefix="XXX"):
    p = copy.deepcopy(HILTON)
    p["label"] = label
    p["templates"] = {k: v.replace("HLT", prefix) for k, v in p["templates"].items()}
    return p


# ---------------------------------------------------------------- tipo / modelo
def guess_type(model="", box_code="", prefixes=None):
    """Deduce el tipo de dispositivo por el código de caja o por el modelo."""
    prefixes = prefixes or HILTON["box_prefixes"]
    m = re.match(r"^\s*([A-Za-z]+)\s*0*\d+\s*$", box_code or "")
    if m:
        pre = m.group(1).upper()
        for t, p in prefixes.items():
            if pre == p.upper():
                return t
        if pre == "AS" or pre == "CS" or pre == "DS":
            return "SW"
    mod = (model or "").upper().strip()
    if re.match(r"^(MR|CW|AP)\d", mod) or mod.startswith("MR") and mod[2:3].isdigit():
        return "AP"
    if re.match(r"^(MS\d|C9[23]\d\d|CBS|C9K|WS-C|C1[0-9]{3})", mod) or "9300" in mod or "9200" in mod:
        return "SW"
    if re.match(r"^MX\d", mod):
        return "MX"
    if re.match(r"^(C98\d\d|CW9800|WLC|9800)", mod):
        return "WLC"
    return "OTHER"


def short_model(model, aliases=None):
    """MR36H -> 36H ; CW9172H -> 9172H ; C9300-48U-M-HILU -> 9300 ; MX105-HW-HILU -> 105."""
    if not model:
        return ""
    if aliases and model in aliases:
        return aliases[model]
    s = model.strip().upper()
    s = re.sub(r"^(MR|MX|MS|CW|CBS|C|AP)(?=\d)", "", s)
    s = s.split("-")[0]
    m = re.match(r"^\d+[A-Z]?", s)
    return m.group(0) if m and len(s) <= len(m.group(0)) + 1 else (m.group(0) if m else s)


def parse_box_code(code):
    m = re.match(r"^\s*([A-Za-z]+)\s*0*(\d+)\s*$", code or "")
    return (m.group(1).upper(), int(m.group(2))) if m else (None, None)


def norm_box_code(code, pad=2):
    pre, n = parse_box_code(code)
    return f"{pre}{n:0{pad}d}" if pre else (code or "").strip().upper()


# ---------------------------------------------------------------- render
_TOKEN = re.compile(r"\{(\w+)(?::(\d+))?\}")


def render_name(template, ctx):
    """Devuelve (nombre, [tokens_que_faltan]). Los tokens vacíos se muestran como '?'."""
    missing = []

    def sub(m):
        key, pad = m.group(1).upper(), m.group(2)
        val = ctx.get(key)
        if val in (None, ""):
            missing.append(key)
            return "?"
        if pad and isinstance(val, int):
            return f"{val:0{int(pad)}d}"
        if pad and str(val).isdigit():
            return str(val).zfill(int(pad))
        return str(val)

    return _TOKEN.sub(sub, template), missing


def device_context(project, dev, rack=None):
    cfg = project.config
    pre, n = parse_box_code(dev.box_code)
    role = dev.role or cfg["role_codes"].get(dev.dtype, "")
    return {
        "INN": project.inn_code,
        "MODEL": short_model(dev.model, cfg.get("model_aliases")),
        "FLOOR": (cfg.get("floor_labels") or {}).get(str(dev.floor or (rack.floor if rack else "")), dev.floor or (rack.floor if rack else "")),
        "ROOM": dev.room,
        "LOC": rack.code if rack else "",
        "ROLE": role,
        "N": dev.seq if dev.seq else n,
        "CODE": (dev.box_code or "").upper(),
        "TYPE": dev.dtype,
        "ZONE": (cfg.get("model_zones") or {}).get(dev.model, ""),
    }


def device_name(project, dev, rack=None):
    if dev.name_override:
        return dev.name_override, []
    ctx = device_context(project, dev, rack)
    tpls = project.config["templates"]
    tpl = (tpls.get(dev.dtype + "_ZONE") if ctx["ZONE"] else None) or tpls.get(dev.dtype) or tpls["OTHER"]
    return render_name(tpl, ctx)


_PASSIVE = re.compile(r"(-NM-|NM-\d|CBL|CABLE|PWR|CORD|UTP|FX2|STACK|SFP|XCVR|TRANSCEIVER|STAY|RACK|BRACKET|MOUNT|ADAPTER|LICENSE|LIC-)", re.I)


def is_active(model):
    """Solo equipos activos: AP, switch, firewall/MX, WLC. Excluye cables, módulos de red, SFP, fuentes, licencias..."""
    if not model or _PASSIVE.search(model):
        return False
    return guess_type(model) in ("AP", "SW", "MX", "WLC")


def default_role(model, dtype):
    """Rol por defecto en el nombre: los C9300X (12Y) son core (CS); el resto de switches, access (AS)."""
    if dtype == "SW" and re.search(r"9300X|-12Y", (model or "").upper()):
        return "CS"
    return ""


def switch_ports(model):
    """Nº de puertos de acceso de un switch según su modelo: C9300-24U -> 24, C9300-48U -> 48, C9300X-12Y -> 12, MS350-24 -> 24."""
    m = re.search(r"-(\d{2})[A-Z]*", str(model or "").upper())
    return int(m.group(1)) if m else 24
