from fastapi import FastAPI, UploadFile, File, Form, Request, Response, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional
import pymysql
import pymysql.cursors
import os
import shutil
import re
import pdfplumber

# --------------------------------------------------
# INTEGRACIÓN DE CLOUDINARY
# --------------------------------------------------
import cloudinary
import cloudinary.uploader

cloudinary.config( 
  cloud_name = "qht52nzv", 
  api_key = "837938118572313", 
  api_secret = "icuwlZPhxp3NX7h9s-he5qhrx2U" 
)

app = FastAPI(title="Localizador Médico Mantis")

CARPETA_UPLOADS = "uploads"
os.makedirs(CARPETA_UPLOADS, exist_ok=True)

app.mount("/uploads", StaticFiles(directory=CARPETA_UPLOADS), name="uploads")

def conectar_db():
    # SSL obligatoriamente activado para TiDB Cloud
    ssl_config = {"ssl": True}
    
    conexion = pymysql.connect(
        host=os.getenv("DB_HOST", "gateway01.us-east-1.prod.aws.tidbcloud.com"),
        user=os.getenv("DB_USER", "3fmG3DnnFK7UThH.root"),
        password=os.getenv("DB_PASSWORD", "8LTTaL89iyPxel3e"),
        database=os.getenv("DB_NAME", "test"),
        port=int(os.getenv("DB_PORT", 4000)),
        ssl=ssl_config,
        cursorclass=pymysql.cursors.DictCursor
    )
    with conexion.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password VARCHAR(100) NOT NULL,
                nombre VARCHAR(100) NOT NULL,
                rol VARCHAR(50) DEFAULT 'recepcionista'
            )
        """)
        
        cursor.execute("SELECT COUNT(*) as total FROM usuarios")
        res_user = cursor.fetchone()
        if res_user and res_user['total'] == 0:
            cursor.execute("""
                INSERT IGNORE INTO usuarios (username, password, nombre, rol) VALUES 
                ('jeancarlos', '1234', 'Jean Carlos', 'recepcionista'),
                ('recepcion1', '1234', 'Recepción 1', 'recepcionista'),
                ('alistador1', '1234', 'Alistador Bodega', 'alistador')
            """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS medicamentos (
                id INT AUTO_INCREMENT PRIMARY KEY,
                codigo_mantis VARCHAR(100),
                nombre VARCHAR(255),
                lote VARCHAR(100),
                ubicacion VARCHAR(100),
                observacion TEXT,
                usuario VARCHAR(100) DEFAULT 'General',
                foto TEXT
            )
        """)

        try:
            cursor.execute("ALTER TABLE medicamentos ADD COLUMN usuario_modificacion VARCHAR(100)")
        except Exception:
            pass

        conexion.commit()
    return conexion

def obtener_usuario_sesion(request: Request):
    username = request.cookies.get("usuario_mantis")
    if not username:
        return None
    conexion = conectar_db()
    cursor = conexion.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE username = %s", (username,))
    user = cursor.fetchone()
    conexion.close()
    return user

