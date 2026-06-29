"""
ConnectVPN — Backend FastAPI
Web para venta de cuentas SSH con ADMRufu.
"""
import os
import sys
import uuid
import hashlib
import sqlite3
import logging
import random
import string
import smtplib
import threading
import re

from pathlib import Path
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv("/root/ConnectVPN/.env")
sys.path.insert(0, "/root/ConnectVPN")

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader

from modules.admrufu import crear_cuenta, renovar_cuenta, info_cuenta
from modules.admin import router as admin_router
from chatbot.chatbot import router as chatbot_router

# ─── CONFIG ───────────────────────────────────────────────
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DB_PATH = os.getenv("DB_PATH", "/root/ConnectVPN/connectvpn.db")
TEMPLATES_DIR = Path("/root/ConnectVPN/templates")
SECRET_KEY = os.getenv("SECRET_KEY", "connectvpn-secret-2026")
APP_URL = os.getenv("APP_URL", "https://connect-vpn.top")
SSH_PRECIO = int(os.getenv("SSH_PRECIO", "8000"))
SSH_DIAS = int(os.getenv("SSH_DIAS", "30"))

# Diccionario de planes disponibles: {dias: precio}
PLANES = {
    3: 1500,
    7: 3000,
    15: 5000,
    30: 8000,
}
ADMRUFU_HOST = os.getenv("ADMRUFU_HOST", "149.33.19.164")

# ─── APP ──────────────────────────────────────────────────
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=2592000)
jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

def render(name: str, **ctx) -> HTMLResponse:
    t = jinja_env.get_template(name)
    return HTMLResponse(t.render(**ctx))

