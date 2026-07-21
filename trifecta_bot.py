import json
import os
import time
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

# ================= CONFIGURACIÓN =================
TELEGRAM_TOKEN = "TU_TOKEN_DE_TELEGRAM"
TELEGRAM_CHAT_ID = "TU_CHAT_ID"

PAIRS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "ADA-USD", "XRP-USD",
    "DOGE-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "NEAR-USD",
    "OP-USD", "ATOM-USD", "RENDER-USD", "INJ-USD", "WLD-USD", "TIA-USD",
    "ZEC-USD", "XMR-USD"
]

STATE_FILE = "state.json"

# ================= FUNCIONES DE ESTADO =================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error al guardar el estado: {e}")

# ================= NOTIFICACIONES =================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        # Se añade un timeout de 10 segundos para evitar que la petición cuelgue el script
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error al enviar la alerta de Telegram: {e}")

# ================= LÓGICA PRINCIPAL =================
def check_market():
    state = load_state()
    now = datetime.utcnow()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S UTC')}] Iniciando escaneo de mercado...")

    for pair in PAIRS:
        try:
            # Descarga con manejo de errores y timeout indirecto a través de yfinance
            # Se descargan datos de 4h para análisis de tendencia
            df_4h = yf.download(pair, period="5d", interval="4h", progress=False)
            
            if df_4h.empty or len(df_4h) < 2:
                print(f"Datos insuficientes para {pair}, omitiendo...")
                continue

            # Ejemplo de lógica de análisis (Estructura base)
            # Asegurarse de lidiar con posibles DataFrames multibatch de yfinance modernos
            if isinstance(df_4h.columns, pd.MultiIndex):
                df_4h.columns = df_4h.columns.get_level_values(0)

            last_close = float(df_4h['Close'].iloc[-1])
            prev_close = float(df_4h['Close'].iloc[-2])
            
            # Condición de ejemplo para notificar (cambio mayor al 3% en el bloque)
            change = ((last_close - prev_close) / prev_close) * 100

            if abs(change) >= 3.0:
                last_alert_time = state.get(pair, 0)
                current_time = time.time()
                
                # Evitar spam: al menos 4 horas (14400 segundos) entre alertas del mismo par
                if current_time - last_alert_time > 14400:
                    message = f"🚨 *Alerta de Movimiento*\nPar: `{pair}`\nVariación: *{change:.2f}%*\nPrecio actual: `{last_close}`"
                    send_telegram_alert(message)
                    state[pair] = current_time
                    save_state(state)

        except Exception as e:
            print(f"Error procesando el par {pair}: {e}")
            # Continuar con el siguiente par en lugar de romper todo el script
            continue

    print("Escaneo finalizado correctamente. Cerrando proceso.")

if __name__ == "__main__":
    # Ejecución única y secuencial para evitar bucles colgados
    check_market()
