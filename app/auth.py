"""Acceso sencillo: un único código compartido (variable ACCESS_CODE). Sin usuarios ni roles: despliegue interno.
Si ACCESS_CODE no está definido, la app queda abierta (útil en local)."""
import os, hmac, hashlib, time
from fastapi import Request, HTTPException

ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
TEAM = [n.strip() for n in os.environ.get("TEAM_NAMES", "Héctor,César,Luis,Rubén,Otto").split(",") if n.strip()]
COOKIE = "dk_session"
PUBLIC = {"/api/config", "/api/health", "/api/login", "/api/logout"}
_tries = {}


def enabled():
    return bool(ACCESS_CODE)


def session_token():
    return hmac.new(ACCESS_CODE.encode(), b"deploykit-session-v1", hashlib.sha256).hexdigest()


def check_code(code, ip):
    now = time.time()
    recent = [t for t in _tries.get(ip, []) if now - t < 600]
    if len(recent) >= 10: raise HTTPException(429, "Demasiados intentos, espera unos minutos")
    ok = hmac.compare_digest(str(code or "").strip().encode(), ACCESS_CODE.encode())
    _tries[ip] = recent if ok else recent + [now]
    return ok


def auth_dep(request: Request):
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC or not enabled(): return
    tok = request.cookies.get(COOKIE, "")
    if not tok or not hmac.compare_digest(tok, session_token()):
        raise HTTPException(401, "Introduce el código de acceso")
