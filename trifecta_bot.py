import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from telegram import Bot

# Configuración de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CANAL_PRINCIPAL_ID = os.getenv("TELEGRAM_CANAL_PRINCIPAL_ID")
CANAL_RADAR_ID = os.getenv("TELEGRAM_CANAL_RADAR_ID")

if not all([TELEGRAM_BOT_TOKEN, CANAL_PRINCIPAL_ID, CANAL_RADAR_ID]):
    logger.error("Faltan variables de entorno críticas en el archivo .env")
    raise ValueError("Configuración incompleta.")

# Inicializar cliente de Telegram
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)


# 1. FUNCIÓN DE ENVÍO CON SEGMENTACIÓN DE DESTINOS
async def enviar_notificacion(texto: str, destino: str = "principal") -> bool:
    """
    Envía notificaciones segmentadas a canales de Telegram.
    - principal: Notificación con sonido.
    - radar: Notificación silenciosa para análisis pasivo.
    """
    try:
        if destino == "radar":
            chat_id = CANAL_RADAR_ID
            disable_notification = True
        else:
            chat_id = CANAL_PRINCIPAL_ID
            disable_notification = False

        await telegram_bot.send_message(
            chat_id=chat_id,
            text=texto,
            parse_mode="Markdown",
            disable_notification=disable_notification
        )
        logger.info(f"Mensaje enviado con éxito a destino: {destino}")
        return True
    except Exception as e:
        logger.error(f"Error al enviar mensaje a Telegram ({destino}): {e}")
        return False


# 2 y 3. PROCESAMIENTO DE SEÑALES, FILTRO RVOL DUAL Y VOLUME HEATMAP
async def procesar_senal_webhook(data: Dict[str, Any]):
    """
    Procesa los Webhooks entrantes de TradingView.
    Filtra el Setup B según el RVOL, el día de la semana y potencia con el Heatmap.
    """
    setup = data.get("setup")      # Ej: "Setup B"
    tipo = data.get("tipo")        # Ej: "LONG" o "SHORT"
    activo = data.get("activo")    # Ej: "BTCUSDT"
    rvol = float(data.get("rvol", 0.0))
    heatmap = data.get("heatmap", "Normal").strip().lower()  # "normal", "caliente", "muy caliente"

    # Redirección automática si es una alerta de análisis (Punto 4)
    mensaje_crudo = data.get("mensaje", "")
    if mensaje_crudo.startswith("RADAR:") or "stc" in mensaje_crudo.lower() or "supertrend" in mensaje_crudo.lower():
        await enviar_notificacion(texto=f"📢 *{mensaje_crudo}*", destino="radar")
        return

    # Lógica exclusiva para el Setup B
    if setup == "Setup B":
        # Detectar día de la semana nativo (0=Lunes, 5=Sábado, 6=Domingo)
        dia_semana = datetime.now().weekday()
        es_fin_de_semana = dia_semana in [5, 6]
        
        # Umbral dinámico
        umbral_rvol = 3.0 if es_fin_de_semana else 4.0

        # Filtro restrictivo inteligente (Eliminados filtros ATR/ADX)
        if rvol < umbral_rvol:
            logger.info(f"⚠️ [{activo}] {setup} descartado por RVOL insuficiente: {rvol} (Requerido: {umbral_rvol})")
            return  # Ignorar en silencio

        # 3. Integración del Volume Heatmap como Potenciador
        prefijo_heatmap = ""
        if heatmap in ["caliente", "muy caliente", "roja"]:
            prefijo_heatmap = "🔥 ALTA DENSIDAD · "

        # Construcción del mensaje para señal ejecutable
        msg_ejecutable = (
            f"{prefijo_heatmap}✅ *SEÑAL EJECUTABLE: {setup}*\n"
            f"📈 *Activo:* {activo}\n"
            f"🔔 *Dirección:* {tipo}\n"
            f"📊 *RVOL:* {rvol} (Umbral: {umbral_rvol})\n"
            f"🧬 *Escudo Institucional:* {heatmap.upper()}"
        )
        
        # Enviar al canal principal con sonido e interactuar con la API del Bróker/Exchange
        await enviar_notificacion(texto=msg_ejecutable, destino="principal")
        await ejecutar_orden_en_broker(activo, tipo, cantidad=1.0)


# 5. NUEVA ALERTA DE GESTIÓN ACTIVA (BREAKEVEN AL TOCAR TP1)
async def gestionar_operacion_abierta(activo: str, precio_actual: float, operacion: Dict[str, Any]):
    """
    Monitorea en tiempo real o por eventos las salidas. 
    Ejecuta el movimiento a Breakeven inmediatamente al tocar el TP1.
    """
    if operacion.get("status") != "ABIERTA" or operacion.get("breakeven_activo", False):
        return

    tp1 = operacion.get("tp1")
    precio_entrada = operacion.get("precio_entrada")
    tipo = operacion.get("tipo")

    # Condición de toque de TP1 según la dirección del trade
    hit_tp1 = (tipo == "LONG" and precio_actual >= tp1) or (tipo == "SHORT" and precio_actual <= tp1)

    if hit_tp1:
        # 1. Modificar la orden en el Bróker/Exchange de inmediato
        exito_broker = await modificar_stop_loss_broker(activo, nuevo_sl=precio_entrada)
        
        if exito_broker:
            operacion["breakeven_activo"] = True
            operacion["stop_loss"] = precio_entrada
            
            # 2. Notificación obligatoria al canal principal
            msg_be = f"🛡️ *POSICIÓN ASEGURADA* · [{activo}]\nTP1 Alcanzado. SL movido a Breakeven. Trade libre de riesgo."
            await enviar_notificacion(texto=msg_be, destino="principal")


# --- Mockups de Funciones de Conectividad con Exchanges ---
async def ejecutar_orden_en_broker(activo: str, tipo: str, cantidad: float):
    logger.info(f"Enviando orden de {tipo} para {activo} al Bróker...")

async def modificar_stop_loss_broker(activo: str, nuevo_sl: float) -> bool:
    logger.info(f"Modificando Stop Loss a {nuevo_sl} en {activo}...")
    return True  # Devuelve True tras la confirmación de la API del Exchange


# --- Simulación de Ejecución del Entorno ---
if __name__ == "__main__":
    import asyncio
    
    # Simulación de un Webhook entrante de fin de semana con Heatmap caliente
    mock_webhook_setup_b = {
        "setup": "Setup B",
        "tipo": "LONG",
        "activo": "ETHUSDT",
        "rvol": 4.2,
        "heatmap": "Caliente"
    }

    # Simulación de una Alerta de Análisis del Radar
    mock_webhook_radar = {
        "mensaje": "RADAR: BTCUSDT - STC 4H cruzó al alza el nivel 25."
    }

    async def main():
        print("Iniciando simulación del Bot de Trading...")
        # Procesar entrada válida del Setup B con alta densidad
        await procesar_senal_webhook(mock_webhook_setup_b)
        # Procesar alerta pasiva redirigida a Radar de forma silenciosa
        await procesar_senal_webhook(mock_webhook_radar)

    asyncio.run(main())
