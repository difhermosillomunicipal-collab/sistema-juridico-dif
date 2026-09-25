from flask import Flask, render_template, request, redirect, url_for, Response, send_file, send_from_directory, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import io
import datetime
import os
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "CAMBIAR-ESTA-CLAVE-ANTES-DE-PRODUCCION")

# ============================================================
# CONFIGURACIÓN
# ============================================================
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///juridico.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB por archivo

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tif", "tiff"}

db = SQLAlchemy(app)

# ============================================================
# CATÁLOGO DE ÁREAS
# Responsables quedan vacíos hasta que se proporcionen.
# ============================================================
AREAS = [
    "Dirección General",
    "Administración",
    "Jurídico",
    "Dirección de la Familia",
    "Asistencia Social",
    "PMA",
    "COMUDIS",
]

RESPONSABLES = {
    "Dirección General": "Paulina",
    "Administración": "Brissa",
    "Jurídico": "Dina",
    "Dirección de la Familia": "Lizeth",
    "Asistencia Social": "Rene",
    "PMA": "Kenna",
    "COMUDIS": "Yaneth",
}

# ============================================================
# MODELO
# ============================================================
class Oficio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    folio = db.Column(db.String(30), unique=True, nullable=False)

    fecha_recepcion = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    fecha_entrega = db.Column(db.DateTime, nullable=False)
    fecha_limite = db.Column(db.DateTime, nullable=False)

    tipo_documento = db.Column(db.String(100), nullable=False)
    autoridad_remitente = db.Column(db.String(200))
    asunto = db.Column(db.Text, nullable=False)

    area = db.Column(db.String(100), nullable=False)
    responsable = db.Column(db.String(200), default="")
    estatus = db.Column(db.String(30), default="PENDIENTE", nullable=False)

    observaciones = db.Column(db.Text)

    # Documento original recibido
    documento = db.Column(db.String(300))
    fecha_carga_documento = db.Column(db.DateTime)

    # Contestación
    contestacion = db.Column(db.String(300))
    fecha_contestacion = db.Column(db.DateTime)

    def semaforo(self):
        if self.estatus in ["CONTESTADO", "CONCLUIDO"]:
            return "🔵"

        ahora = datetime.datetime.now()

        if ahora > self.fecha_limite:
            return "🔴"

        diferencia = self.fecha_limite - ahora

        if diferencia.total_seconds() <= 24 * 60 * 60:
            return "🟡"

        return "🟢"



class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(80), unique=True, nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    rol = db.Column(db.String(30), nullable=False)
    area = db.Column(db.String(100), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, default=True, nullable=False)

    def verificar_password(self, password):
        return check_password_hash(self.password_hash, password)


class Historial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    oficio_id = db.Column(db.Integer, db.ForeignKey("oficio.id"), nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.datetime.now, nullable=False)
    accion = db.Column(db.String(200), nullable=False)
    detalle = db.Column(db.Text)
    usuario = db.Column(db.String(100), default="Sistema")




USUARIOS_PROVISIONALES = [
    ("admin", "Administrador del sistema", "ADMIN", "", "Admin2026!"),
    ("recepcion", "Recepción / Filtro", "RECEPCION", "", "Recepcion2026!"),
    ("juridico", "Área Jurídica", "JURIDICO", "Jurídico", "Juridico2026!"),
    ("direccion", "Dirección", "DIRECCION", "", "Direccion2026!"),
    ("usr_direccion_general", "Responsable provisional", "AREA", "Dirección General", "DG2026!"),
    ("usr_administracion", "Responsable provisional", "AREA", "Administración", "ADM2026!"),
    ("usr_juridico", "Responsable provisional", "AREA", "Jurídico", "JUR2026!"),
    ("usr_familia", "Responsable provisional", "AREA", "Dirección de la Familia", "FAM2026!"),
    ("usr_asistencia", "Responsable provisional", "AREA", "Asistencia Social", "AS2026!"),
    ("usr_pma", "Responsable provisional", "AREA", "PMA", "PMA2026!"),
    ("usr_comudis", "Responsable provisional", "AREA", "COMUDIS", "COM2026!"),
]


