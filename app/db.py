"""Modelo de datos (SQLite por defecto; cambia DATABASE_URL para Postgres/Supabase)."""
import os, datetime as dt
from sqlalchemy import (create_engine, Column, Integer, String, Float, Boolean, Text,
                        ForeignKey, JSON, DateTime, LargeBinary)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(os.path.join(DATA_DIR, "uploads"), exist_ok=True)
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'deploykit.db')}")
if DATABASE_URL.startswith("postgres://"): DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://"): DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
                       **({} if DATABASE_URL.startswith("sqlite") else {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 5, "pool_recycle": 1800}))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


class ChainProfile(Base):
    """Perfil de cadena hotelera: plantillas de nombres, prefijos de caja, roles."""
    __tablename__ = "chain_profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    config = Column(JSON, nullable=False)


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    chain = Column(String, default="Hilton")
    inn_code = Column(String, default="")
    brand = Column(String, default="")
    config = Column(JSON, nullable=False)          # copia editable del perfil de cadena
    created = Column(DateTime, default=dt.datetime.utcnow)
    devices = relationship("Device", cascade="all, delete-orphan", back_populates="project")
    racks = relationship("Rack", cascade="all, delete-orphan", back_populates="project")


class Rack(Base):
    __tablename__ = "racks"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    code = Column(String, nullable=False)           # MDF, IDF01... (token {LOC} en los nombres)
    rtype = Column(String, default="MDF")           # MDF | IDF
    location = Column(String, default="")           # descripción: "Planta -1, cuarto técnico"
    floor = Column(String, default="")
    units = Column(Integer, default=42)
    project = relationship("Project", back_populates="racks")


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    dtype = Column(String, default="AP")            # AP, SW, MX, WLC, SRV, TV, OTHER
    box_code = Column(String, default="")           # lo que el técnico escribe en la caja: AP01, SW02...
    role = Column(String, default="")               # AS / CS / DS (switches); vacío = rol por defecto del tipo
    seq = Column(Integer, nullable=True)            # número usado en el nombre (por defecto el de box_code)
    model = Column(String, default="")
    serial = Column(String, default="")
    mac = Column(String, default="")
    ip = Column(String, default="")
    cloud_id = Column(String, default="")
    comment = Column(String, default="")
    spare = Column(Boolean, default=False)          # repuesto: lleva código pero no tiene posición en el plano
    status = Column(String, default="received")     # expected | received | confirmed | mismatch | added
    source = Column(String, default="manual")       # template | scan | manual
    exp_model = Column(String, default="")          # valores esperados que traía la plantilla
    exp_serial = Column(String, default="")
    exp_mac = Column(String, default="")
    template_name = Column(String, default="")      # nombre que traía precargado la plantilla
    issues = Column(Text, default="")
    floor = Column(String, default="")
    room = Column(String, default="")
    rack_id = Column(Integer, ForeignKey("racks.id"), nullable=True)
    rack_u = Column(Integer, nullable=True)
    rack_h = Column(Integer, default=1)
    name_override = Column(String, default="")
    sw_id = Column(Integer, nullable=True)          # AP: switch al que va conectado (id de otro Device)
    sw_port = Column(Integer, nullable=True)        # AP: puerto de ese switch
    port_manual = Column(Boolean, default=False)    # puerto fijado a mano: el recálculo automático no lo toca
    installed = Column(Boolean, default=False)      # el técnico lo marcó como instalado
    installed_at = Column(DateTime, nullable=True)
    install_note = Column(String, default="")
    zone = Column(String, default="")               # zona común del AP (CORR…): vacío = AP de habitación
    photo = Column(String, default="")              # clave de la foto de instalación (tabla files)
    installed_by = Column(String, default="")       # email de quien lo marcó
    scan_id = Column(Integer, nullable=True)
    created = Column(DateTime, default=dt.datetime.utcnow)
    project = relationship("Project", back_populates="devices")


class MaterialItem(Base):
    """Material pasivo del pedido (cables, SFP, twinax, fuentes, módulos...): el técnico lo valida con OK o NOK + nota."""
    __tablename__ = "material"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    part_number = Column(String, default="")
    description = Column(String, default="")
    qty = Column(Integer, default=1)
    status = Column(String, default="pending")       # pending | ok | nok
    note = Column(String, default="")
    checked_by = Column(String, default="")
    checked_at = Column(DateTime, nullable=True)


class ExtraPort(Base):
    """Puerto de un switch que no estaba previsto: el técnico le pone una leyenda y marca si está conectado."""
    __tablename__ = "extra_ports"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    sw_id = Column(Integer, index=True)
    port = Column(Integer)
    label = Column(String, default="")
    connected = Column(Boolean, default=False)


class FileBlob(Base):
    """Fotos, planos y logos: van en la base de datos para que sobrevivan a los redespliegues sin discos ni buckets."""
    __tablename__ = "files"
    key = Column(String, primary_key=True)
    content_type = Column(String, default="application/octet-stream")
    data = Column(LargeBinary, nullable=False)
    created = Column(DateTime, default=dt.datetime.utcnow)


class Scan(Base):
    __tablename__ = "scans"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    filename = Column(String)
    provider = Column(String, default="")
    raw = Column(JSON, nullable=True)
    created = Column(DateTime, default=dt.datetime.utcnow)


class ApPlan(Base):
    __tablename__ = "ap_plans"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    filename = Column(String)
    path = Column(String)
    pages = Column(Integer, default=1)
    page_floors = Column(JSON, default=dict)        # {"0": "1", "1": "2"}
    color_map = Column(JSON, default=dict)          # {"#e03131": "CW9172H"}
    mode = Column(String, default="vector")
    created = Column(DateTime, default=dt.datetime.utcnow)
    markers = relationship("ApMarker", cascade="all, delete-orphan", back_populates="plan")


class ApMarker(Base):
    __tablename__ = "ap_markers"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("ap_plans.id"))
    page = Column(Integer, default=0)
    x = Column(Float)
    y = Column(Float)
    size = Column(Float, default=10)
    color = Column(String)
    room = Column(String, default="")
    ignored = Column(Boolean, default=False)
    locked = Column(Boolean, default=False)         # asignación manual: la auto-asignación no la toca
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    plan = relationship("ApPlan", back_populates="markers")


def init_db():
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":      # Supabase expone el esquema public por su API: sin políticas, RLS la deja cerrada (la app entra como propietario)
        from sqlalchemy import text as _t
        with engine.begin() as cn:
            cn.execute(_t("""DO $$ DECLARE t record; BEGIN
              FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
              END LOOP; END $$;"""))
    from sqlalchemy import inspect, text          # migración ligera para bases creadas con una versión anterior
    cols = {c["name"] for c in inspect(engine).get_columns("devices")}
    new_cols = {"spare": "BOOLEAN DEFAULT 0", "sw_id": "INTEGER", "sw_port": "INTEGER", "port_manual": "BOOLEAN DEFAULT 0",
                "installed": "BOOLEAN DEFAULT 0", "installed_at": "DATETIME", "install_note": "VARCHAR DEFAULT ''",
                "photo": "VARCHAR DEFAULT ''", "installed_by": "VARCHAR DEFAULT ''", "zone": "VARCHAR DEFAULT ''"}
    for name, ddl in new_cols.items():
        if name not in cols:
            with engine.begin() as cn:
                cn.execute(text(f"ALTER TABLE devices ADD COLUMN {name} {ddl}"))
