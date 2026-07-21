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
    Función blindada con TIMEOUT de 5 segundos para evitar que el bot se congele si Telegram falla.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CANAL_PRINCIPAL_ID:
        print("Aviso: Tokens o IDs de Telegram no configurados correctamente.")
        return

    chat_id = TELEGRAM_CANAL_RADAR_ID if destino == "radar" else TELEGRAM_CANAL_PRINCIPAL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    
    try:
        # IMPORTANTE: timeout=5 evita que el bot se quede pegado eternamente
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code != 200:
            print(f"Error al enviar mensaje a Telegram: {response.text}")
    except requests.exceptions.Timeout:
        print("Advertencia: La petición a Telegram tardó demasiado (Timeout). El bot continúa operando...")
    except Exception as e:
        print(f"Excepción en la petición HTTP: {e}")


def evaluar_filtro_rvol(rvol_actual):
    """
    Filtro inteligente de volumen:
    - Lunes a Viernes: Exige RVOL >= 4.0
    - Sábados y Domingos: Exige RVOL >= 3.0
    """
    dia_actual = datetime.utcnow().weekday()
    es_fin_de_semana = (dia_actual >= 5)
    umbral_requerido = 3.0 if es_fin_de_semana else 4.0
    
    return rvol_actual >= umbral_requerido, umbral_requerido


def procesar_senal_setup_b(par, precio, sl, tp1, tp2, tp3, rvol_actual, heatmap_rojo=False):
    cumple_rvol, umbral = evaluar_filtro_rvol(rvol_actual)
    
    if not cumple_rvol:
        return False

    estadisticas_sesion["entradas"] += 1
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


def generar_reporte_estadistico_0945():
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
    for k in estadisticas_sesion:
        estadisticas_sesion[k] = 0


# ==========================================
# BUCLE PRINCIPAL (CONTROLADO Y VELOZ)
# ==========================================
def ejecutar_ciclo_bot():
    print("🚀 Bot iniciado correctamente. Controlando tiempos de escaneo...")
    
    # Lista de ejemplo de tus pares (reemplázala por tu lista real de monitoreo)
    lista_de_pares = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT"]

    while True:
        inicio_ciclo = time.time()
        ahora = datetime.utcnow()
        hora_actual = ahora.strftime("%H:%M")
        
        print(f"[{hora_actual}] Iniciando ciclo de escaneo rápido de pares...")

        # Simulación o iteración rápida de tus pares
        for par in lista_de_pares:
            # Aquí va la lógica de lectura de cada par de forma fluida
            pass

        # Disparar reporte a las 09:45
        if hora_actual == "09:45":
            generar_reporte_estadistico_0945()
            time.sleep(60)

        # Control estricto de tiempo del ciclo
        tiempo_transcurrido = time.time() - inicio_ciclo
        print(f"Ciclo completado en {tiempo_transcurrido:.2f} segundos.")

        # Descansa el resto del minuto (si tardó 5 segundos, duerme 55 para cumplir 1 minuto exacto)
        tiempo_espera = max(1, 60 - tiempo_transcurrido)
        time.sleep(tiempo_espera)

if __name__ == "__main__":
    ejecutar_ciclo_bot()
    
