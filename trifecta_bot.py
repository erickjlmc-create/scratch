import os
import json
import logging
from datetime import datetime
from typing import Dict, Any
import requests

# Configuración de logs en consola de GitHub Actions
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 1. CARGA DE VARIABLES DESDE EL ENTORNO DE GITHUB ACTIONS
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CANAL_PRINCIPAL_ID = os.getenv("TELEGRAM_CANAL_PRINCIPAL_ID")
CANAL_RADAR_ID = os.getenv("TELEGRAM_CANAL_RADAR_ID")
STATE_FILE = "state.json"

# Función síncrona optimizada para el ciclo rápido de GitHub Actions
def enviar_notificacion(texto: str, destino: str = "principal") -> bool:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Falta el TOKEN del bot de Telegram en los Secrets de GitHub.")
        return False

    # Segmentación de destinos y configuración de sonido
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
        logger.error(f"Error al enviar mensaje a Telegram ({destino}): {e}")
        return False

# Funciones de persistencia para el archivo state.json controlado por tu .yml
def cargar_estado() -> Dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {"operaciones": {}}
    return {"operaciones": {}}

def guardar_estado(estado: Dict[str, Any]):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(estado, f, indent=4)
    except Exception as e:
        logger.error(f"No se pudo escribir en state.json: {e}")

# 2 y 3. PROCESAMIENTO DE FILTROS (RVOL DUAL & HEATMAP)
def procesar_alerta_indicador(data: Dict[str, Any]):
    setup = data.get("setup")
    tipo = data.get("tipo")        # "LONG" o "SHORT"
    activo = data.get("activo")    # Ej: "BTCUSDT"
    rvol = float(data.get("rvol", 0.0))
    heatmap = data.get("heatmap", "Normal").strip().lower()
    mensaje_crudo = data.get("mensaje", "")

    # 4. REDIRECCIÓN AUTOMÁTICA AL CANAL RADAR (Análisis Pasivo Silenciado)
    if mensaje_crudo.startswith("RADAR:") or "stc" in mensaje_crudo.lower() or "supertrend" in mensaje_crudo.lower():
        enviar_notificacion(texto=f"📢 *{mensaje_crudo}*", destino="radar")
        return

    # Lógica y Filtros restrictivos para el Setup B
    if setup == "Setup B":
        # Usar la función de tiempo nativa de Python (0=Lunes, 5=Sábado, 6=Domingo)
        dia_semana = datetime.now().weekday()
        es_fin_de_semana = dia_semana in [5, 6]
        
        # Umbral dinámico exigido según el día
        umbral_rvol = 3.0 if es_fin_de_semana else 4.0

        if rvol < umbral_rvol:
            logger.info(f"❌ [{activo}] {setup} ignorado. RVOL actual: {rvol} (Mínimo requerido: {umbral_rvol})")
            return  # Se ignora en silencio sin enviar alertas

        # 3. Integración del Volume Heatmap como prefijo opcional
        prefijo_heatmap = "🔥 ALTA DENSIDAD · " if heatmap in ["caliente", "muy caliente", "roja"] else ""

        msg_senal = (
            f"{prefijo_heatmap}✅ *SEÑAL EJECUTABLE: {setup}*\n"
            f"📈 *Activo:* {activo}\n"
            f"🔔 *Dirección:* {tipo}\n"
            f"📊 *RVOL:* {rvol} (Umbral: {umbral_rvol})\n"
            f"🧬 *Heatmap:* {heatmap.upper()}"
        )
        enviar_notificacion(texto=msg_senal, destino="principal")

# 5. GESTIÓN ACTIVA DE OPERACIONES Y MOVIMIENTO A BREAKEVEN
def gestionar_breakeven_trades(precio_actual_dict: Dict[str, float]):
    """
    Compara los precios actuales del mercado con las órdenes guardadas en state.json.
    Si toca el TP1, mueve el SL al precio de entrada y alerta al canal principal.
    """
    estado = cargar_estado()
    cambio_detectado = False

    for id_trade, operacion in estado.get("operaciones", {}).items():
        if operacion.get("status") != "ABIERTA" or operacion.get("breakeven_activo", False):
            continue

        activo = operacion.get("activo")
        precio_actual = precio_actual_dict.get(activo)
        
        if not precio_actual:
            continue

        tipo = operacion.get("tipo")
        precio_entrada = operacion.get("precio_entrada")
        tp1 = operacion.get("tp1")

        # Evaluar toque del TP1 según dirección
        toco_tp1 = (tipo == "LONG" and precio_actual >= tp1) or (tipo == "SHORT" and precio_actual <= tp1)

        if toco_tp1:
            # [Aquí ejecutarías la llamada a la API de tu Broker/Exchange para mover el SL]
            # Ejemplo: exchange.modificar_sl(activo, precio_entrada)
            
            operacion["breakeven_activo"] = True
            operacion["stop_loss"] = precio_entrada
            cambio_detectado = True
            
            # Notificación obligatoria con sonido al canal principal
            msg_be = f"🛡️ *POSICIÓN ASEGURADA* · [{activo}]\nTP1 Alcanzado. SL movido a Breakeven. Trade libre de riesgo."
            enviar_notificacion(texto=msg_be, destino="principal")

    if cambio_detectado:
        guardar_estado(estado)

# Simulación de ejecución del flujo de GitHub Actions
if __name__ == "__main__":
    logger.info("Iniciando escaneo del bot desde el disparo de Cron-job...")
    
    # Aquí es donde mapeas tus datos de entrada del escáner en este ciclo
    # (Ejemplo simulado de una alerta de fin de semana que pasa el filtro)
    datos_ciclo_actual = {
        "setup": "Setup B",
        "tipo": "LONG",
        "activo": "BTCUSDT",
        "rvol": 4.5,
        "heatmap": "Caliente"
    }
    
    procesar_alerta_indicador(datos_ciclo_actual)
    logger.info("Escaneo finalizado. GitHub procederá a guardar el state.json si hubo cambios.")
