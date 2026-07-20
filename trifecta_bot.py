import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
import requests

# ── 0. CONFIGURACIÓN DE LOGS DE ALTA VISIBILIDAD ────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── 1. CARGA DE VARIABLES Y SECRETOS DE ENTORNO ──────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CANAL_PRINCIPAL_ID = os.getenv("TELEGRAM_CANAL_PRINCIPAL_ID")
CANAL_RADAR_ID = os.getenv("TELEGRAM_CANAL_RADAR_ID")
STATE_FILE = os.getenv("STATE_FILE", "state.json")

def enviar_notificacion(texto_html: str, destino: str = "principal") -> bool:
    """
    Envía mensajes a Telegram utilizando HTML para evitar fallos de parseo.
    - principal: Notificaciones con sonido (Alertas Operables / Breakeven)
    - radar: Notificaciones silenciadas (Filtros, STC, Supertrend pasivos)
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ FALTA DE CONFIGURACIÓN: TELEGRAM_BOT_TOKEN no existe en Secrets.")
        return False

    chat_id = CANAL_RADAR_ID if destino == "radar" else CANAL_PRINCIPAL_ID
    if not chat_id:
        logger.error(f"❌ FALTA DE CONFIGURACIÓN: ID del canal destino '{destino}' no encontrado.")
        return False

    # El canal radar se envía silenciado (disable_notification=True)
    silenciar_notificacion = (destino == "radar")

    url = f"https://api.telegram.com/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto_html,
        "parse_mode": "HTML",
        "disable_notification": silenciar_notificacion
    }
    
    try:
        response = requests.post(url, json=payload, timeout=12)
        if response.status_code == 200:
            logger.info(f"⚡ Notificación enviada con éxito al canal [{destino.upper()}].")
            return True
        else:
            logger.error(f"❌ Telegram API Error ({response.status_code}): {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red/timeout al conectar con Telegram ({destino}): {e}")
        return False

# ── PERSISTENCIA JSON PARA GITHUB ACTIONS ────────────────────────────────
def cargar_estado() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Error al leer {STATE_FILE}, creando estado limpio. Detalle: {e}")
            return {"operaciones": {}}
    return {"operaciones": {}}

def guardar_estado(estado: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 Estado actualizado correctamente en '{STATE_FILE}'.")
    except Exception as e:
        logger.error(f"❌ Error crítico escribiendo en {STATE_FILE}: {e}")

# ── 2 y 3. LÓGICA Y FILTROS DEL INDICADOR ───────────────────────────────
def procesar_alerta_indicador(data: Dict[str, Any]):
    setup = data.get("setup", "")
    tipo = data.get("tipo", "LONG").upper()
    activo = data.get("activo", "UNKNOWN")
    try:
        rvol = float(data.get("rvol", 0.0))
    except (ValueError, TypeError):
        rvol = 0.0

    heatmap = str(data.get("heatmap", "Normal")).strip().lower()
    mensaje_crudo = str(data.get("mensaje", "")).strip()

    # 4. REDIRECCIÓN AUTOMÁTICA AL CANAL RADAR (Análisis Pasivo Silenciado)
    msg_lower = mensaje_crudo.lower()
    if mensaje_crudo.startswith("RADAR:") or "stc" in msg_lower or "supertrend" in msg_lower:
        html_radar = f"📢 <b>[RADAR DE MERCADO]</b>\n<code>{mensaje_crudo}</code>"
        enviar_notificacion(texto_html=html_radar, destino="radar")
        return

    # Lógica y Filtros restrictivos para el Setup B
    if setup == "Setup B":
        # Ajuste Zona Horaria UTC-6 para determinar día de la semana preciso
        tz_gt = timezone(timedelta(hours=-6))
        hora_local = datetime.now(tz_gt)
        dia_semana = hora_local.weekday()  # 0=Lunes ... 5=Sábado, 6=Domingo
        es_fin_de_semana = dia_semana in [5, 6]
        
        # Umbral dinámico exigido según el día
        umbral_rvol = 3.0 if es_fin_de_semana else 4.0

        if rvol < umbral_rvol:
            logger.info(
                f"🚫 [{activo}] {setup} descartado por filtro RVOL. "
                f"Obtenido: {rvol:.2f}x | Requerido ({'Fin de semana' if es_fin_de_semana else 'Lunes-Viernes'}): {umbral_rvol}x"
            )
            return  # Ignora en silencio sin molestar en Telegram

        # Integración del Volume Heatmap como prefijo opcional
        prefijo_heatmap = "🔥 <b>ALTA DENSIDAD</b> · " if heatmap in ["caliente", "muy caliente", "roja", "alta densidad"] else ""

        msg_senal = (
            f"{prefijo_heatmap}✅ <b>SEÑAL EJECUTABLE: {setup}</b>\n"
            f"📈 <b>Activo:</b> <code>{activo}</code>\n"
            f"🔔 <b>Dirección:</b> {tipo}\n"
            f"📊 <b>RVOL:</b> {rvol:.2f}x (Mínimo: {umbral_rvol}x)\n"
            f"🧬 <b>Heatmap:</b> {heatmap.upper()}"
        )
        enviar_notificacion(texto_html=msg_senal, destino="principal")

# ── 5. GESTIÓN ACTIVA DE OPERACIONES Y MOVIMIENTO A BREAKEVEN ────────────
def gestionar_breakeven_trades(precio_actual_dict: Dict[str, float]):
    """
    Compara precios actuales de mercado con las órdenes guardadas en state.json.
    Al tocar el TP1, marca la orden como resguardada e informa al Canal Principal con sonido.
    """
    estado = cargar_estado()
    operaciones = estado.get("operaciones", {})
    cambio_detectado = False

    for id_trade, operacion in operaciones.items():
        if operacion.get("status") != "ABIERTA" or operacion.get("breakeven_activo", False):
            continue

        activo = operacion.get("activo")
        precio_actual = precio_actual_dict.get(activo)
        
        if precio_actual is None:
            continue

        tipo = operacion.get("tipo", "LONG").upper()
        precio_entrada = operacion.get("precio_entrada", 0.0)
        tp1 = operacion.get("tp1", 0.0)

        # Evaluar toque del TP1 según dirección del trade
        toco_tp1 = (tipo == "LONG" and precio_actual >= tp1) or (tipo == "SHORT" and precio_actual <= tp1)

        if toco_tp1:
            operacion["breakeven_activo"] = True
            operacion["stop_loss"] = precio_entrada
            cambio_detectado = True
            
            msg_be = (
                f"🛡️ <b>POSICIÓN ASEGURADA (BREAKEVEN)</b>\n"
                f"📌 <b>Activo:</b> <code>{activo}</code>\n"
                f"🎯 <b>TP1 Alcanzado:</b> {precio_actual}\n"
                f"🔒 <b>Nuevo Stop Loss:</b> {precio_entrada} (Precio de entrada)"
            )
            enviar_notificacion(texto_html=msg_be, destino="principal")

    if cambio_detectado:
        guardar_estado(estado)

# ── BLOQUE DE EJECUCIÓN SÍNCRONA PARA GITHUB ACTIONS ──────────────────────
if __name__ == "__main__":
    logger.info("🚀 Iniciando ciclo de escaneo desde GitHub Actions...")
    
    # Payload simulado / Entrada recibida
    datos_ciclo_actual = {
        "setup": "Setup B",
        "tipo": "LONG",
        "activo": "BTCUSDT",
        "rvol": 4.5,
        "heatmap": "Caliente"
    }
    
    procesar_alerta_indicador(datos_ciclo_actual)
    logger.info("🏁 Ciclo completado. El Runner finalizó la ejecución.")