def sincronizar_responsables_pendientes():
    cambios = 0
    for oficio in Oficio.query.all():
        responsable_actual = (oficio.responsable or "").strip()
        if responsable_actual in ("", "PENDIENTE", "Pendiente de asignar"):
            nuevo_responsable = RESPONSABLES.get(oficio.area, "")
            if nuevo_responsable:
                oficio.responsable = nuevo_responsable
                cambios += 1

    if cambios:
        db.session.commit()


def crear_usuarios_provisionales():
    for usuario, nombre, rol, area, password in USUARIOS_PROVISIONALES:
        existente = Usuario.query.filter_by(usuario=usuario).first()
        if not existente:
            db.session.add(
                Usuario(
                    usuario=usuario,
                    nombre=nombre,
                    rol=rol,
                    area=area,
                    password_hash=generate_password_hash(password),
                    activo=True,
                )
            )
    db.session.commit()


with app.app_context():
    db.create_all()
    crear_usuarios_provisionales()
    sincronizar_responsables_pendientes()


# ============================================================
# FUNCIONES
# ============================================================
def siguiente_folio():
    anio = datetime.datetime.now().year

    ultimo = (
        Oficio.query
        .filter(Oficio.folio.like(f"JUR-{anio}-%"))
        .order_by(Oficio.id.desc())
        .first()
    )

    if ultimo:
        try:
            numero = int(ultimo.folio.split("-")[-1]) + 1
        except (ValueError, IndexError):
            numero = Oficio.query.count() + 1
    else:
        numero = 1

    return f"JUR-{anio}-{numero:06d}"


def calcular_fecha_limite(fecha_entrega):
    # Primera versión: 3 días naturales.
    return fecha_entrega + datetime.timedelta(days=3)


def registrar_historial(oficio_id, accion, detalle="", usuario="Sistema"):
    movimiento = Historial(
        oficio_id=oficio_id,
        accion=accion,
        detalle=detalle,
        usuario=usuario,
    )
    db.session.add(movimiento)


