"""
Módulo ADMRufu — Gestión de cuentas SSH via SSH remoto.
"""
import os
import random
import string
import subprocess
import logging
from dotenv import load_dotenv

load_dotenv("/root/ConnectVPN/.env")

log = logging.getLogger(__name__)

ADMRUFU_HOST = os.getenv("ADMRUFU_HOST")
ADMRUFU_USER = os.getenv("ADMRUFU_USER", "root")
ADMRUFU_KEY = os.getenv("ADMRUFU_KEY")
ADMRUFU_SOCKET = os.getenv("ADMRUFU_SOCKET", "/tmp/admAPI.sock")
SSH_DIAS = int(os.getenv("SSH_DIAS", "30"))

def ejecutar_comando(comando: str) -> str:
    """Ejecuta un comando en ADMRufu via SSH."""
    try:
        result = subprocess.run(
            [
                "ssh", "-i", ADMRUFU_KEY,
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                f"{ADMRUFU_USER}@{ADMRUFU_HOST}",
                f"echo -e '{comando}' | nc -U {ADMRUFU_SOCKET} -q 2"
            ],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    except Exception as e:
        log.error(f"Error ejecutando comando ADMRufu: {e}")
        return ""

def generar_password(longitud: int = 10) -> str:
    """Genera una contraseña aleatoria."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=longitud))

def crear_cuenta(nombre: str, dias: int = None) -> dict:
    """
    Crea una cuenta SSH nueva.
    Retorna {success, user, password, dias, ip}
    """
    if dias is None:
        dias = SSH_DIAS
    password = generar_password()
    comando = f"/ssh add {nombre} {password} 1 {dias}"
    respuesta = ejecutar_comando(comando)

    if "creado" in respuesta.lower() or "success" in respuesta.lower():
        return {
            "success": True,
            "user": nombre,
            "password": password,
            "dias": dias,
            "ip": ADMRUFU_HOST,
            "puerto": 22
        }
    else:
        log.error(f"Error creando cuenta: {respuesta}")
        return {"success": False, "error": respuesta}

def renovar_cuenta(nombre: str, dias: int = None) -> dict:
    """Renueva una cuenta SSH existente por X días más."""
    if dias is None:
        dias = SSH_DIAS
    comando = f"/ssh set expire {nombre} {dias}"
    respuesta = ejecutar_comando(comando)

    if respuesta:
        return {"success": True, "dias": dias}
    return {"success": False, "error": respuesta}

def eliminar_cuenta(nombre: str) -> bool:
    """Elimina una cuenta SSH."""
    comando = f"/ssh remove {nombre}"
    respuesta = ejecutar_comando(comando)
    return True

def listar_cuentas() -> str:
    """Lista todas las cuentas SSH."""
    return ejecutar_comando("/ssh list")

def info_cuenta(nombre: str) -> str:
    """Información de una cuenta SSH."""
    return ejecutar_comando(f"/ssh info {nombre}")