@app.post("/api/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    conexion = conectar_db()
    cursor = conexion.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE username = %s AND password = %s", (username, password))
    user = cursor.fetchone()
    conexion.close()

    if user:
        response.set_cookie(key="usuario_mantis", value=user['username'], httponly=True)
        return {"ok": True, "mensaje": f"Bienvenido {user['nombre']}"}
    else:
        return {"ok": False, "mensaje": "Usuario o contraseña incorrectos."}

@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("usuario_mantis")
    return {"mensaje": "Sesión cerrada correctamente."}

@app.post("/medicamentos/manual")
async def agregar_o_actualizar_medicamento(
    request: Request,
    id: Optional[int] = Form(None),
    codigo_mantis: str = Form(...),
    nombre: str = Form(...),
    lote: str = Form(...),
    ubicacion: str = Form(...),
    observacion: Optional[str] = Form(""),
    foto: Optional[UploadFile] = File(None)
):
    user = obtener_usuario_sesion(request)
    if not user or user['rol'] == 'alistador':
        raise HTTPException(status_code=403, detail="Los alistadores solo tienen permisos de consulta y observación.")

    usuario_nombre = user['nombre']
    conexion = conectar_db()
    cursor = conexion.cursor()

    nombre_foto = None
    if foto and foto.filename:
        res = cloudinary.uploader.upload(foto.file, folder="mantis_medicamentos")
        nombre_foto = res.get("secure_url")

    if id:
        if nombre_foto:
            sql = """UPDATE medicamentos SET codigo_mantis=%s, nombre=%s, lote=%s, ubicacion=%s, observacion=%s, usuario=%s, foto=%s WHERE id=%s"""
            valores = (codigo_mantis, nombre, lote, ubicacion, observacion, usuario_nombre, nombre_foto, id)
        else:
            sql = """UPDATE medicamentos SET codigo_mantis=%s, nombre=%s, lote=%s, ubicacion=%s, observacion=%s, usuario=%s WHERE id=%s"""
            valores = (codigo_mantis, nombre, lote, ubicacion, observacion, usuario_nombre, id)
    else:
        sql = """INSERT INTO medicamentos (codigo_mantis, nombre, lote, ubicacion, observacion, usuario, foto) VALUES (%s, %s, %s, %s, %s, %s, %s)"""
        valores = (codigo_mantis, nombre, lote, ubicacion, observacion, usuario_nombre, nombre_foto or "")

    cursor.execute(sql, valores)
    conexion.commit()
    conexion.close()
    return {"mensaje": "Operación realizada con éxito."}

# ENDPOINT MODIFICADO: Agrega la nota nueva a la anterior, no la borra
@app.post("/api/medicamentos/{id}/observacion")
async def actualizar_observacion(request: Request, id: int, observacion: str = Form(...)):
    user = obtener_usuario_sesion(request)
    if not user:
        raise HTTPException(status_code=403, detail="No autorizado.")

    conexion = conectar_db()
    cursor = conexion.cursor()
    
    # 1. Obtener lo que ya estaba escrito
    cursor.execute("SELECT observacion FROM medicamentos WHERE id = %s", (id,))
    row = cursor.fetchone()
    obs_actual = row['observacion'] if row and row['observacion'] else ""
    
    # 2. Concatenar la nota nueva sin tocar la vieja
    if obs_actual.strip():
        observacion_final = f"{obs_actual.strip()}\n[{user['nombre']}]: {observacion}"
    else:
        observacion_final = f"[{user['nombre']}]: {observacion}"

    # 3. Guardar el nuevo bloque de texto completo
    cursor.execute(
        "UPDATE medicamentos SET observacion = %s, usuario_modificacion = %s WHERE id = %s",
        (observacion_final, user['nombre'], id)
    )
    conexion.commit()
    conexion.close()
    return {"mensaje": "Observación agregada correctamente."}

@app.post("/api/medicamentos/{id}/foto")
async def actualizar_foto_directa(request: Request, id: int, foto: UploadFile = File(...)):
    user = obtener_usuario_sesion(request)
    if not user or user['rol'] == 'alistador':
        raise HTTPException(status_code=403, detail="Los alistadores no pueden subir fotos.")

    if not foto or not foto.filename:
        return {"error": "Archivo no válido"}
        
    res = cloudinary.uploader.upload(foto.file, folder="mantis_medicamentos")
    nombre_foto = res.get("secure_url")

    conexion = conectar_db()
    cursor = conexion.cursor()
    cursor.execute("UPDATE medicamentos SET foto=%s WHERE id=%s", (nombre_foto, id))
    conexion.commit()
    conexion.close()
    return {"mensaje": "Foto actualizada correctamente."}

@app.post("/api/extraer-pdf")
async def extraer_pdf(request: Request, archivo_pdf: UploadFile = File(...)):
    user = obtener_usuario_sesion(request)
    if not user or user['rol'] == 'alistador':
        raise HTTPException(status_code=403, detail="Los alistadores no tienen permisos de carga masiva.")

    usuario_nombre = user['nombre']
    conexion = conectar_db()
    cursor = conexion.cursor()
    guardados = 0
    omitidos = 0

    PALABRAS_IGNORAR = ["CODIGO", "CÓDIGO", "DESCRIPCION", "DESCRIPCIÓN", "LOTE", "UBICACION", "UBICACIÓN", "TOTAL"]

    with pdfplumber.open(archivo_pdf.file) as pdf:
        for pagina in pdf.pages:
            tablas = pagina.extract_tables()
            for tabla in tablas:
                for fila in tabla:
                    if not fila or len(fila) < 3:
                        continue
                    
                    codigo = str(fila[0] or "").strip()
                    nombre = str(fila[1] or "").strip()
                    lote_ubi = str(fila[2] or "").strip()
                    
                    if codigo.upper() in PALABRAS_IGNORAR or nombre.upper() in PALABRAS_IGNORAR:
                        continue
                    
                    if re.match(r'^(AL|ME|LA|IM|[A-Z]{2,4})[-\s]?\d+.*$', codigo, re.IGNORECASE) or len(codigo) >= 4:
                        nombre_limpio = nombre.replace('\n', ' ').strip()
                        
                        lote = lote_ubi
                        ubicacion = "General"
                        
                        if ' / ' in lote_ubi:
                            partes = lote_ubi.split(' / ')
                            lote = partes[0].strip()
                            ubicacion = partes[1].strip()
                        elif '/' in lote_ubi:
                            partes = lote_ubi.rsplit('/', 1)
                            lote = partes[0].strip()
                            ubicacion = partes[1].strip()

                        sql_verificar = "SELECT id FROM medicamentos WHERE codigo_mantis = %s AND lote = %s"
                        cursor.execute(sql_verificar, (codigo, lote))
                        existe = cursor.fetchone()

                        if existe:
                            omitidos += 1
                        else:
                            sql_insertar = """INSERT INTO medicamentos (codigo_mantis, nombre, lote, ubicacion, observacion, usuario, foto) 
                                             VALUES (%s, %s, %s, %s, %s, %s, %s)"""
                            obs = "" 
                            valores = (codigo, nombre_limpio, lote, ubicacion, obs, usuario_nombre, "")
                            cursor.execute(sql_insertar, valores)
                            guardados += 1

    conexion.commit()
    conexion.close()
    
    if guardados == 0 and omitidos > 0:
        msg = f"No se procesó ningún registro nuevo: los {omitidos} medicamentos ya existen."
    else:
        msg = f"Se agregaron {guardados} registros nuevos a nombre de {usuario_nombre}."
        if omitidos > 0:
            msg += f" ({omitidos} omitidos por estar duplicados)."
            
    return {"mensaje": msg}

@app.delete("/api/medicamentos/{id}")
def eliminar_medicamento(request: Request, id: int):
    user = obtener_usuario_sesion(request)
    if not user or user['rol'] == 'alistador':
        raise HTTPException(status_code=403, detail="Los alistadores solo tienen permisos de consulta.")

    conexion = conectar_db()
    cursor = conexion.cursor()
    cursor.execute("DELETE FROM medicamentos WHERE id = %s", (id,))
    conexion.commit()
    conexion.close()
    return {"mensaje": "Registro eliminado."}

@app.get("/api/medicamentos")
def obtener_medicamentos():
    conexion = conectar_db()
    cursor = conexion.cursor()
    cursor.execute("SELECT * FROM medicamentos ORDER BY id DESC")
    productos = cursor.fetchall()
    conexion.close()
    return productos

@app.get("/", response_class=HTMLResponse)
def cargar_vista(request: Request):
    user = obtener_usuario_sesion(request)

    if not user:
        return """
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Login - Mantis</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
            <style>
                body { background-color: #f8fafc; font-family: 'Inter', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
                .login-card { background: white; border-radius: 16px; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.05); padding: 2.5rem; width: 100%; max-width: 400px; border: 1px solid #e2e8f0; }
                .btn-primary-custom { background-color: #2563eb; border: none; border-radius: 8px; padding: 0.7rem; font-weight: 600; color: white; width: 100%; }
            </style>
        </head>
        <body>
            <div class="login-card">
                <div class="text-center mb-4">
                    <i class="bi bi-box-seam-fill text-primary fs-1"></i>
                    <h4 class="fw-bold mt-2">Acceso a <span class="text-primary">Mantis</span></h4>
                    <p class="text-muted small">Ingresa tus credenciales para continuar</p>
                </div>
                <form id="formLogin">
                    <div class="mb-3">
                        <label class="form-label small fw-medium">Usuario</label>
                        <input type="text" id="username" class="form-control" placeholder="Ej: jeancarlos o alistador1" required>
                    </div>
                    <div class="mb-4">
                        <label class="form-label small fw-medium">Contraseña</label>
                        <input type="password" id="password" class="form-control" placeholder="••••••••" required>
                    </div>
                    <button type="submit" class="btn-primary-custom">Iniciar Sesión</button>
                </form>
            </div>
            <script>
                document.getElementById('formLogin').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const formData = new FormData();
                    formData.append('username', document.getElementById('username').value);
                    formData.append('password', document.getElementById('password').value);

                    const res = await fetch('/api/login', { method: 'POST', body: formData });
                    const data = await res.json();
                    if(data.ok) {
                        window.location.reload();
                    } else {
                        alert(data.mensaje);
                    }
                });
            </script>
        </body>
        </html>
        """

    es_alistador = (user['rol'] == 'alistador')
    rol_badge = '<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle ms-2">Alistador</span>' if es_alistador else '<span class="badge bg-primary-subtle text-primary border border-primary-subtle ms-2">Recepcionista</span>'

    return f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Localizador Médico Mantis</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
        <style>
            :root {{ --primary-color: #2563eb; --bg-color: #f8fafc; --card-bg: #ffffff; --text-main: #0f172a; --text-muted: #64748b; --border-color: #e2e8f0; }}
            body {{ background-color: var(--bg-color); font-family: 'Inter', sans-serif; color: var(--text-main); }}
            .navbar-custom {{ background: white; border-bottom: 1px solid var(--border-color); padding: 1rem 0; }}
            .card-custom {{ background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 16px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
            .form-control, .form-select {{ border-radius: 8px; border: 1px solid var(--border-color); padding: 0.6rem 0.8rem; font-size: 0.9rem; }}
            .btn-primary-custom {{ background-color: var(--primary-color); border: none; border-radius: 8px; padding: 0.7rem; font-weight: 600; color: white; }}
            .table-custom th {{ font-weight: 600; color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; background-color: #f1f5f9; }}
            .table-custom td {{ vertical-align: middle; font-size: 0.9rem; }}
            
            .img-preview {{ width: 44px; height: 44px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border-color); }}
            .btn-cam-pendiente {{ width: 44px; height: 44px; border-radius: 8px; border: 2px dashed #f59e0b; background-color: #fffbe6; color: #d97706; position: relative; }}
            .badge-no-foto {{ position: absolute; top: -6px; right: -6px; background-color: #ef4444; color: white; border-radius: 50%; width: 14px; height: 14px; font-size: 9px; display: flex; align-items: center; justify-content: center; }}
            
            .badge-location {{ background-color: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; font-weight: 500; padding: 0.4em 0.7em; border-radius: 6px; }}
            .badge-code {{ background-color: #f1f5f9; color: #334155; font-family: monospace; }}
            .badge-user {{ background-color: #f3e8ff; color: #6b21a8; font-weight: 500; border: 1px solid #e9d5ff; }}
            .btn-action {{ width: 32px; height: 32px; padding: 0; display: inline-flex; align-items: center; justify-content: center; border-radius: 6px; }}
            
            /* NUEVO: pre-wrap permite que los saltos de linea se muestren correctamente */
            .obs-text {{ max-width: 150px; white-space: pre-wrap; font-size: 0.85rem; color: #475569; }}
        </style>
    </head>
    <body>
        <nav class="navbar-custom mb-4">
            <div class="container d-flex justify-content-between align-items-center">
                <div class="d-flex align-items-center gap-2">
                    <i class="bi bi-box-seam-fill text-primary fs-4"></i>
                    <h5 class="m-0 fw-bold">Localizador Médico <span class="text-primary">Mantis</span></h5>
                </div>
                
                <div class="d-flex align-items-center gap-3">
                    <div class="d-flex align-items-center gap-2 bg-light px-3 py-1 border rounded-pill">
                        <i class="bi bi-person-circle text-primary"></i>
                        <span class="small fw-bold text-dark">{user['nombre']}</span>
                        {rol_badge}
                    </div>
                    <button class="btn btn-sm btn-outline-danger border-0" onclick="cerrarSesion()" title="Cerrar Sesión">
                        <i class="bi bi-box-arrow-right fs-5"></i>
                    </button>
                </div>
            </div>
        </nav>

        <div class="container mb-5">
            {'<!-- Panel de Carga PDF Oculto para Alistador -->' if es_alistador else '''
            <div class="card-custom p-3 mb-4 bg-white border-primary-subtle">
                <div class="row align-items-center">
                    <div class="col-md-4">
                        <h6 class="fw-bold m-0 text-primary"><i class="bi bi-file-earmark-pdf me-2"></i>Carga Masiva desde PDF</h6>
                        <small class="text-muted">Extrae medicamentos bajo tu sesión activa</small>
                    </div>
                    <div class="col-md-8">
                        <div class="input-group">
                            <input type="file" id="pdfFile" accept=".pdf" class="form-control">
                            <button class="btn btn-outline-primary fw-medium" type="button" onclick="procesarPDF()">
                                <i class="bi bi-magic me-1"></i> Procesar Documento Completo
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            '''}

            <div class="row g-4">
                {'<!-- Formulario Manual Oculto para Alistador -->' if es_alistador else '''
                <div class="col-lg-3">
                    <div class="card-custom p-4">
                        <h6 class="fw-bold mb-3 text-uppercase text-muted small" id="formTitulo"><i class="bi bi-plus-lg me-1"></i> Nuevo Registro Manual</h6>
                        <form id="formMedicamento" enctype="multipart/form-data">
                            <input type="hidden" id="med_id" name="id">
                            
                            <div class="mb-3">
                                <label class="form-label small fw-medium">Código Mantis</label>
                                <input type="text" id="codigo_mantis" name="codigo_mantis" class="form-control" required placeholder="MANTIS-101">
                            </div>
                            <div class="mb-3">
                                <label class="form-label small fw-medium">Nombre del Medicamento</label>
                                <input type="text" id="nombre" name="nombre" class="form-control" required placeholder="Acetaminofén 500mg">
                            </div>
                            <div class="row g-2 mb-3">
                                <div class="col-6">
                                    <label class="form-label small fw-medium">Lote</label>
                                    <input type="text" id="lote" name="lote" class="form-control" required placeholder="L-2026-X">
                                </div>
                                <div class="col-6">
                                    <label class="form-label small fw-medium">Ubicación</label>
                                    <input type="text" id="ubicacion" name="ubicacion" class="form-control" required placeholder="Estante A2">
                                </div>
                            </div>
                            
                            <div class="mb-3">
                                <label class="form-label small fw-medium">Fotografía</label>
                                
                                <input type="file" id="fotoCamara" accept="image/*" capture="environment" style="display: none;" onchange="asignarFoto(this)">
                                <input type="file" id="fotoGaleria" accept="image/*" style="display: none;" onchange="asignarFoto(this)">
                                <input type="file" id="foto" name="foto" style="display: none;">

                                <div class="d-flex gap-2">
                                    <button type="button" class="btn btn-outline-primary w-50 btn-sm" onclick="document.getElementById('fotoCamara').click()">
                                        <i class="bi bi-camera me-1"></i> Cámara
                                    </button>
                                    <button type="button" class="btn btn-outline-secondary w-50 btn-sm" onclick="document.getElementById('fotoGaleria').click()">
                                        <i class="bi bi-image me-1"></i> Galería
                                    </button>
                                </div>
                                <small id="nombreFotoSeleccionada" class="text-success d-block mt-1 fw-bold" style="font-size: 0.75rem;"></small>
                            </div>

                            <div class="mb-4">
                                <label class="form-label small fw-medium">Observación (Manual)</label>
                                <textarea id="observacion" name="observacion" class="form-control" placeholder="Añadir nota o detalle..." rows="2"></textarea>
                            </div>
                            
                            <div class="d-flex gap-2">
                                <button type="submit" class="btn btn-primary-custom w-100" id="btnGuardar">
                                    <i class="bi bi-check2-circle me-1"></i> Guardar
                                </button>
                                <button type="button" class="btn btn-light border d-none" id="btnCancelar" onclick="limpiarFormulario()">
                                    Cancelar
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
                '''}

                <div class="{'col-12' if es_alistador else 'col-lg-9'}">
                    <div class="card-custom p-4">
                        <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
                            <div class="d-flex align-items-center gap-2">
                                <h6 class="fw-bold m-0 text-uppercase text-muted small"><i class="bi bi-list-task me-1"></i> Inventario Registrado</h6>
                                <select id="filtroVistaUsuario" class="form-select form-select-sm py-1" onchange="filtrar()">
                                    <option value="TODOS">Ver: Todos los registros</option>
                                    <option value="MIS_REGISTROS">Ver: Ingresados por mí ({user['nombre']})</option>
                                    <option value="SIN_FOTO">Ver: Pendientes de Foto 📷</option>
                                </select>
                            </div>

                            <div class="position-relative" style="width: 280px;">
                                <i class="bi bi-search position-absolute text-muted" style="left: 12px; top: 9px;"></i>
                                <input type="text" id="buscador" class="form-control ps-5" placeholder="Buscar por Lote, Ubicación, Nombre..." onkeyup="filtrar()">
                            </div>
                        </div>

                        <input type="file" id="inputFotoDirecta" accept="image/*" capture="environment" style="display: none;" onchange="subirFotoSeleccionada()">

                        <div class="table-responsive">
                            <table class="table table-custom align-middle mb-0">
                                <thead>
                                    <tr>
                                        <th>Foto</th>
                                        <th>Código</th>
                                        <th>Medicamento</th>
                                        <th>Lote</th>
                                        <th>Ubicación / Ingreso</th>
                                        <th>Observaciones</th>
                                        {'<th>Acciones</th>' if not es_alistador else ''}
                                    </tr>
                                </thead>
                                <tbody id="tablaCuerpo"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let listaMedicamentos = [];
            let idMedicamentoParaFoto = null;
            const usuarioActual = "{user['nombre']}";
            const esAlistador = {'true' if es_alistador else 'false'};

            function asignarFoto(inputOrigen) {{
                if (inputOrigen.files && inputOrigen.files[0]) {{
                    const inputFinal = document.getElementById('foto');
                    const dataTransfer = new DataTransfer();
                    dataTransfer.items.add(inputOrigen.files[0]);
                    inputFinal.files = dataTransfer.files;

                    document.getElementById('nombreFotoSeleccionada').innerText = `📷 Foto cargada: ${{inputOrigen.files[0].name}}`;
                }}
            }}

            async function cargarMedicamentos() {{
                const res = await fetch('/api/medicamentos');
                listaMedicamentos = await res.json();
                filtrar();
            }}

            async function cerrarSesion() {{
                await fetch('/api/logout', {{ method: 'POST' }});
                window.location.reload();
            }}

            async function procesarPDF() {{
                if (esAlistador) return;
                const input = document.getElementById('pdfFile');
                if (!input.files[0]) {{
                    alert('Por favor selecciona un archivo PDF primero.');
                    return;
                }}

                const formData = new FormData();
                formData.append('archivo_pdf', input.files[0]);

                const res = await fetch('/api/extraer-pdf', {{
                    method: 'POST',
                    body: formData
                }});

                const data = await res.json();
                alert(data.mensaje);
                cargarMedicamentos();
            }}

            function abrirSelectorFoto(id) {{
                if (esAlistador) return;
                idMedicamentoParaFoto = id;
                document.getElementById('inputFotoDirecta').click();
            }}

            async function subirFotoSeleccionada() {{
                if (esAlistador) return;
                const input = document.getElementById('inputFotoDirecta');
                if (!input.files[0] || !idMedicamentoParaFoto) return;

                const formData = new FormData();
                formData.append('foto', input.files[0]);

                await fetch(`/api/medicamentos/${{idMedicamentoParaFoto}}/foto`, {{
                    method: 'POST',
                    body: formData
                }});

                idMedicamentoParaFoto = null;
                input.value = '';
                cargarMedicamentos();
            }}

            // FUNCION MODIFICADA: Ahora solo pide la nota nueva, no muestra la anterior
            async function editarObservacionRapida(id) {{
                const nuevaObs = prompt("Añade una nueva nota (NO se borrará lo anterior):");
                
                if (nuevaObs !== null && nuevaObs.trim() !== "") {{
                    const formData = new FormData();
                    formData.append('observacion', nuevaObs.trim());

                    try {{
                        await fetch(`/api/medicamentos/${{id}}/observacion`, {{
                            method: 'POST',
                            body: formData
                        }});
                        cargarMedicamentos(); // Recarga la tabla
                    }} catch(error) {{
                        alert("Hubo un error al actualizar la observación.");
                    }}
                }}
            }}

            function renderTabla(datos) {{
                let html = '';
                const totalColumnas = esAlistador ? 6 : 7;
                if (datos.length === 0) {{
                    html = `<tr><td colspan="${{totalColumnas}}" class="text-center py-4 text-muted"><i class="bi bi-inbox fs-3 d-block mb-2"></i>No hay registros para mostrar</td></tr>`;
                }} else {{
                    datos.forEach(med => {{
                        let imgTag = '';
                        if (med.foto) {{
                            imgTag = `<a href="${{med.foto}}" target="_blank"><img src="${{med.foto}}" class="img-preview" title="Ver foto"></a>`;
                        }} else {{
                            if (esAlistador) {{
                                imgTag = `<div class="img-preview bg-light d-flex align-items-center justify-content-center text-muted" title="Sin foto"><i class="bi bi-image"></i></div>`;
                            }} else {{
                                imgTag = `<button class="btn btn-cam-pendiente" onclick="abrirSelectorFoto(${{med.id}})" title="¡Falta foto! Clic para tomar foto en vivo">
                                            <i class="bi bi-camera fs-5"></i>
                                            <span class="badge-no-foto">!</span>
                                           </button>`;
                            }}
                        }}

                        let accionesColumna = '';
                        if (!esAlistador) {{
                            accionesColumna = `<td>
                                <div class="d-flex gap-1">
                                    <button class="btn btn-outline-secondary btn-action" onclick="abrirSelectorFoto(${{med.id}})" title="Subir Foto Directa"><i class="bi bi-camera"></i></button>
                                    <button class="btn btn-outline-primary btn-action" onclick="prepararEdicion(${{med.id}})" title="Editar Registro Completo"><i class="bi bi-pencil"></i></button>
                                    <button class="btn btn-outline-danger btn-action" onclick="eliminarRegistro(${{med.id}})" title="Eliminar"><i class="bi bi-trash"></i></button>
                                </div>
                            </td>`;
                        }}
                        
                        // BOTON MODIFICADO (ahora es un ícono de MÁS)
                        const btnObs = `<button class="btn btn-sm text-primary py-0 px-1 border-0" onclick="editarObservacionRapida(${{med.id}})" title="Agregar nueva nota"><i class="bi bi-plus-circle fs-5"></i></button>`;
                        
                        // Texto Modificado
                        const txtModificado = med.usuario_modificacion ? `<div style="font-size: 0.7em; color: #94a3b8; margin-top: 6px; border-top: 1px solid #e2e8f0; padding-top: 4px;"><i class="bi bi-person-check me-1"></i>Última nota por: ${{med.usuario_modificacion}}</div>` : '';
                        
                        let celdaObservacion = `
                            <div class="d-flex flex-column">
                                <div class="d-flex justify-content-between align-items-start">
                                    <span class="obs-text">${{med.observacion || '<span class="text-muted">-</span>'}}</span>
                                    ${{btnObs}}
                                </div>
                                ${{txtModificado}}
                            </div>
                        `;

                        html += `<tr>
                            <td>${{imgTag}}</td>
                            <td><span class="badge badge-code">${{med.codigo_mantis}}</span></td>
                            <td class="fw-medium">${{med.nombre}}</td>
                            <td class="text-muted"><small class="fw-bold">${{med.lote}}</small></td>
                            <td>
                                <span class="badge badge-location d-inline-block mb-1"><i class="bi bi-layers-half me-1"></i>${{med.ubicacion}}</span>
                                <small class="d-block"><span class="badge badge-user"><i class="bi bi-person me-1"></i>${{med.usuario || 'General'}}</span></small>
                            </td>
                            <td>${{celdaObservacion}}</td>
                            ${{accionesColumna}}
                        </tr>`;
                    }});
                }}
                document.getElementById('tablaCuerpo').innerHTML = html;
            }}

            if (!esAlistador) {{
                document.getElementById('formMedicamento').addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const formData = new FormData(document.getElementById('formMedicamento'));

                    await fetch('/medicamentos/manual', {{
                        method: 'POST',
                        body: formData
                    }});
                    limpiarFormulario();
                    cargarMedicamentos();
                }});
            }}

            function prepararEdicion(id) {{
                if (esAlistador) return;
                const med = listaMedicamentos.find(m => m.id === id);
                if (!med) return;

                document.getElementById('med_id').value = med.id;
                document.getElementById('codigo_mantis').value = med.codigo_mantis;
                document.getElementById('nombre').value = med.nombre;
                document.getElementById('lote').value = med.lote;
                document.getElementById('ubicacion').value = med.ubicacion;
                document.getElementById('observacion').value = med.observacion || '';

                document.getElementById('formTitulo').innerHTML = '<i class="bi bi-pencil-square me-1"></i> Editar Registro Completo';
                document.getElementById('btnGuardar').innerHTML = '<i class="bi bi-arrow-repeat me-1"></i> Actualizar';
                document.getElementById('btnCancelar').classList.remove('d-none');
            }}

            function limpiarFormulario() {{
                if (esAlistador) return;
                document.getElementById('formMedicamento').reset();
                document.getElementById('med_id').value = '';
                document.getElementById('nombreFotoSeleccionada').innerText = '';
                document.getElementById('formTitulo').innerHTML = '<i class="bi bi-plus-lg me-1"></i> Nuevo Registro Manual';
                document.getElementById('btnGuardar').innerHTML = '<i class="bi bi-check2-circle me-1"></i> Guardar';
                document.getElementById('btnCancelar').classList.add('d-none');
            }}

            async function eliminarRegistro(id) {{
                if (esAlistador) return;
                if (confirm('¿Estás seguro de que deseas borrar este medicamento?')) {{
                    await fetch(`/api/medicamentos/${{id}}`, {{ method: 'DELETE' }});
                    cargarMedicamentos();
                }}
            }}

            function filtrar() {{
                const modoVista = document.getElementById('filtroVistaUsuario').value;
                const texto = document.getElementById('buscador').value.toLowerCase();

                const filtrados = listaMedicamentos.filter(m => {{
                    let cumpleUsuario = true;
                    if (modoVista === 'MIS_REGISTROS') {{
                        cumpleUsuario = (m.usuario === usuarioActual);
                    }} else if (modoVista === 'SIN_FOTO') {{
                        cumpleUsuario = (!m.foto || m.foto === '');
                    }}

                    let cumpleTexto = m.nombre.toLowerCase().includes(texto) || 
                                      m.codigo_mantis.toLowerCase().includes(texto) ||
                                      m.lote.toLowerCase().includes(texto) ||
                                      m.ubicacion.toLowerCase().includes(texto) ||
                                      (m.observacion && m.observacion.toLowerCase().includes(texto));

                    return cumpleUsuario && cumpleTexto;
                }});

                renderTabla(filtrados);
            }}

            cargarMedicamentos();
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)