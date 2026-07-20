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

# ── 1. CONFIGURACIÓN Y VARIABLES DE ENTORNO ──────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CANAL_PRINCIPAL_ID = os.getenv("TELEGRAM_CANAL_PRINCIPAL_ID")
CANAL_RADAR_ID = os.getenv("TELEGRAM_CANAL_RADAR_ID")
STATE_FILE = os.getenv("STATE_FILE", "state.json")

def enviar_notificacion(texto_html: str, destino: str = "principal") -> bool:
    """
    Envía notificaciones a Telegram usando parse_mode HTML.
    - destino="principal": Canal principal con sonido activado (Señales A++, A+, B y Breakeven).
    - destino="radar": Canal de análisis pasivo con sonido silenciado (Supertrend, STC, Volumen Medio).
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ FALTA DE CONFIGURACIÓN: TELEGRAM_BOT_TOKEN no existe en las variables de entorno.")
        return False

    chat_id = CANAL_RADAR_ID if destino == "radar" else CANAL_PRINCIPAL_ID
    if not chat_id:
        logger.error(f"❌ FALTA DE CONFIGURACIÓN: No se encontró el ID para el canal destino '{destino}'.")
        return False

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
            logger.info(f"⚡ Mensaje enviado con éxito al canal [{destino.upper()}].")
            return True
        else:
            logger.error(f"❌ Telegram API Error ({response.status_code}): {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de conexión/timeout al enviar a Telegram ({destino}): {e}")
        return False

# ── PERSISTENCIA JSON PARA EL STATE.JSON ─────────────────────────────────
def cargar_estado() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Error al leer {STATE_FILE}. Inicializando estado nuevo: {e}")
            return {"operaciones": {}}
    return {"operaciones": {}}

def guardar_estado(estado: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(estado, f, indent=4, ensure_ascii=False)
        logger.info(f"💾 Estado persistido exitosamente en '{STATE_FILE}'.")
    except Exception as e:
        logger.error(f"❌ Error crítico al escribir {STATE_FILE}: {e}")

# ── 2, 3 y 4. PROCESAMIENTO DE ALERTAS (A++, A+, B, RADAR, HEATMAP) ─────
def procesar_alerta_indicador(data: Dict[str, Any]):
    setup = str(data.get("setup", "")).strip()
    tipo = str(data.get("tipo", "LONG")).strip().upper()
    activo = str(data.get("activo", "UNKNOWN")).strip().upper()
    
    try:
        rvol = float(data.get("rvol", 0.0))
    except (ValueError, TypeError):
        rvol = 0.0

    heatmap = str(data.get("heatmap", "Normal")).strip().lower()
    mensaje_crudo = str(data.get("mensaje", "")).strip()
    msg_lower = mensaje_crudo.lower()

    # 4. REDIRECCIÓN DE ALERTAS AL CANAL RADAR
    # (Supertrend, cruces STC y control de Heatmap "Amarilla" para Volumen Medio)
    if mensaje_crudo.startswith("RADAR:") or "stc" in msg_lower or "supertrend" in msg_lower:
        html_radar = f"📢 <b>[ANÁLISIS PASIVO - RADAR 4H]</b>\n<code>{mensaje_crudo}</code>"
        enviar_notificacion(texto_html=html_radar, destino="radar")
        return

    # Si el Heatmap está en zona "Amarilla", se desvía y reporta Volumen Medio al canal Radar
    if heatmap in ["amarilla", "yellow", "medio"]:
        html_volumen_medio = (
            f"🟡 <b>VOLUMEN MEDIO DETECTADO</b>\n"
            f"📈 <b>Activo:</b> <code>{activo}</code>\n"
            f"🧬 <b>Heatmap:</b> AMARILLA"
        )
        enviar_notificacion(texto_html=html_volumen_medio, destino="radar")
        return

    # Normalizar la etiqueta del setup para evaluar A++, A+ y B por igual
    setups_validos = ["A++", "A+", "B", "SETUP A++", "SETUP A+", "SETUP B"]
    
    if any(s == setup.upper() for s in setups_validos):
        # 2. SUSTITUCIÓN DE VOLATILIDAD POR FILTRO INTELIGENTE DE RVOL (Zona horaria Guatemala UTC-6)
        tz_gt = timezone(timedelta(hours=-6))
        hora_local = datetime.now(tz_gt)
        dia_semana = hora_local.weekday()  # 0=Lunes ... 5=Sábado, 6=Domingo
        es_fin_de_semana = dia_semana in [5, 6]

        umbral_rvol = 3.0 if es_fin_de_semana else 4.0

        if rvol < umbral_rvol:
            logger.info(
                f"🚫 [{activo}] Signal {setup} ignorada en silencio. "
                f"RVOL actual: {rvol:.2f}x < Mínimo requerido ({'Fin de semana' if es_fin_de_semana else 'Lunes-Viernes'}): {umbral_rvol}x"
            )
            return

        # 3. INTEGRACIÓN DEL VOLUME HEATMAP (Zona Roja / Caliente)
        prefijo_heatmap = "🔥 <b>ALTA DENSIDAD</b> · " if heatmap in ["caliente", "muy caliente", "roja", "alta densidad"] else ""

        msg_senal = (
            f"{prefijo_heatmap}✅ <b>SEÑAL EJECUTABLE: {setup.upper()}</b>\n"
            f"📈 <b>Activo:</b> <code>{activo}</code>\n"
            f"🔔 <b>Dirección:</b> {tipo}\n"
            f"📊 <b>RVOL:</b> {rvol:.2f}x (Umbral: {umbral_rvol:.1f}x)\n"
            f"🧬 <b>Heatmap:</b> {heatmap.upper()}"
        )
        enviar_notificacion(texto_html=msg_senal, destino="principal")

# ── 5. NUEVA ALERTA DE GESTIÓN ACTIVA (BREAKEVEN) ────────────────────────
def gestionar_breakeven_trades(precio_actual_dict: Dict[str, float]):
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

        toco_tp1 = (tipo == "LONG" and precio_actual >= tp1) or (tipo == "SHORT" and precio_actual <= tp1)

        if toco_tp1:
            operacion["breakeven_activo"] = True
            operacion["stop_loss"] = precio_entrada
            cambio_detectado = True
            
            msg_be = f"🛡️ <b>POSICIÓN ASEGURADA</b> · [{activo}]\nTP1 Alcanzado. SL movido a Breakeven. Trade libre de riesgo."
            enviar_notificacion(texto_html=msg_be, destino="principal")

    if cambio_detectado:
        guardar_estado(estado)

# ── EJECUCIÓN PRUEBA / RUNNER ────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Ejecutando bot en GitHub Actions...")

    alerta_ejemplo = {
        "setup": "A++",
        "tipo": "LONG",
        "activo": "BTCUSDT",
        "rvol": 4.8,
        "heatmap": "Caliente"
    }

    procesar_alerta_indicador(alerta_ejemplo)
    
