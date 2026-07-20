import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any
import requests  # Más directo para scripts que abren y cierran por Cron

# Configuración de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Intentar cargar .env local solo para pruebas tuyas en PC; en producción el hosting las inyecta
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CANAL_PRINCIPAL_ID = os.getenv("TELEGRAM_CANAL_PRINCIPAL_ID")
CANAL_RADAR_ID = os.getenv("TELEGRAM_CANAL_RADAR_ID")

# 1. FUNCIÓN DE ENVÍO SEGMENTADO (SÍNCRONA OPTIMIZADA PARA CRON)
def enviar_notificacion(texto: str, destino: str = "principal") -> bool:
    """
    Envía las alertas al canal correspondiente. 
    Ideal para Cron-Jobs ya que gestiona la petición de forma inmediata.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Falta el TOKEN del bot de Telegram.")
        return False

    chat_id = CANAL_RADAR_ID if destino == "radar" else CANAL_PRINCIPAL_ID
    disable_notification = True if destino == "radar" else False

    url = f"https://api.telegram.com/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_notification": disable_notification
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error al conectar con Telegram: {e}")
        return False


# 2 y 3. PROCESAMIENTO PRINCIPAL DE ALERTAS (RVOL DUAL & HEATMAP)
def procesar_alerta_entrante(data: Dict[str, Any]):
    """
    Lógica que analiza los datos que envía tu TradingView o tu validador en este tick del Cron.
    """
    setup = data.get("setup")
    tipo = data.get("tipo")        # "LONG" o "SHORT"
    activo = data.get("activo")    # "BTCUSDT"
    rvol = float(data.get("rvol", 0.0))
    heatmap = data.get("heatmap", "Normal").strip().lower()
    mensaje_crudo = data.get("mensaje", "")

    # 4. REDIRECCIÓN AUTOMÁTICA AL CANAL RADAR (Prefijo RADAR: o componentes analíticos)
    if mensaje_crudo.startswith("RADAR:") or "stc" in mensaje_crudo.lower() or "supertrend" in mensaje_crudo.lower():
        enviar_notificacion(texto=f"📢 *{mensaje_crudo}*", destino="radar")
        return

    # Filtro exclusivo para Setup B
    if setup == "Setup B":
        # Filtro de tiempo nativo de Python para el día de la semana (0=Lunes, 6=Domingo)
        dia_semana = datetime.now().weekday()
        es_fin_de_semana = dia_semana in [5, 6]
        
        # Umbral dinámico exigido
        umbral_rvol = 3.0 if es_fin_de_semana else 4.0

        if rvol < umbral_rvol:
            logger.info(f"❌ [{activo}] Setup B descartado por volumen relativo bajo: {rvol} (Requerido: {umbral_rvol})")
            return  # Ignora la señal en silencio y termina la ejecución de este bloque

        # 3. Integración del Volume Heatmap como Potenciador (No Filtro)
        prefijo_heatmap = "🔥 ALTA DENSIDAD · " if heatmap in ["caliente", "muy caliente", "roja"] else ""

        msg_final = (
            f"{prefijo_heatmap}✅ *SEÑAL EJECUTABLE: {setup}*\n"
            f"📈 *Activo:* {activo}\n"
            f"🔔 *Dirección:* {tipo}\n"
            f"📊 *RVOL:* {rvol} (Umbral Mínimo: {umbral_rvol})\n"
            f"🧬 *Estado Heatmap:* {heatmap.upper()}"
        )
        
        enviar_notificacion(texto=msg_final, destino="principal")
        # Aquí añadirías tu función de trigger de órdenes: ejecutar_orden(activo, tipo)


# 5. GESTIÓN ACTIVA: DETECCIÓN DE TP1 -> MOVIMIENTO A BREAKEVEN
def verificar_y_ejecutar_breakeven(operacion_activa: Dict[str, Any], precio_actual: float):
    """
    Se ejecuta en cada pasada del Cron para revisar si el precio actual de mercado 
    ya tocó el TP1, modificando el SL al precio de entrada si es el caso.
    """
    if operacion_activa.get("status") != "ABIERTA" or operacion_activa.get("breakeven_hecho", False):
        return

    activo = operacion_activa.get("activo")
    tipo = operacion_activa.get("tipo")
    precio_entrada = operacion_activa.get("precio_entrada")
    tp1 = operacion_activa.get("tp1")

    # Verificar si tocó o superó el TP1 según la dirección
    toco_tp1 = (tipo == "LONG" and precio_actual >= tp1) or (tipo == "SHORT" and precio_actual <= tp1)

    if toco_tp1:
        # Enviar orden de modificación de SL al Exchange/Broker aquí...
        logger.info(f"Modificando SL de {activo} a precio de entrada ({precio_entrada}).")
        
        # Guardar cambio de estado
        operacion_activa["breakeven_hecho"] = True
        operacion_activa["stop_loss"] = precio_entrada
        
        # Notificación inmediata al canal principal
        msg_be = f"🛡️ *POSICIÓN ASEGURADA* · [{activo}]\nTP1 Alcanzado. SL movido a Breakeven. Trade libre de riesgo."
        enviar_notificacion(texto=msg_be, destino="principal")


# --- Bloque de ejecución temporal por ciclo del Cron ---
if __name__ == "__main__":
    # Ejemplo de datos simulados recibidos en la ejecución de este ciclo de 15 minutos:
    datos_prueba_setup_b = {
        "setup": "Setup B",
        "tipo": "SHORT",
        "activo": "SOLUSDT",
        "rvol": 4.5,
        "heatmap": "Caliente"
    }
    
    logger.info("Cron-job iniciado: Ejecutando verificaciones de ciclo...")
    procesar_alerta_entrante(datos_prueba_setup_b)
    logger.info("Ciclo terminado con éxito.")
