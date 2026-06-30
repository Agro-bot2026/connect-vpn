#!/bin/bash
# ============================================================
# Instalador automático - ConnectVPN
# SaaS de reventa SSH (FastAPI + MercadoPago + ADMRufu)
# ============================================================
set -e

REPO="https://github.com/Agro-bot2026/connect-vpn.git"
DIR="/root/ConnectVPN"

echo "============================================"
echo "   INSTALADOR CONNECTVPN"
echo "============================================"
echo ""

echo "[1/8] Instalando dependencias del sistema..."
apt update -qq
apt install -y python3 python3-venv python3-pip git curl tar nodejs npm openssh-client netcat-openbsd >/dev/null 2>&1
if ! command -v pm2 >/dev/null 2>&1; then
    npm install -g pm2 >/dev/null 2>&1
fi
echo "      Listo."

echo "[2/8] Clonando el código desde GitHub..."
if [ -d "$DIR" ]; then
    echo "      ATENCION: $DIR ya existe. Respaldala y borrala antes de reinstalar."
    exit 1
fi
git clone "$REPO" "$DIR"
cd "$DIR"
echo "      Codigo descargado."

echo "[3/8] Creando entorno virtual e instalando librerias..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip >/dev/null 2>&1
./venv/bin/pip install -r requirements.txt >/dev/null 2>&1
echo "      Librerias instaladas."

echo "[4/8] Necesito tu backup (.env, base de datos, clave RSA de ADMRufu)."
echo "      Esta en tu Drive: Backups_VPS/ConnectVPN/"
echo ""
echo "      Pega el ENLACE NORMAL de Drive (el de compartir)."
echo ""
read -p "      Enlace de Drive: " ENLACE_DRIVE

if [ -z "$ENLACE_DRIVE" ]; then
    echo "      No pegaste enlace. ConnectVPN queda instalado SIN credenciales."
    echo "      Subi a mano .env, connectvpn.db y la clave admrufu_key."
    exit 0
fi

FILE_ID=$(echo "$ENLACE_DRIVE" | grep -oE '[-_a-zA-Z0-9]{25,}' | head -1)
if [ -z "$FILE_ID" ]; then
    echo "      No pude extraer el ID del enlace. Revisa el enlace de Drive."
    exit 1
fi
echo "      ID detectado: $FILE_ID"

echo "[5/8] Descargando credenciales desde Drive..."
DL="https://drive.google.com/uc?export=download&id=${FILE_ID}"
curl -L -c /tmp/gdrive_cookie.txt "$DL" -o /tmp/connectvpn_cred.tar.gz
if file /tmp/connectvpn_cred.tar.gz | grep -qi "html"; then
    CONFIRM=$(grep -oE 'confirm=[a-zA-Z0-9_-]+' /tmp/connectvpn_cred.tar.gz | head -1 | cut -d= -f2)
    curl -L -b /tmp/gdrive_cookie.txt \
        "https://drive.google.com/uc?export=download&confirm=${CONFIRM}&id=${FILE_ID}" \
        -o /tmp/connectvpn_cred.tar.gz
fi
rm -f /tmp/gdrive_cookie.txt
echo "      Descargado."

echo "[6/8] Extrayendo .env y base de datos..."
tar xzf /tmp/connectvpn_cred.tar.gz -C "$DIR" .env connectvpn.db 2>/dev/null || tar xzf /tmp/connectvpn_cred.tar.gz -C "$DIR"
echo "      .env y DB en su lugar."

echo "[7/8] Colocando la clave RSA de ADMRufu en /root/.ssh/ ..."
mkdir -p /root/.ssh
chmod 700 /root/.ssh
tar xzf /tmp/connectvpn_cred.tar.gz -C /root/.ssh admrufu_key
chmod 600 /root/.ssh/admrufu_key
rm -f /tmp/connectvpn_cred.tar.gz
echo "      Clave RSA colocada con permisos correctos (600)."

echo "[8/8] Arrancando ConnectVPN con pm2..."
cd "$DIR"
pm2 start main.py --name connectvpn --interpreter "$DIR/venv/bin/python3"
pm2 save
echo ""
echo "============================================"
echo "   INSTALACION COMPLETA"
echo "============================================"
echo "   pm2 list            -> ver estado"
echo "   pm2 logs connectvpn -> ver logs"
echo ""
echo "   IMPORTANTE: Volve a tu Drive y pone el archivo"
echo "   de credenciales como PRIVADO de nuevo."
echo "============================================"
