"""Archivos (fotos, PDFs, logos) guardados en la base de datos.
Si se pasa `db` (la sesión de la petición) se usa esa y no se hace commit: lo hace quien llama."""
from contextlib import contextmanager
from .db import SessionLocal, FileBlob


@contextmanager
def _s(db):
    if db is not None:
        yield db, False
    else:
        with SessionLocal() as own:
            yield own, True


def put(key, data, ctype="application/octet-stream", db=None):
    with _s(db) as (s, own):
        b = s.get(FileBlob, key)
        if b: b.data, b.content_type = data, ctype
        else: s.add(FileBlob(key=key, data=data, content_type=ctype))
        s.flush()
        if own: s.commit()


def get(key, db=None):
    with _s(db) as (s, _):
        b = s.get(FileBlob, key)
        return (bytes(b.data), b.content_type) if b else (None, None)


def exists(key, db=None):
    with _s(db) as (s, _):
        return s.query(FileBlob.key).filter_by(key=key).first() is not None


def delete(key, db=None):
    with _s(db) as (s, own):
        b = s.get(FileBlob, key)
        if b:
            s.delete(b); s.flush()
            if own: s.commit()