def archivo_permitido(nombre):
    return (
        "." in nombre
        and nombre.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def guardar_archivo(archivo, folio, prefijo):
    if not archivo or not archivo.filename:
        return None

    if not archivo_permitido(archivo.filename):
        raise ValueError("Formato no permitido. Usa PDF, PNG, JPG, JPEG, TIF o TIFF.")

    extension = archivo.filename.rsplit(".", 1)[1].lower()
    nombre_seguro = secure_filename(archivo.filename)
    nombre_final = f"{folio}_{prefijo}_{uuid.uuid4().hex[:8]}_{nombre_seguro}"

    ruta = os.path.join(app.config["UPLOAD_FOLDER"], nombre_final)
    archivo.save(ruta)

    return nombre_final


# ============================================================
# RECEPCIÓN
# ============================================================
@app.route("/", methods=["GET", "POST"])
def index():
    confirmacion = None
    error = None

    if request.method == "POST":
        try:
            tipo_documento = request.form.get("tipo_documento", "").strip()
            autoridad = request.form.get("autoridad_remitente", "").strip()
            asunto = request.form.get("asunto", "").strip()
            area = request.form.get("area", "").strip()
            observaciones = request.form.get("observaciones", "").strip()

            if not tipo_documento:
                raise ValueError("Debes indicar el tipo de documento.")
            if not asunto:
                raise ValueError("Debes indicar el asunto.")
            if area not in AREAS:
                raise ValueError("Debes seleccionar un área válida.")

            archivo = request.files.get("documento")
            if not archivo or not archivo.filename:
                raise ValueError("Debes seleccionar el documento original.")

            fecha_entrega = datetime.datetime.now()
            fecha_limite = calcular_fecha_limite(fecha_entrega)
            responsable = RESPONSABLES.get(area, "")
            folio = siguiente_folio()

            nombre_documento = guardar_archivo(
                archivo, folio, "ORIGINAL"
            )

            nuevo = Oficio(
                folio=folio,
                fecha_recepcion=fecha_entrega,
                fecha_entrega=fecha_entrega,
                fecha_limite=fecha_limite,
                tipo_documento=tipo_documento,
                autoridad_remitente=autoridad,
                asunto=asunto,
                area=area,
                responsable=responsable,
                estatus="PENDIENTE",
                observaciones=observaciones,
                documento=nombre_documento,
                fecha_carga_documento=datetime.datetime.now(),
            )

            db.session.add(nuevo)
            db.session.flush()

            registrar_historial(
                nuevo.id,
                "Documento recibido",
                f"Se registró el {tipo_documento.lower()} y se asignó el folio {nuevo.folio}.",
                "Recepción",
            )
            registrar_historial(
                nuevo.id,
                "Documento turnado",
                f"Turnado al área: {area}. Responsable: {responsable or 'Pendiente de asignar'}.",
                "Recepción",
            )

            db.session.commit()
            confirmacion = nuevo.folio

        except Exception as e:
            db.session.rollback()
            error = str(e)

    return render_template(
        "index.html",
        folio=confirmacion,
        error=error,
        areas=AREAS,
        responsables=RESPONSABLES,
    )


# ============================================================
# PANEL ADMINISTRATIVO
# ============================================================
def usuario_actual():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return None
    return db.session.get(Usuario, usuario_id)


def puede_ver_todos(usuario):
    return usuario and usuario.activo and usuario.rol in ["ADMIN", "JURIDICO", "DIRECCION"]


def puede_administrar(usuario):
    return usuario and usuario.activo and usuario.rol == "ADMIN"


def puede_modificar_estatus(usuario):
    return usuario and usuario.activo and usuario.rol in ["ADMIN", "JURIDICO"]


def puede_subir_contestacion(usuario, oficio):
    if not usuario or not usuario.activo:
        return False
    if usuario.rol in ["ADMIN", "JURIDICO"]:
        return True
    return usuario.rol == "AREA" and usuario.area == oficio.area


def login_requerido():
    usuario = usuario_actual()
    if not usuario:
        return None, redirect(url_for("login"))
    return usuario, None



# ============================================================
# LOGIN / LOGOUT
# ============================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario_texto = request.form.get("usuario", "").strip()
        password = request.form.get("password", "")

        usuario = Usuario.query.filter_by(usuario=usuario_texto).first()

        if usuario and usuario.activo and usuario.verificar_password(password):
            session["usuario_id"] = usuario.id
            return redirect(url_for("admin_panel"))

        flash("Usuario o contraseña incorrectos.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
def admin_panel():
    usuario, redireccion = login_requerido()
    if redireccion:
        return redireccion

    if puede_ver_todos(usuario):
        oficios = Oficio.query.order_by(Oficio.fecha_limite.asc()).all()
    elif usuario.rol == "AREA":
        oficios = (
            Oficio.query
            .filter_by(area=usuario.area)
            .order_by(Oficio.fecha_limite.asc())
            .all()
        )
    elif usuario.rol == "RECEPCION":
        oficios = Oficio.query.order_by(Oficio.fecha_recepcion.desc()).all()
    else:
        oficios = []

    return render_template(
        "admin.html",
        oficios=oficios,
        areas=AREAS,
        usuario=usuario,
    )


# ============================================================
# DETALLE
# ============================================================
@app.route("/oficio/<int:oficio_id>")
def detalle_oficio(oficio_id):
    usuario, redireccion = login_requerido()
    if redireccion:
        return redireccion

    oficio = Oficio.query.get_or_404(oficio_id)

    if usuario.rol == "AREA" and usuario.area != oficio.area:
        return Response("No tienes permiso para consultar este asunto.", 403)

    if usuario.rol == "RECEPCION":
        # Recepción puede consultar los datos del trámite, pero no gestionar la contestación.
        pass

    historial = (
        Historial.query
        .filter_by(oficio_id=oficio.id)
        .order_by(Historial.fecha.asc())
        .all()
    )

    return render_template(
        "detalle.html",
        oficio=oficio,
        historial=historial,
        usuario=usuario,
    )


# ============================================================
# DESCARGAR / VER DOCUMENTO
# ============================================================
@app.route("/archivo/<int:oficio_id>/<tipo>")
def archivo_oficio(oficio_id, tipo):
    usuario, redireccion = login_requerido()
    if redireccion:
        return redireccion

    oficio = Oficio.query.get_or_404(oficio_id)

    if usuario.rol == "AREA" and usuario.area != oficio.area:
        return Response("No tienes permiso para consultar este asunto.", 403)

    if tipo == "original":
        nombre = oficio.documento
    elif tipo == "contestacion":
        nombre = oficio.contestacion
    else:
        return "Tipo de archivo no válido.", 400

    if not nombre:
        return "El archivo todavía no está cargado.", 404

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        nombre,
        as_attachment=False,
    )


# ============================================================
# SUBIR CONTESTACIÓN
# ============================================================
@app.route("/oficio/<int:oficio_id>/contestacion", methods=["POST"])
def subir_contestacion(oficio_id):
    usuario, redireccion = login_requerido()
    if redireccion:
        return redireccion

    oficio = Oficio.query.get_or_404(oficio_id)

    if not puede_subir_contestacion(usuario, oficio):
        return Response("No tienes permiso para cargar la contestación de este asunto.", 403)

    archivo = request.files.get("contestacion")

    try:
        if not archivo or not archivo.filename:
            raise ValueError("Selecciona la contestación.")

        nombre = guardar_archivo(archivo, oficio.folio, "CONTESTACION")

        oficio.contestacion = nombre
        oficio.fecha_contestacion = datetime.datetime.now()
        oficio.estatus = "CONTESTADO"

        registrar_historial(
            oficio.id,
            "Contestación cargada",
            "Se cargó el documento de contestación.",
            usuario.nombre,
        )

        db.session.commit()

    except Exception:
        db.session.rollback()

    return redirect(url_for("detalle_oficio", oficio_id=oficio.id))


# ============================================================
# ACTUALIZAR ESTATUS
# ============================================================
@app.route("/oficio/<int:oficio_id>/estatus", methods=["POST"])
def actualizar_estatus(oficio_id):
    usuario, redireccion = login_requerido()
    if redireccion:
        return redireccion

    oficio = Oficio.query.get_or_404(oficio_id)

    if not puede_modificar_estatus(usuario):
        return Response("No tienes permiso para cambiar el estatus.", 403)

    estatus_anterior = oficio.estatus
    nuevo_estatus = request.form.get("estatus", "").strip()

    estatus_validos = ["PENDIENTE", "EN REVISION", "CONTESTADO", "CONCLUIDO"]

    if nuevo_estatus in estatus_validos:
        oficio.estatus = nuevo_estatus

        if nuevo_estatus == "CONTESTADO" and not oficio.fecha_contestacion:
            oficio.fecha_contestacion = datetime.datetime.now()

        if estatus_anterior != nuevo_estatus:
            registrar_historial(
                oficio.id,
                "Cambio de estatus",
                f"El estatus cambió de {estatus_anterior} a {nuevo_estatus}.",
                usuario.nombre,
            )

        db.session.commit()

    return redirect(url_for("detalle_oficio", oficio_id=oficio.id))


# ============================================================
# EXCEL
# ============================================================
@app.route("/descargar_excel")
def descargar_excel():
    usuario, redireccion = login_requerido()
    if redireccion:
        return redireccion

    if puede_ver_todos(usuario):
        oficios = Oficio.query.order_by(Oficio.fecha_recepcion.desc()).all()
    elif usuario.rol == "AREA":
        oficios = (
            Oficio.query
            .filter_by(area=usuario.area)
            .order_by(Oficio.fecha_recepcion.desc())
            .all()
        )
    elif usuario.rol == "RECEPCION":
        oficios = Oficio.query.order_by(Oficio.fecha_recepcion.desc()).all()
    else:
        oficios = []

    datos = []
    for o in oficios:
        datos.append({
            "Folio": o.folio,
            "Recepción": o.fecha_recepcion.strftime("%Y-%m-%d %H:%M"),
            "Entrega": o.fecha_entrega.strftime("%Y-%m-%d %H:%M"),
            "Fecha límite": o.fecha_limite.strftime("%Y-%m-%d %H:%M"),
            "Tipo": o.tipo_documento,
            "Autoridad remitente": o.autoridad_remitente,
            "Asunto": o.asunto,
            "Área": o.area,
            "Responsable": o.responsable or "PENDIENTE",
            "Estatus": o.estatus,
            "Documento original": "Sí" if o.documento else "No",
            "Contestación": "Sí" if o.contestacion else "No",
            "Fecha contestación": (
                o.fecha_contestacion.strftime("%Y-%m-%d %H:%M")
                if o.fecha_contestacion else ""
            ),
            "Observaciones": o.observaciones or "",
        })

    df = pd.DataFrame(datos)
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Oficios")

    output.seek(0)

    return send_file(
        output,
        download_name="reporte_juridico.xlsx",
        as_attachment=True,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
