import os
import time
from datetime import datetime
import requests

# ==========================================
# CONFIGURACIÓN DE CREDENCIALES Y CANALES
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CANAL_PRINCIPAL_ID = os.getenv("TELEGRAM_CANAL_PRINCIPAL_ID")  # Canal B (Ejecutable)
TELEGRAM_CANAL_RADAR_ID = os.getenv("TELEGRAM_CANAL_RADAR_ID")          # Canal Radar (Silenciado)

# Contadores globales para el reporte estadístico de las 09:45
estadisticas_sesion = {
    "volatilidad": 0,
    "supertrend": 0,
    "entradas": 0,
    "informativos": 0,
    "bloqueos": 0,
    "desbloqueos": 0,
    "caducadas": 0
}

def enviar_notificacion(texto, destino="principal"):
    """
    Función unificada para enviar mensajes al canal principal o al radar.
    """
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN no está configurado.")
        return

    chat_id = TELEGRAM_CANAL_RADAR_ID if destino == "radar" else TELEGRAM_CANAL_PRINCIPAL_ID
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Error al enviar mensaje a Telegram: {response.text}")
    except Exception as e:
        print(f"Excepción en la petición HTTP: {e}")


def evaluar_filtro_rvol(rvol_actual):
    """
    Filtro inteligente de volumen:
    - Lunes a Viernes (0 a 4): Exige RVOL >= 4.0
    - Sábados y Domingos (5 y 6): Exige RVOL >= 3.0
    """
    dia_actual = datetime.utcnow().weekday()
    es_fin_de_semana = (dia_actual >= 5)
    umbral_requerido = 3.0 if es_fin_de_semana else 4.0
    
    return rvol_actual >= umbral_requerido, umbral_requerido


def procesar_senal_setup_b(par, precio, sl, tp1, tp2, tp3, rvol_actual, heatmap_rojo=False):
    """
    Procesa la entrada del Setup B aplicando el filtro estricto de RVOL.
    """
    cumple_rvol, umbral = evaluar_filtro_rvol(rvol_actual)
    
    if not cumple_rvol:
        # Se ignora en silencio porque no pasó el filtro institucional
        return False

    # Si pasa el filtro, sumamos a la estadística de entradas
    estadisticas_sesion["entradas"] += 1

    # Detectar si el Heatmap está caliente para agregar la etiqueta
    tag_densidad = "🔥 *ALTA DENSIDAD* · " if heatmap_rojo else ""
    
    mensaje = (
        f"✅ *B 📈 LONG · {par}*\n"
        f"{tag_densidad}📊 *RVOL:* {rvol_actual} (Umbral mín: {umbral})\n"
        f"💰 *Precio Entr:* `{precio}`\n"
        f"🛑 *SL:* `{sl}`\n"
        f"🎯 *TPs:* `{tp1}` | `{tp2}` | `{tp3}`"
    )
    
    enviar_notificacion(mensaje, destino="principal")
    return True


def verificar_gestion_breakeven(par, precio_actual, precio_tp1, sl_actual):
    """
    Verifica en tiempo real si el precio tocó el TP1 para mover el SL a Breakeven.
    """
    if precio_actual >= precio_tp1 and sl_actual != precio_actual:
        mensaje = (
            f"🛡️ *POSICIÓN ASEGURADA · {par}*\n"
            f"🎯 TP1 Alcanzado. Stop Loss movido a Breakeven.\n"
            f"🔒 *Trade 100% libre de riesgo.*"
        )
        enviar_notificacion(mensaje, destino="principal")
        return True
    return False


def generar_reporte_estadistico_0945():
    """
    Genera y envía la tabla estadística exacta a las 09:45 al canal principal.
    """
    total_alertas = sum(estadisticas_sesion.values())
    if total_alertas == 0:
        return

    def calc_pct(val):
        return f"{(val / total_alertas) * 100:.1f}%"

    mensaje = (
        "📊 *RESUMEN ESTADÍSTICO DE LA SESIÓN (09:45)*\n\n"
        "Tipo de Alerta | Cantidad | % | Impacto tras el cambio\n"
        "---|:---:|:---:|---\n"
        f"💥 *Volatilidad Extr.* | {estadisticas_sesion['volatilidad']} | {calc_pct(estadisticas_sesion['volatilidad'])} | Reducida por RVOL.\n"
        f"🔄 *Supertrend* | {estadisticas_sesion['supertrend']} | {calc_pct(estadisticas_sesion['supertrend'])} | Estructura macro.\n"
        f"🎯 *Señales Entr.* | {estadisticas_sesion['entradas']} | {calc_pct(estadisticas_sesion['entradas'])} | Trades ejecutados.\n"
        f"ℹ️ *Informativos* | {estadisticas_sesion['informativos']} | {calc_pct(estadisticas_sesion['informativos'])} | Avisos de sesión.\n"
        f"🔒 *Bloqueo Activo* | {estadisticas_sesion['bloqueos']} | {calc_pct(estadisticas_sesion['bloqueos'])} | Circuit Breaker.\n"
        f"🔓 *Desbloqueo* | {estadisticas_sesion['desbloqueos']} | {calc_pct(estadisticas_sesion['desbloqueos'])} | Reaperturas.\n"
        f"❌ *Caducadas* | {estadisticas_sesion['caducadas']} | {calc_pct(estadisticas_sesion['caducadas'])} | Time-out.\n"
        f"📉 *Total Recibidas* | **{total_alertas}** | **100%** | *Métrica limpia.*\n\n"
        "✅ _Escaneo matutino finalizado. Sistema operando con filtro RVOL institucional._"
    )
    
    enviar_notificacion(mensaje, destino="principal")
    
    # Reiniciar contadores para el día siguiente
    for k in estadisticas_sesion:
        estadisticas_sesion[k] = 0


# ==========================================
# BUCLE PRINCIPAL DEL BOT (SIMULACIÓN O RUNNER)
# ==========================================
def ejecutar_ciclo_bot():
    print("Bot iniciado correctamente en GitHub con filtrado RVOL y doble canal...")
    
    while True:
        ahora = datetime.utcnow()
        hora_actual = ahora.strftime("%H:%M")
        
        # --- AQUí IRÍA TU LÓGICA DE ESCANEO DE PARES ---
        # Ejemplo: Si llega una alerta de Supertrend o STC 4H, la mandas al radar:
        # enviar_notificacion("🔄 STC 4H cambió de tendencia en AVAX", destino="radar")
        # estadisticas_sesion["supertrend"] += 1

        # Disparar automáticamente el reporte estadístico a las 09:45 UTC (ajusta a tu zona horaria local si es necesario)
        if hora_actual == "09:45":
            generar_reporte_estadistico_0945()
            time.sleep(60) # Espera un minuto para evitar que se ejecute dos veces en el mismo minuto
            
        time.sleep(10) # Pausa de ciclo para no saturar CPU

if __name__ == "__main__":
    ejecutar_ciclo_bot()