# ─── BASE DE DATOS ────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        nombre TEXT DEFAULT '',
        verificado INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS cuentas_ssh (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        usuario_ssh TEXT NOT NULL,
        password_ssh TEXT NOT NULL,
        ip TEXT NOT NULL,
        puerto INTEGER DEFAULT 22,
        dias INTEGER DEFAULT 30,
        expira_at TEXT NOT NULL,
        activa INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS verificaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        codigo TEXT NOT NULL,
        intentos INTEGER DEFAULT 0,
        expires_at TEXT NOT NULL,
        verificado INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS password_resets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        codigo TEXT NOT NULL,
        intentos INTEGER DEFAULT 0,
        expires_at TEXT NOT NULL,
        usado INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS pagos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        payment_id TEXT,
        monto INTEGER DEFAULT 8000,
        estado TEXT DEFAULT 'pending',
        tipo TEXT DEFAULT 'nueva',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

# ─── AUTH ─────────────────────────────────────────────────
def hash_password(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()

def get_user_by_email(email: str) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_user_by_id(user_id: int) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def current_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)

def get_cuenta(user_id: int) -> dict | None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM cuentas_ssh WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    cuenta = dict(row)
    # Calcular días restantes
    expira = datetime.fromisoformat(cuenta["expira_at"])
    dias_restantes = (expira - datetime.now()).days
    cuenta["dias_restantes"] = max(0, dias_restantes)
    cuenta["activa"] = dias_restantes > 0
    return cuenta

# ─── EMAIL ────────────────────────────────────────────────
def generar_codigo() -> str:
    return ''.join(random.choices(string.digits, k=6))

def send_email(to: str, subject: str, html: str, attachments: list = None) -> bool:
    try:
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", "465"))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASSWORD")
        from_name = os.getenv("SMTP_FROM_NAME", "ConnectVPN")

        msg = MIMEMultipart("mixed" if attachments else "alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{smtp_user}>"
        msg["To"] = to
        msg.attach(MIMEText(html, "html"))

        if attachments:
            for filepath in attachments:
                try:
                    with open(filepath, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    filename = os.path.basename(filepath)
                    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
                    msg.attach(part)
                except Exception as e:
                    log.error(f"Error adjuntando {filepath}: {e}")

        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True
    except Exception as e:
        log.error(f"Error email: {e}")
        return False

def send_verification_email(email: str, codigo: str) -> bool:
    html = f"""<html><body style="font-family:Arial;background:#030608;color:#e0f0f5;padding:40px;">
    <div style="max-width:500px;margin:0 auto;background:rgba(8,13,16,0.9);border:1px solid rgba(0,255,157,0.2);border-radius:20px;padding:40px;">
    <h1 style="color:#00ff9d;text-align:center;">🔒 ConnectVPN</h1>
    <p style="text-align:center;color:#5a7a85;">Verificá tu email para activar tu cuenta</p>
    <div style="background:rgba(0,255,157,0.08);border:2px solid #00ff9d;border-radius:12px;padding:20px;text-align:center;margin:20px 0;">
    <p style="color:#5a7a85;font-size:14px;">Tu código de verificación:</p>
    <p style="font-size:36px;font-weight:bold;color:#00f2ff;letter-spacing:8px;margin:20px 0;">{codigo}</p>
    <p style="color:#5a7a85;font-size:12px;">Válido por 10 minutos</p>
    </div></div></body></html>"""
    return send_email(email, "Verificá tu email — ConnectVPN", html)

def send_credenciales_email(email: str, nombre: str, usuario_ssh: str, password_ssh: str, ip: str, puerto: int, dias: int) -> bool:
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background:#030608; font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#030608;">
    <tr>
      <td align="center" style="padding:0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background:#080d10;">

          <!-- HEADER -->
          <tr>
            <td align="center" style="padding:32px 20px 20px;">
              <div style="font-size:40px; line-height:1;">🔒</div>
              <h1 style="margin:10px 0 0; color:#00ff9d; font-size:30px;">ConnectVPN</h1>
              <h2 style="margin:14px 0 0; color:#ffffff; font-size:21px;">¡Tu cuenta está lista, {nombre}!</h2>
            </td>
          </tr>

          <!-- CREDENCIALES -->
          <tr>
            <td style="padding:8px 16px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:rgba(0,242,255,0.06); border:1px solid rgba(0,242,255,0.2); border-radius:12px;">
                <tr>
                  <td style="padding:22px 18px;">
                    <h3 style="color:#00f2ff; margin:0 0 18px; font-size:17px;">🔑 Tus credenciales SSH</h3>

                    <p style="margin:0 0 12px; color:#5a7a85; font-size:13px;">Usuario:</p>
                    <p style="margin:0 0 16px; color:#00ff9d; font-family:'Courier New',monospace; font-size:18px; word-break:break-all;">{usuario_ssh}</p>

                    <p style="margin:0 0 12px; color:#5a7a85; font-size:13px;">Contraseña:</p>
                    <p style="margin:0 0 16px; color:#00ff9d; font-family:'Courier New',monospace; font-size:18px; word-break:break-all;">{password_ssh}</p>

                    <p style="margin:0 0 12px; color:#5a7a85; font-size:13px;">Servidor:</p>
                    <p style="margin:0 0 16px; color:#00f2ff; font-family:'Courier New',monospace; font-size:16px; word-break:break-all;">{ip}</p>

                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                      <tr>
                        <td style="color:#5a7a85; font-size:13px; padding:0 0 6px;">Puerto:</td>
                        <td style="color:#5a7a85; font-size:13px; padding:0 0 6px;">Días:</td>
                      </tr>
                      <tr>
                        <td style="color:#e0f0f5; font-family:'Courier New',monospace; font-size:16px;">{puerto}</td>
                        <td style="color:#e0f0f5; font-size:16px;">{dias} días</td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>
              <p style="text-align:center; color:#5a7a85; font-size:12px; margin:14px 0 0;">
                Podés ver tus credenciales en cualquier momento en tu panel de usuario.
              </p>
            </td>
          </tr>

          <!-- ARCHIVOS ADJUNTOS -->
          <tr>
            <td style="padding:18px 16px 8px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:rgba(0,255,157,0.06); border:1px solid rgba(0,255,157,0.2); border-radius:12px;">
                <tr>
                  <td style="padding:22px 18px;">
                    <h3 style="color:#00ff9d; margin:0 0 14px; font-size:17px;">📎 Archivos adjuntos</h3>
                    <p style="color:#e0f0f5; line-height:1.7; font-size:14px; margin:0;">
                      ¡Gracias por tu compra! 🎉<br>
                      Te adjuntamos 2 archivos SSH listos para usar en HTTP Custom, junto con una guía rápida de configuración.<br><br>
                      <strong style="color:#00ff9d;">📲 PASOS:</strong><br>
                      1️⃣ Descargá cualquiera de los archivos SSH adjuntos<br>
                      2️⃣ Abrí la app HTTP Custom<br>
                      3️⃣ Importá el archivo descargado<br>
                      4️⃣ Donde dice usuario:contraseña, borrá esos datos de ejemplo<br>
                      5️⃣ Colocá el usuario y contraseña que te enviamos por correo<br>
                      6️⃣ Guardá y conectate 🚀<br><br>
                      📖 También incluimos una guía paso a paso para ayudarte.<br>
                      📞 Si tenés dudas, escribime por WhatsApp al +54 9 263 484-1144 entre las 9:00 y las 22:00 hs.<br><br>
                      — Charly
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- AVISO PERSONAL + DATOS MANUALES -->
          <tr>
            <td style="padding:10px 16px 24px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:rgba(255,180,0,0.08); border:1px solid rgba(255,180,0,0.35); border-radius:12px;">
                <tr>
                  <td style="padding:20px 18px;">
                    <h3 style="color:#ffb400; margin:0 0 12px; font-size:16px;">⚠️ Importante sobre tu compañía</h3>
                    <p style="color:#e0f0f5; line-height:1.7; font-size:14px; margin:0 0 16px;">
                      Los archivos adjuntos (.hc) están configurados exclusivamente para <strong style="color:#ffb400;">línea Personal</strong>. 📱<br><br>
                      Si usás otra compañía (Movistar, Claro, etc.), estos archivos podrían no funcionar. Pero si sabés configurar tu propia conexión, abajo te dejamos todos los datos. 🛠️
                    </p>

                    <div style="background:rgba(0,0,0,0.25); border:1px solid rgba(255,180,0,0.2); border-radius:10px; padding:16px 16px;">
                      <p style="margin:0 0 12px; color:#ffb400; font-size:13px; font-weight:bold;">🔧 Datos para configuración manual:</p>

                      <p style="margin:0 0 4px; color:#5a7a85; font-size:12px;">Servidor SSH:</p>
                      <p style="margin:0 0 12px; color:#00f2ff; font-family:'Courier New',monospace; font-size:15px; word-break:break-all;">{ip}</p>

                      <p style="margin:0 0 4px; color:#5a7a85; font-size:12px;">Puerto SSH:</p>
                      <p style="margin:0 0 12px; color:#e0f0f5; font-family:'Courier New',monospace; font-size:15px;">{puerto}</p>

                      <p style="margin:0 0 4px; color:#5a7a85; font-size:12px;">Puerto WS (websocket):</p>
                      <p style="margin:0 0 12px; color:#e0f0f5; font-family:'Courier New',monospace; font-size:15px;">80</p>

                      <p style="margin:0 0 4px; color:#5a7a85; font-size:12px;">Dominio (SNI / Host Cloudflare):</p>
                      <p style="margin:0; color:#00ff9d; font-family:'Courier New',monospace; font-size:15px; word-break:break-all;">custom.agro-bot.top</p>
                    </div>

                    <p style="color:#e0f0f5; line-height:1.7; font-size:14px; margin:16px 0 0;">
                      Cualquier duda, escribinos por WhatsApp y te orientamos. 🙂
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td align="center" style="padding:16px 20px 30px;">
              <p style="margin:0; color:#5a7a85; font-size:12px;">© 2026 ConnectVPN · connect-vpn.top</p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
    # ────────── ARCHIVOS A ADJUNTAR ──────────
    DOCS_DIR = "/root/ConnectVPN/documentos"
    ARCHIVO_1 = "SSH PREMIUM CLOUDFRONT.hc"
    ARCHIVO_2 = "SSH PREMIUM CLOUDFLARE.hc"
    adjuntos = [
        os.path.join(DOCS_DIR, ARCHIVO_1),
        os.path.join(DOCS_DIR, ARCHIVO_2),
    ]
    # ─────────────────────────────────────────
    return send_email(email, "🔒 Tus credenciales SSH — ConnectVPN", html, attachments=adjuntos)

# ─── ALERTAS TELEGRAM ─────────────────────────────────────
import requests as req_tg

def send_alert(msg: str, nivel: str = "info"):
    token = os.getenv("ALERT_BOT_TOKEN")
    chat_id = os.getenv("ALERT_CHAT_ID")
    if not token or not chat_id:
        return
    iconos = {"info": "ℹ️", "success": "✅", "error": "🔴", "warning": "⚠️"}
    hora = datetime.now().strftime("%H:%M:%S")
    texto = f"{iconos.get(nivel,'📌')} *ConnectVPN Alert*\n`{hora}`\n\n{msg}"
    try:
        req_tg.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"}, timeout=10)
    except:
        pass

# ─── MERCADOPAGO ──────────────────────────────────────────
import mercadopago

def get_mp_sdk():
    return mercadopago.SDK(os.getenv("MP_ACCESS_TOKEN", ""))

def crear_preferencia_mp(user_id: int, email: str, tipo: str = "nueva", dias: int = 30) -> dict:
    sdk = get_mp_sdk()
    precio = PLANES.get(dias, 8000)
    preference_data = {
        "items": [{
            "title": f"ConnectVPN — Cuenta SSH {dias} días",
            "quantity": 1,
            "currency_id": "ARS",
            "unit_price": float(precio)
        }],
        "payer": {"email": email},
        "back_urls": {
            "success": "https://connect-vpn.top/pago/exitoso",
            "failure": "https://connect-vpn.top/pago/fallido",
            "pending": "https://connect-vpn.top/pago/pendiente"
        },
        "auto_return": "approved",
        "notification_url": "https://connect-vpn.top/mp/webhook",
        "external_reference": f"{user_id}|{tipo}|{dias}",
        "statement_descriptor": "ConnectVPN"
    }
    result = sdk.preference().create(preference_data)
    if result["status"] == 201:
        return {"success": True, "init_point": result["response"]["init_point"]}
    return {"success": False, "error": str(result)}

def generar_nombre_ssh(nombre: str) -> str:
    """Genera un nombre de usuario SSH único basado en el nombre."""
    clean = re.sub(r'[^a-zA-Z0-9]', '', nombre.lower())[:8]
    suffix = ''.join(random.choices(string.digits, k=4))
    return f"{clean}{suffix}" if clean else f"cvpn{suffix}"

# ─── RUTAS PÚBLICAS ───────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    ref = request.query_params.get("ref", "").strip()
    if ref:
        request.session["ref_afiliado"] = ref
    user = current_user(request)
    if user:
        return RedirectResponse("/dashboard")
    return render("landing.html")

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard")
    msg = request.session.pop("login_success", None)
    return render("login.html", success=msg)

@app.post("/login")
async def login_post(request: Request):
    data = await request.form()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()

    user = get_user_by_email(email)
    if not user or user["password"] != hash_password(password):
        return render("login.html", error="Email o contraseña incorrectos")
    if not user["verificado"]:
        return render("login.html", error="Debés verificar tu email primero")

    request.session["user_id"] = user["id"]
    return RedirectResponse("/dashboard", status_code=302)

@app.get("/register", response_class=HTMLResponse)
async def register_get(request: Request):
    ref = request.query_params.get("ref", "").strip()
    if ref:
        request.session["ref_afiliado"] = ref
    if current_user(request):
        return RedirectResponse("/dashboard")
    return render("register.html")

@app.post("/register")
async def register_post(request: Request):
    data = await request.form()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    nombre = data.get("nombre", "").strip()

    if not email or not password or len(password) < 6:
        return render("register.html", error="Email o contraseña inválidos")
    if get_user_by_email(email):
        return render("register.html", error="Ese email ya está registrado")

    try:
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM verificaciones WHERE email = ?", (email,))
        cur.execute("INSERT INTO verificaciones (email, codigo, expires_at) VALUES (?, ?, ?)",
                    (email, codigo, expires_at))
        conn.commit()
        conn.close()

        if send_verification_email(email, codigo):
            request.session["registro_temporal"] = {"email": email, "password": password, "nombre": nombre, "ref": request.session.get("ref_afiliado", "")}
            return RedirectResponse("/verificar", status_code=303)
        return render("register.html", error="Error al enviar email. Intentá de nuevo.")
    except Exception as e:
        return render("register.html", error=str(e))

@app.get("/verificar", response_class=HTMLResponse)
async def verificar_get(request: Request):
    if "registro_temporal" not in request.session:
        return RedirectResponse("/register")
    return render("verificar.html")

@app.post("/verificar")
async def verificar_post(request: Request):
    data = await request.form()
    codigo_ingresado = data.get("codigo", "").strip()
    if "registro_temporal" not in request.session:
        return RedirectResponse("/register")

    temp = request.session["registro_temporal"]
    email = temp["email"]

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT codigo, expires_at, intentos FROM verificaciones WHERE email = ? AND verificado = 0 ORDER BY created_at DESC LIMIT 1", (email,))
        row = cur.fetchone()

        if not row:
            conn.close()
            return render("verificar.html", error="Código expirado. Registrate de nuevo.")

        codigo_guardado, expires_at, intentos = row

        if datetime.fromisoformat(expires_at) < datetime.now():
            conn.close()
            return render("verificar.html", error="Código expirado. Solicitá uno nuevo.")

        if intentos >= 3:
            conn.close()
            return render("verificar.html", error="Demasiados intentos. Solicitá un código nuevo.")

        if codigo_ingresado != codigo_guardado:
            cur.execute("UPDATE verificaciones SET intentos = intentos + 1 WHERE email = ?", (email,))
            conn.commit()
            conn.close()
            return render("verificar.html", error=f"Código incorrecto. {3-(intentos+1)} intentos restantes.")

        ref_codigo = temp.get("ref", "")
        referido_por = None
        if ref_codigo:
            fila_af = cur.execute("SELECT id FROM usuarios WHERE codigo_afiliado = ?", (ref_codigo,)).fetchone()
            if fila_af:
                referido_por = fila_af["id"]
        nuevo_codigo_af = generar_codigo_afiliado()
        cur.execute("INSERT INTO usuarios (email, password, nombre, verificado, referido_por, codigo_afiliado) VALUES (?, ?, ?, 1, ?, ?)",
                    (email, hash_password(temp["password"]), temp["nombre"], referido_por, nuevo_codigo_af))
        user_id = cur.lastrowid
        cur.execute("UPDATE verificaciones SET verificado = 1 WHERE email = ?", (email,))
        conn.commit()
        conn.close()

        del request.session["registro_temporal"]
        request.session["user_id"] = user_id
        send_alert(f"👤 Nuevo usuario registrado\nEmail: `{email}`\nNombre: {temp['nombre']}", "success")
        return RedirectResponse("/planes", status_code=303)
    except Exception as e:
        return render("verificar.html", error=str(e))

@app.post("/reenviar-codigo")
async def reenviar_codigo(request: Request):
    if "registro_temporal" not in request.session:
        return RedirectResponse("/register")
    email = request.session["registro_temporal"]["email"]
    try:
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM verificaciones WHERE email = ? AND verificado = 0", (email,))
        cur.execute("INSERT INTO verificaciones (email, codigo, expires_at) VALUES (?, ?, ?)", (email, codigo, expires_at))
        conn.commit()
        conn.close()
        if send_verification_email(email, codigo):
            return render("verificar.html", success="Código reenviado. Revisá tu email.")
        return render("verificar.html", error="Error al reenviar.")
    except Exception as e:
        return render("verificar.html", error=str(e))

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

@app.get("/planes", response_class=HTMLResponse)
async def planes(request: Request):
    user = current_user(request)
    cuenta = get_cuenta(user["id"]) if user else None
    return render("planes.html", user=user, cuenta=cuenta, planes=PLANES)

@app.get("/privacidad", response_class=HTMLResponse)
async def privacidad(request: Request):
    return render("privacidad.html", user=current_user(request))

# ─── RESET CONTRASEÑA ─────────────────────────────────────
@app.get("/reset", response_class=HTMLResponse)
async def reset_get(request: Request):
    return render("reset.html")

@app.post("/reset")
async def reset_post(request: Request):
    data = await request.form()
    email = data.get("email", "").strip()
    user = get_user_by_email(email)
    if not user:
        return render("reset.html", error="No existe una cuenta con ese email.")
    try:
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM password_resets WHERE email = ?", (email,))
        cur.execute("INSERT INTO password_resets (email, codigo, expires_at) VALUES (?, ?, ?)", (email, codigo, expires_at))
        conn.commit()
        conn.close()
        if send_verification_email(email, codigo):
            request.session["reset_email"] = email
            return RedirectResponse("/reset/verificar", status_code=303)
        return render("reset.html", error="Error al enviar email.")
    except Exception as e:
        return render("reset.html", error=str(e))

@app.get("/reset/verificar", response_class=HTMLResponse)
async def reset_verificar_get(request: Request):
    if "reset_email" not in request.session:
        return RedirectResponse("/reset")
    return render("reset_verificar.html")

@app.post("/reset/verificar")
async def reset_verificar_post(request: Request):
    if "reset_email" not in request.session:
        return RedirectResponse("/reset")
    data = await request.form()
    codigo = data.get("codigo", "").strip()
    password = data.get("password", "").strip()
    password2 = data.get("password2", "").strip()
    email = request.session["reset_email"]

    if len(password) < 6:
        return render("reset_verificar.html", error="Mínimo 6 caracteres.")
    if password != password2:
        return render("reset_verificar.html", error="Las contraseñas no coinciden.")

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT codigo, expires_at, intentos FROM password_resets WHERE email = ? AND usado = 0 ORDER BY created_at DESC LIMIT 1", (email,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return render("reset_verificar.html", error="Código expirado.")
        codigo_guardado, expires_at, intentos = row
        if datetime.fromisoformat(expires_at) < datetime.now():
            conn.close()
            return render("reset_verificar.html", error="Código expirado.")
        if intentos >= 3:
            conn.close()
            return render("reset_verificar.html", error="Demasiados intentos.")
        if codigo != codigo_guardado:
            cur.execute("UPDATE password_resets SET intentos = intentos + 1 WHERE email = ?", (email,))
            conn.commit()
            conn.close()
            return render("reset_verificar.html", error=f"Código incorrecto. {3-(intentos+1)} intentos.")
        cur.execute("UPDATE usuarios SET password = ? WHERE email = ?", (hash_password(password), email))
        cur.execute("UPDATE password_resets SET usado = 1 WHERE email = ?", (email,))
        conn.commit()
        conn.close()
        del request.session["reset_email"]
        request.session["login_success"] = "¡Contraseña actualizada! Ya podés iniciar sesión."
        return RedirectResponse("/login", status_code=303)
    except Exception as e:
        return render("reset_verificar.html", error=str(e))

@app.post("/reset/reenviar")
async def reset_reenviar(request: Request):
    if "reset_email" not in request.session:
        return RedirectResponse("/reset")
    email = request.session["reset_email"]
    try:
        codigo = generar_codigo()
        expires_at = (datetime.now() + timedelta(minutes=10)).isoformat()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM password_resets WHERE email = ?", (email,))
        cur.execute("INSERT INTO password_resets (email, codigo, expires_at) VALUES (?, ?, ?)", (email, codigo, expires_at))
        conn.commit()
        conn.close()
        if send_verification_email(email, codigo):
            return render("reset_verificar.html", success="Código reenviado.")
        return render("reset_verificar.html", error="Error al reenviar.")
    except Exception as e:
        return render("reset_verificar.html", error=str(e))

# ─── DASHBOARD ────────────────────────────────────────────
@app.get("/afiliados", response_class=HTMLResponse)
async def afiliados(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login")
    conn = get_db()
    cur = conn.cursor()
    codigo_af = user.get("codigo_afiliado") or ""
    total_ref = cur.execute("SELECT COUNT(*) FROM usuarios WHERE referido_por = ?", (user["id"],)).fetchone()[0]
    compraron = cur.execute("SELECT COUNT(DISTINCT referido_id) FROM afiliados_ventas WHERE afiliado_id = ?", (user["id"],)).fetchone()[0]
    dias_ganados = cur.execute("SELECT COALESCE(SUM(dias_comision),0) FROM afiliados_ventas WHERE afiliado_id = ?", (user["id"],)).fetchone()[0]
    conn.close()
    link = f"https://connect-vpn.top/?ref={codigo_af}"
    stats = {"total_referidos": total_ref, "compraron": compraron, "dias_ganados": dias_ganados}
    return render("afiliados.html", user=user, link=link, stats=stats)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login")
    cuenta = get_cuenta(user["id"])
    if not cuenta:
        return RedirectResponse("/planes")
    return render("dashboard.html", user=user, cuenta=cuenta)

# ─── PAGOS ────────────────────────────────────────────────
@app.get("/pago/iniciar")
async def pago_iniciar(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login")
    try:
        dias = int(request.query_params.get("dias", "30"))
    except:
        dias = 30
    if dias not in PLANES:
        dias = 30
    cuenta = get_cuenta(user["id"])
    tipo = "renovacion" if cuenta else "nueva"
    result = crear_preferencia_mp(user["id"], user["email"], tipo, dias)
    if result["success"]:
        return RedirectResponse(result["init_point"])
    return render("planes.html", user=user, error="Error al iniciar el pago.")

@app.get("/pago/exitoso", response_class=HTMLResponse)
async def pago_exitoso(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login")
    payment_id = request.query_params.get("payment_id", "")
    status = request.query_params.get("status", "")
    # Leer días del external_reference via la API de MP
    dias_plan = 30
    if payment_id:
        try:
            sdk = get_mp_sdk()
            info = sdk.payment().get(payment_id)
            if info["status"] == 200:
                ext_ref = info["response"].get("external_reference", "")
                parts = ext_ref.split("|")
                if len(parts) >= 3:
                    dias_plan = int(parts[2])
                if dias_plan not in PLANES:
                    dias_plan = 30
        except:
            dias_plan = 30
    if status == "approved":
        if pago_ya_procesado(payment_id):
            cuenta_actualizada = get_cuenta(user["id"])
            return render("pago_exitoso.html", user=user, cuenta=cuenta_actualizada, dias_plan=dias_plan, precio_plan=PLANES.get(dias_plan, 8000))
        cuenta = get_cuenta(user["id"])
        if cuenta:
            # Renovar
            # OPCIÓN A: si la cuenta está activa, sumar a la fecha actual de vencimiento
            # Si está vencida, partir desde hoy
            try:
                expira_actual = datetime.fromisoformat(cuenta["expira_at"])
                if expira_actual > datetime.now():
                    # Cuenta activa: sumar días a la fecha de vencimiento
                    base = expira_actual
                else:
                    # Cuenta vencida: partir desde hoy
                    base = datetime.now()
            except:
                base = datetime.now()
            nueva_expira_dt = base + timedelta(days=dias_plan)
            nueva_expira = nueva_expira_dt.isoformat()
            # Días totales desde hoy hasta el nuevo vencimiento (para sincronizar con SSH)
            dias_totales_ssh = max(1, (nueva_expira_dt - datetime.now()).days + 1)
            resultado = renovar_cuenta(cuenta["usuario_ssh"], dias_totales_ssh)
            if resultado["success"]:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("UPDATE cuentas_ssh SET expira_at = ?, activa = 1, dias = ? WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                            (nueva_expira, dias_plan, user["id"]))
                cur.execute("INSERT INTO pagos (user_id, payment_id, estado, tipo, monto) VALUES (?, ?, 'approved', 'renovacion', ?)",
                            (user["id"], payment_id, PLANES.get(dias_plan, 8000)))
                conn.commit()
                conn.close()
                send_alert(f"💰 Renovación pagada\nEmail: `{user['email']}`\nUsuario SSH: `{cuenta['usuario_ssh']}`", "success")
        else:
            # Cuenta nueva
            nombre_ssh = generar_nombre_ssh(user["nombre"] or user["email"].split("@")[0])
            resultado = crear_cuenta(nombre_ssh, dias_plan)
            if resultado["success"]:
                expira_at = (datetime.now() + timedelta(days=dias_plan)).isoformat()
                conn = get_db()
                cur = conn.cursor()
                cur.execute("""INSERT INTO cuentas_ssh (user_id, usuario_ssh, password_ssh, ip, puerto, dias, expira_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (user["id"], resultado["user"], resultado["password"],
                             ADMRUFU_HOST, 22, dias_plan, expira_at))
                cur.execute("INSERT INTO pagos (user_id, payment_id, estado, tipo, monto) VALUES (?, ?, 'approved', 'nueva', ?)",
                            (user["id"], payment_id, PLANES.get(dias_plan, 8000)))
                conn.commit()
                conn.close()
                # Enviar credenciales por email
                send_credenciales_email(
                    user["email"], user["nombre"] or "usuario",
                    resultado["user"], resultado["password"],
                    ADMRUFU_HOST, 22, dias_plan
                )
                send_alert(f"💰 Nueva cuenta vendida ({dias_plan} días)\nEmail: `{user['email']}`\nUsuario SSH: `{resultado['user']}`", "success")

    cuenta_actualizada = get_cuenta(user["id"])
    return render("pago_exitoso.html", user=user, cuenta=cuenta_actualizada, dias_plan=dias_plan, precio_plan=PLANES.get(dias_plan, 8000))

@app.get("/pago/fallido", response_class=HTMLResponse)
async def pago_fallido(request: Request):
    user = current_user(request)
    return render("planes.html", user=user, error="El pago no se completó. Intentá de nuevo.")

@app.get("/pago/pendiente", response_class=HTMLResponse)
async def pago_pendiente(request: Request):
    user = current_user(request)
    return render("planes.html", user=user, error="Tu pago está pendiente de acreditación.")



def generar_codigo_afiliado() -> str:
    """Genera un codigo de afiliado unico de 8 caracteres."""
    import secrets, string as _s
    alfabeto = (_s.ascii_uppercase + _s.digits).replace("O","").replace("0","").replace("I","").replace("1","")
    conn = get_db(); cur = conn.cursor()
    while True:
        cod = "".join(secrets.choice(alfabeto) for _ in range(8))
        if not cur.execute("SELECT 1 FROM usuarios WHERE codigo_afiliado = ?", (cod,)).fetchone():
            conn.close()
            return cod


def pago_ya_procesado(payment_id: str) -> bool:
    """Devuelve True si este payment_id ya fue registrado (evita doble procesamiento)."""
    if not payment_id:
        return False
    conn = get_db()
    cur = conn.cursor()
    existe = cur.execute("SELECT 1 FROM pagos WHERE payment_id = ?", (str(payment_id),)).fetchone()
    conn.close()
    return existe is not None

# ─── COMISIÓN DE REFERIDOS ────────────────────────────────
COMISION_DIAS = {3: 1, 7: 2, 15: 4, 30: 8}
TOPE_COMISION_MENSUAL = 30

def acreditar_comision_referido(referido_id: int, dias_plan: int, payment_id: str):
    """Acredita días de comisión al afiliado que trajo a este referido.
    Nunca rompe el pago: cualquier error se atrapa y se loguea."""
    try:
        if dias_plan not in COMISION_DIAS:
            return
        conn = get_db()
        cur = conn.cursor()
        ref = cur.execute("SELECT referido_por FROM usuarios WHERE id = ?", (referido_id,)).fetchone()
        if not ref or not ref["referido_por"]:
            conn.close()
            return
        afiliado_id = ref["referido_por"]
        ya = cur.execute("SELECT 1 FROM afiliados_ventas WHERE payment_id = ?", (payment_id,)).fetchone()
        if ya:
            conn.close()
            return
        dias_comision = COMISION_DIAS[dias_plan]
        mes_actual = datetime.now().strftime("%Y-%m")
        usado = cur.execute(
            "SELECT COALESCE(SUM(dias_comision),0) FROM afiliados_ventas WHERE afiliado_id = ? AND substr(created_at,1,7) = ?",
            (afiliado_id, mes_actual)
        ).fetchone()[0]
        if usado + dias_comision > TOPE_COMISION_MENSUAL:
            cur.execute("INSERT INTO fraude_alertas (tipo,detalle,afiliado_id,referido_id) VALUES ('tope_mensual',?,?,?)",
                        (f"Tope: {usado}+{dias_comision}", afiliado_id, referido_id))
            conn.commit(); conn.close()
            send_alert(f"⚠️ Afiliado id {afiliado_id} alcanzó el tope mensual ({usado} días). Comisión de {dias_comision} días NO acreditada.", "warning")
            return
        cuenta = cur.execute("SELECT * FROM cuentas_ssh WHERE user_id = ? ORDER BY id DESC LIMIT 1", (afiliado_id,)).fetchone()
        if cuenta:
            try:
                expira_actual = datetime.fromisoformat(cuenta["expira_at"])
                base = expira_actual if expira_actual > datetime.now() else datetime.now()
            except:
                base = datetime.now()
            nueva_expira_dt = base + timedelta(days=dias_comision)
            nueva_expira = nueva_expira_dt.isoformat()
            dias_totales_ssh = max(1, (nueva_expira_dt - datetime.now()).days + 1)
            res = renovar_cuenta(cuenta["usuario_ssh"], dias_totales_ssh)
            if res.get("success"):
                cur.execute("UPDATE cuentas_ssh SET expira_at = ?, activa = 1 WHERE user_id = ?", (nueva_expira, afiliado_id))
                cur.execute("INSERT INTO afiliados_ventas (afiliado_id,referido_id,payment_id,dias_plan,dias_comision) VALUES (?,?,?,?,?)",
                            (afiliado_id, referido_id, payment_id, dias_plan, dias_comision))
                conn.commit()
                send_alert(f"🎁 Comisión acreditada: afiliado id {afiliado_id} +{dias_comision} días (vence {nueva_expira[:10]})", "success")
            else:
                send_alert(f"⚠️ Falló renovar_cuenta para comisión del afiliado id {afiliado_id}. Revisar manual.", "warning")
        else:
            cur.execute("INSERT INTO afiliados_ventas (afiliado_id,referido_id,payment_id,dias_plan,dias_comision) VALUES (?,?,?,?,?)",
                        (afiliado_id, referido_id, payment_id, dias_plan, dias_comision))
            conn.commit()
            send_alert(f"⚠️ Afiliado id {afiliado_id} ganó {dias_comision} días pero NO tiene cuenta SSH. Resolver manual.", "warning")
        conn.close()
    except Exception as e:
        try:
            send_alert(f"🔴 Error en comisión de referido (no afectó el pago): {e}", "error")
        except:
            pass

@app.post("/mp/webhook")
async def mp_webhook(request: Request):
    try:
        data = await request.json()
        topic = data.get("type") or request.query_params.get("topic")
        if topic == "payment":
            payment_id = str(data.get("data", {}).get("id") or request.query_params.get("id"))
            sdk = get_mp_sdk()
            info = sdk.payment().get(payment_id)
            if info["status"] == 200 and info["response"].get("status") == "approved":
                if pago_ya_procesado(payment_id):
                    return JSONResponse({"status": "ok", "info": "ya procesado"})
                ext_ref = info["response"].get("external_reference", "")
                parts = ext_ref.split("|")
                user_id = int(parts[0]) if parts else 0
                tipo = parts[1] if len(parts) > 1 else "nueva"
                try:
                    dias_plan = int(parts[2]) if len(parts) > 2 else 30
                except:
                    dias_plan = 30
                if dias_plan not in PLANES:
                    dias_plan = 30
                if user_id:
                    user = get_user_by_id(user_id)
                    if user:
                        cuenta = get_cuenta(user_id)
                        if tipo == "renovacion" and cuenta:
                            # OPCIÓN A: sumar días si la cuenta está activa
                            try:
                                expira_actual = datetime.fromisoformat(cuenta["expira_at"])
                                base = expira_actual if expira_actual > datetime.now() else datetime.now()
                            except:
                                base = datetime.now()
                            nueva_expira_dt = base + timedelta(days=dias_plan)
                            nueva_expira = nueva_expira_dt.isoformat()
                            # Sincronizar con SSH (días totales desde hoy)
                            dias_totales_ssh = max(1, (nueva_expira_dt - datetime.now()).days + 1)
                            renovar_cuenta(cuenta["usuario_ssh"], dias_totales_ssh)
                            conn = get_db()
                            cur = conn.cursor()
                            cur.execute("UPDATE cuentas_ssh SET expira_at = ?, activa = 1, dias = ? WHERE user_id = ?", (nueva_expira, dias_plan, user_id))
                            cur.execute("INSERT INTO pagos (user_id, payment_id, estado, tipo, monto) VALUES (?, ?, 'approved', 'renovacion', ?)", (user_id, payment_id, PLANES.get(dias_plan, 8000)))
                            conn.commit()
                            conn.close()
                            acreditar_comision_referido(user_id, dias_plan, payment_id)
                        elif not cuenta:
                            nombre_ssh = generar_nombre_ssh(user["nombre"] or user["email"].split("@")[0])
                            resultado = crear_cuenta(nombre_ssh, dias_plan)
                            if resultado["success"]:
                                expira_at = (datetime.now() + timedelta(days=dias_plan)).isoformat()
                                conn = get_db()
                                cur = conn.cursor()
                                cur.execute("""INSERT INTO cuentas_ssh (user_id, usuario_ssh, password_ssh, ip, puerto, dias, expira_at)
                                              VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                            (user_id, resultado["user"], resultado["password"], ADMRUFU_HOST, 22, dias_plan, expira_at))
                                cur.execute("INSERT INTO pagos (user_id, payment_id, estado, tipo, monto) VALUES (?, ?, 'approved', 'nueva', ?)", (user_id, payment_id, PLANES.get(dias_plan, 8000)))
                                conn.commit()
                                conn.close()
                                acreditar_comision_referido(user_id, dias_plan, payment_id)
                                send_credenciales_email(user["email"], user["nombre"] or "usuario",
                                    resultado["user"], resultado["password"], ADMRUFU_HOST, 22, dias_plan)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        log.error(f"Error webhook: {e}")
        return JSONResponse({"status": "error"}, status_code=500)

# Panel Admin
app.include_router(admin_router)
app.include_router(chatbot_router)

# ─── MAIN ─────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    send_alert(f"Servidor iniciado\nURL: {APP_URL}", "success")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8001")))
