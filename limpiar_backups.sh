#!/bin/bash
# ════════════════════════════════════════════════════════════
#   Limpieza automática de backups de ConnectVPN
# ════════════════════════════════════════════════════════════
#   Reglas:
#   - Conserva SIEMPRE los marcados con _OK o _final
#   - Conserva los últimos 7 días (todos)
#   - Borra los intermedios (sin _OK ni _final) más viejos de 7 días
# ════════════════════════════════════════════════════════════

BACKUP_DIR="/root/ConnectVPN/backups"
DIAS_CONSERVAR=7
HOY=$(date +%s)

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  🧹 LIMPIEZA DE BACKUPS — ConnectVPN"
echo "  Fecha: $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════════════════════"
echo ""

ANTES=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
echo "📊 Espacio antes: $ANTES"
echo ""

BORRADOS=0
CONSERVADOS=0

cd "$BACKUP_DIR" || exit 1

for item in *; do
    [ -e "$item" ] || continue

    # Conservar siempre los _OK y _final
    if [[ "$item" == *_OK* ]] || [[ "$item" == *_final* ]]; then
        echo "  ✅ CONSERVAR (marcado): $item"
        CONSERVADOS=$((CONSERVADOS+1))
        continue
    fi

    # Ver edad del archivo
    MOD=$(stat -c %Y "$item" 2>/dev/null)
    DIFF_DIAS=$(( (HOY - MOD) / 86400 ))

    if [ "$DIFF_DIAS" -lt "$DIAS_CONSERVAR" ]; then
        echo "  ✅ CONSERVAR (reciente, ${DIFF_DIAS}d): $item"
        CONSERVADOS=$((CONSERVADOS+1))
    else
        echo "  ❌ BORRAR (${DIFF_DIAS}d, intermedio): $item"
        rm -rf "$item"
        BORRADOS=$((BORRADOS+1))
    fi
done

echo ""
DESPUES=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
echo "═══════════════════════════════════════════════════════"
echo "  📊 Espacio después: $DESPUES"
echo "  🗑️  Borrados: $BORRADOS"
echo "  💾 Conservados: $CONSERVADOS"
echo "═══════════════════════════════════════════════════════"
echo ""
