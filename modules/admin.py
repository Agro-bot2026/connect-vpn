"""
Módulo Panel Admin — ConnectVPN
Acceso: /admin (requiere login con ADMIN_USER y ADMIN_PASSWORD del .env)
"""
import os
import sqlite3
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv("/root/ConnectVPN/.env")
log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="/root/ConnectVPN/templates")

DB_PATH = "/root/ConnectVPN/connectvpn.db"
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def is_admin(request: Request) -> bool:
    return request.session.get("admin_logged") is True


def require_admin(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    return None


# ─── LOGIN / LOGOUT ──────────────────────────────────────
@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, error: str = ""):
    if is_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "admin/login.html", {"error": error})


@router.post("/login")
async def admin_login(request: Request, user: str = Form(...), password: str = Form(...)):
    if user == ADMIN_USER and password == ADMIN_PASSWORD and ADMIN_PASSWORD:
        request.session["admin_logged"] = True
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "admin/login.html",
        {"error": "Usuario o contraseña incorrectos"})


@router.get("/logout")
async def admin_logout(request: Request):
    request.session.pop("admin_logged", None)
    return RedirectResponse("/admin/login", status_code=303)


# ─── DASHBOARD ───────────────────────────────────────────
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    redirect = require_admin(request)
    if redirect: return redirect

    conn = get_db()
    cur = conn.cursor()

    hoy_inicio = datetime.now().strftime("%Y-%m-%d 00:00:00")
    mes_inicio = datetime.now().strftime("%Y-%m-01 00:00:00")

    stats = {
        "usuarios_total": cur.execute("SELECT COUNT(*) FROM usuarios WHERE verificado=1").fetchone()[0],
        "ventas_hoy": cur.execute("SELECT COUNT(*) FROM pagos WHERE estado='approved' AND created_at >= ?", (hoy_inicio,)).fetchone()[0],
        "ingresos_hoy": cur.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE estado='approved' AND created_at >= ?", (hoy_inicio,)).fetchone()[0],
        "ventas_mes": cur.execute("SELECT COUNT(*) FROM pagos WHERE estado='approved' AND created_at >= ?", (mes_inicio,)).fetchone()[0],
        "ingresos_mes": cur.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE estado='approved' AND created_at >= ?", (mes_inicio,)).fetchone()[0],
        "cuentas_activas": cur.execute("SELECT COUNT(*) FROM cuentas_ssh WHERE activa=1 AND expira_at > ?", (datetime.now().isoformat(),)).fetchone()[0],
        "cuentas_vencidas": cur.execute("SELECT COUNT(*) FROM cuentas_ssh WHERE expira_at <= ?", (datetime.now().isoformat(),)).fetchone()[0],
        "ingresos_total": cur.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE estado='approved'").fetchone()[0],
    }

    # Últimas 5 ventas
    ultimas_ventas = cur.execute("""
        SELECT p.id, p.monto, p.tipo, p.created_at, u.email
        FROM pagos p JOIN usuarios u ON u.id = p.user_id
        WHERE p.estado='approved'
        ORDER BY p.id DESC LIMIT 5
    """).fetchall()

    # Cuentas próximas a vencer (3 días)
    limite = (datetime.now() + timedelta(days=3)).isoformat()
    proximas_vencer = cur.execute("""
        SELECT c.usuario_ssh, c.expira_at, u.email
        FROM cuentas_ssh c JOIN usuarios u ON u.id = c.user_id
        WHERE c.activa=1 AND c.expira_at <= ? AND c.expira_at > ?
        ORDER BY c.expira_at ASC LIMIT 10
    """, (limite, datetime.now().isoformat())).fetchall()

    conn.close()

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "stats": stats,
        "ultimas_ventas": [dict(r) for r in ultimas_ventas],
        "proximas_vencer": [dict(r) for r in proximas_vencer],
    })


# ─── CLIENTES ────────────────────────────────────────────
@router.get("/clientes", response_class=HTMLResponse)
async def admin_clientes(request: Request, q: str = ""):
    redirect = require_admin(request)
    if redirect: return redirect

    conn = get_db()
    cur = conn.cursor()

    if q:
        query = """SELECT id, email, nombre, verificado, created_at FROM usuarios
                   WHERE email LIKE ? OR nombre LIKE ? ORDER BY id DESC"""
        clientes = cur.execute(query, (f"%{q}%", f"%{q}%")).fetchall()
    else:
        clientes = cur.execute("SELECT id, email, nombre, verificado, created_at FROM usuarios ORDER BY id DESC").fetchall()

    conn.close()
    return templates.TemplateResponse(request, "admin/clientes.html", {
        "clientes": [dict(r) for r in clientes],
        "q": q,
    })


