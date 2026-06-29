"""
Módulo Chatbot — ConnectVPN
IA: DeepSeek V4 Flash
Accede a datos del usuario logueado (días, vencimiento, usuario SSH, etc.)
"""
import os
import sqlite3
import logging
from datetime import datetime
from openai import OpenAI
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv("/root/ConnectVPN/.env")
log = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DB_PATH = "/root/ConnectVPN/connectvpn.db"
SYSTEM_PROMPT_PATH = "/root/ConnectVPN/chatbot/system_prompt.txt"
MODEL = "deepseek-v4-flash"

# Cliente OpenAI apuntando a DeepSeek
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# Cargar el system prompt al iniciar
try:
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
    log.info(f"✅ System prompt cargado ({len(SYSTEM_PROMPT)} chars)")
except Exception as e:
    log.error(f"❌ Error cargando system prompt: {e}")
    SYSTEM_PROMPT = "Eres el asistente virtual de ConnectVPN."

router = APIRouter(prefix="/chatbot")


# ─── HELPERS PARA OBTENER DATOS DEL USUARIO ──────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_user_context(user_id: int) -> str:
    """
    Obtiene los datos relevantes del usuario para incluir en cada consulta.
    NO incluye la contraseña por seguridad.
    """
    conn = get_db()
    cur = conn.cursor()

    user = cur.execute("SELECT email, nombre FROM usuarios WHERE id=?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return ""

    cuenta = cur.execute("""
        SELECT usuario_ssh, ip, puerto, dias, expira_at, activa
        FROM cuentas_ssh WHERE user_id=? ORDER BY id DESC LIMIT 1
    """, (user_id,)).fetchone()

    pagos = cur.execute("""
        SELECT monto, tipo, created_at FROM pagos
        WHERE user_id=? AND estado='approved' ORDER BY id DESC LIMIT 3
    """, (user_id,)).fetchall()

    conn.close()

    contexto = f"\n\n=== DATOS DEL CLIENTE ACTUAL ===\n"
    contexto += f"Email: {user['email']}\n"
    contexto += f"Nombre: {user['nombre'] or 'sin nombre'}\n"

    if cuenta:
        try:
            expira = datetime.fromisoformat(cuenta['expira_at'])
            dias_restantes = (expira - datetime.now()).days
            estado = "ACTIVA" if dias_restantes > 0 and cuenta['activa'] else "VENCIDA"
        except:
            dias_restantes = 0
            estado = "DESCONOCIDO"

        contexto += f"\nCuenta SSH:\n"
        contexto += f"  Usuario: {cuenta['usuario_ssh']}\n"
        contexto += f"  Servidor: {cuenta['ip']}:{cuenta['puerto']}\n"
        contexto += f"  Plan contratado: {cuenta['dias']} días\n"
        contexto += f"  Vence el: {cuenta['expira_at'][:16]}\n"
        contexto += f"  Días restantes: {max(0, dias_restantes)}\n"
        contexto += f"  Estado: {estado}\n"
        contexto += f"  (La contraseña SSH NUNCA debes darla por chat, solo decir que está en su dashboard)\n"
    else:
        contexto += f"\n⚠️ Este cliente NO tiene cuenta SSH activa todavía.\n"

    if pagos:
        contexto += f"\nÚltimos pagos:\n"
        for p in pagos:
            contexto += f"  - ${p['monto']} ARS ({p['tipo']}) — {p['created_at'][:16]}\n"

    contexto += f"\n=== FIN DATOS DEL CLIENTE ===\n"
    return contexto


# ─── ENDPOINT DEL CHAT ───────────────────────────────────
class ChatMessage(BaseModel):
    message: str
    history: list = []


@router.post("/message")
async def chat_message(request: Request, payload: ChatMessage):
    # Verificar que el usuario esté logueado
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse(
            {"error": "Necesitás iniciar sesión para usar el chat."},
            status_code=401
        )

    if not DEEPSEEK_API_KEY:
        return JSONResponse(
            {"error": "El chatbot no está configurado correctamente."},
            status_code=500
        )

    # Validar mensaje
    msg = (payload.message or "").strip()
    if not msg:
        return JSONResponse({"error": "Mensaje vacío."}, status_code=400)
    if len(msg) > 1500:
        return JSONResponse(
            {"error": "Tu mensaje es muy largo. Reducilo a menos de 1500 caracteres."},
            status_code=400
        )

    # Obtener contexto del usuario (datos personales)
    user_context = get_user_context(user_id)

    # Armar la conversación
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + user_context}
    ]

    # Agregar historial reciente (últimos 6 mensajes para no gastar tokens)
    for h in payload.history[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"][:1500]})

    # Mensaje actual
    messages.append({"role": "user", "content": msg})

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=600,
            timeout=30
        )
        reply = response.choices[0].message.content.strip()
        return {"reply": reply}
    except Exception as e:
        log.error(f"Error DeepSeek: {e}")
        return JSONResponse(
            {"error": "Tuve un problema técnico. Probá de nuevo en unos segundos o escribile a Charly a +54 9 263 484-1144."},
            status_code=500
        )