# ─── PAGOS ───────────────────────────────────────────────
@router.get("/pagos", response_class=HTMLResponse)
async def admin_pagos(request: Request):
    redirect = require_admin(request)
    if redirect: return redirect

    conn = get_db()
    cur = conn.cursor()
    pagos = cur.execute("""
        SELECT p.id, p.payment_id, p.monto, p.tipo, p.estado, p.created_at, u.email
        FROM pagos p JOIN usuarios u ON u.id = p.user_id
        ORDER BY p.id DESC LIMIT 200
    """).fetchall()
    conn.close()

    return templates.TemplateResponse(request, "admin/pagos.html", {
        "pagos": [dict(r) for r in pagos],
    })


# ─── CUENTAS SSH ─────────────────────────────────────────
@router.get("/cuentas", response_class=HTMLResponse)
async def admin_cuentas(request: Request):
    redirect = require_admin(request)
    if redirect: return redirect

    conn = get_db()
    cur = conn.cursor()
    cuentas = cur.execute("""
        SELECT c.id, c.usuario_ssh, c.ip, c.puerto, c.dias, c.expira_at, c.activa, u.email
        FROM cuentas_ssh c JOIN usuarios u ON u.id = c.user_id
        ORDER BY c.id DESC
    """).fetchall()
    conn.close()

    cuentas_list = []
    ahora = datetime.now()
    for c in cuentas:
        c = dict(c)
        try:
            expira = datetime.fromisoformat(c["expira_at"])
            c["dias_restantes"] = (expira - ahora).days
            c["vencida"] = c["dias_restantes"] < 0
        except:
            c["dias_restantes"] = 0
            c["vencida"] = True
        cuentas_list.append(c)

    return templates.TemplateResponse(request, "admin/cuentas.html", {
        "cuentas": cuentas_list,
    })


# ─── EXPORTAR EMAILS PARA GOOGLE PLAY ────────────────────
@router.get("/exportar-emails")
async def admin_exportar_emails(request: Request, tipo: str = "todos"):
    redirect = require_admin(request)
    if redirect: return redirect

    conn = get_db()
    cur = conn.cursor()

    if tipo == "clientes":
        emails = cur.execute("""
            SELECT DISTINCT u.email FROM usuarios u
            JOIN pagos p ON p.user_id = u.id
            WHERE p.estado='approved' ORDER BY u.id DESC
        """).fetchall()
    else:
        emails = cur.execute("SELECT email FROM usuarios WHERE verificado=1 ORDER BY id DESC").fetchall()

    conn.close()
    contenido = "\n".join([e["email"] for e in emails])

    return PlainTextResponse(
        contenido,
        headers={"Content-Disposition": f"attachment; filename=emails_{tipo}.txt"}
    )


# ─── CREAR CUENTA MANUAL ─────────────────────────────────
@router.get("/crear-cuenta", response_class=HTMLResponse)
async def admin_crear_cuenta_form(request: Request, msg: str = "", error: str = ""):
    redirect = require_admin(request)
    if redirect: return redirect
    return templates.TemplateResponse(request, "admin/crear_cuenta.html", {
        "msg": msg, "error": error
    })


@router.post("/crear-cuenta")
async def admin_crear_cuenta(request: Request,
                              email: str = Form(...),
                              dias: int = Form(30)):
    redirect = require_admin(request)
    if redirect: return redirect

    # Import lazy para evitar circular imports
    from modules.admrufu import crear_cuenta, generar_password
    import sys
    sys.path.insert(0, "/root/ConnectVPN")

    conn = get_db()
    cur = conn.cursor()

    user = cur.execute("SELECT id, nombre, email FROM usuarios WHERE email=?", (email,)).fetchone()
    if not user:
        conn.close()
        return RedirectResponse(f"/admin/crear-cuenta?error=No existe el usuario con email {email}", status_code=303)

    # Generar nombre SSH único
    base = email.split("@")[0].lower().replace(".", "")[:8]
    sufijo = str(datetime.now().timestamp()).split(".")[0][-4:]
    nombre_ssh = f"{base}{sufijo}"

    resultado = crear_cuenta(nombre_ssh, dias)
    if not resultado.get("success"):
        conn.close()
        return RedirectResponse(f"/admin/crear-cuenta?error=Error creando cuenta: {resultado.get('error','desconocido')}", status_code=303)

    expira_at = (datetime.now() + timedelta(days=dias)).isoformat()
    cur.execute("""INSERT INTO cuentas_ssh (user_id, usuario_ssh, password_ssh, ip, puerto, dias, expira_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user["id"], resultado["user"], resultado["password"],
                 resultado.get("ip","149.33.19.164"), 22, dias, expira_at))
    cur.execute("INSERT INTO pagos (user_id, payment_id, estado, tipo, monto) VALUES (?, ?, 'approved', 'manual', 0)",
                (user["id"], f"manual_{datetime.now().timestamp()}"))
    conn.commit()
    conn.close()

    return RedirectResponse(
        f"/admin/crear-cuenta?msg=Cuenta creada: {resultado['user']} / {resultado['password']} ({dias} días)",
        status_code=303)
