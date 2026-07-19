"""
RVOL Weekend Filter Bot — replica RVOL_Weekend_Heatmap.pine
Detecta volumen relativo inusual (RVOL >= umbral) confirmado en 4H
y cambios de dirección del STC. Corre independiente del bot Trifecta.

Destino: grupo Telegram RVOL (secrets RVOL_BOT_TOKEN + RVOL_CHAT_ID)
"""

import os, json, math, requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# ── CONFIG (replica Pine Script) ─────────────────────────────────
PAIRS = [
    {"symbol": "SOLUSDT",  "name": "SOL/USDT"},
    {"symbol": "ETHUSDT",  "name": "ETH/USDT"},
    {"symbol": "BNBUSDT",  "name": "BNB/USDT"},
    {"symbol": "AVAXUSDT", "name": "AVAX/USDT"},
    {"symbol": "LINKUSDT", "name": "LINK/USDT"},
    {"symbol": "DOTUSDT",  "name": "DOT/USDT"},
    {"symbol": "NEARUSDT", "name": "NEAR/USDT"},
    {"symbol": "AAVEUSDT", "name": "AAVE/USDT"},
    {"symbol": "SUIUSDT",  "name": "SUI/USDT"},
    {"symbol": "OPUSDT",   "name": "OP/USDT"},
    {"symbol": "INJUSDT",  "name": "INJ/USDT"},
    {"symbol": "WLDUSDT",  "name": "WLD/USDT"},
    {"symbol": "TIAUSDT",  "name": "TIA/USDT"},
    {"symbol": "XRPUSDT",  "name": "XRP/USDT"},
    {"symbol": "HYPEUSDT", "name": "HYPE/USDT"},
    {"symbol": "DOGEUSDT", "name": "DOGE/USDT"},
    {"symbol": "ZECUSDT",  "name": "ZEC/USDT"},
    {"symbol": "XMRUSDT",  "name": "XMR/USDT"},
    {"symbol": "ADAUSDT",  "name": "ADA/USDT"},
    {"symbol": "DEXEUSDT", "name": "DEXE/USDT"},
]

CFG = {
    # RVOL
    "AVG_DAYS":       7,      # días para calcular el promedio
    "BARS_PER_DAY":   96,     # velas de 15m en 24h
    "RVOL_WEEKEND":   2.0,    # umbral sábado/domingo
    "RVOL_WEEKDAY":   3.0,    # umbral lunes-viernes
    # Heatmap z-score
    "HEAT_LEN":       20,
    "Z_HIGH":         1.0,
    "Z_EXTRA_HIGH":   2.0,
    "Z_LOW":         -1.0,
    # 4H confirmation
    "VOL_4H_LEN":     20,
    # STC
    "STC_CYCLE":      10,
    "STC_FAST":       23,
    "STC_SLOW":       50,
    "STC_FACTOR":     0.5,
    # Candles
    "CANDLES_15":     800,    # ~8 días de 15m para calcular el promedio mismo horario
    "CANDLES_4H":     50,
}

YF_SYMBOLS = {
    "SOLUSDT":"SOL-USD",  "ETHUSDT":"ETH-USD",  "BNBUSDT":"BNB-USD",  "AVAXUSDT":"AVAX-USD",
    "LINKUSDT":"LINK-USD","DOTUSDT":"DOT-USD",  "NEARUSDT":"NEAR-USD","AAVEUSDT":"AAVE-USD",
    "SUIUSDT":"SUI-USD",  "OPUSDT":"OP-USD",    "INJUSDT":"INJ-USD",
    "WLDUSDT":"WLD-USD",  "TIAUSDT":"TIA-USD",
    "XRPUSDT":"XRP-USD",  "HYPEUSDT":"HYPE-USD","DOGEUSDT":"DOGE-USD",
    "ZECUSDT":"ZEC-USD",  "XMRUSDT":"XMR-USD",  "ADAUSDT":"ADA-USD",
    "DEXEUSDT":"DEXE-USD",
}

STATE_FILE = os.path.join(os.path.dirname(__file__), "state_rvol.json")

# ── DATOS ─────────────────────────────────────────────────────────
def fetch_klines(symbol, interval, limit):
    yf_sym = YF_SYMBOLS[symbol]
    if interval == "15":
        yi, per = "15m", "59d"
    else:
        yi, per = "1h", "729d"
    df = yf.download(yf_sym, period=per, interval=yi,
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.tail(limit).copy()
    return [{"time": int(ts.timestamp()*1000),
             "volume": float(r["Volume"]),
             "close":  float(r["Close"])}
            for ts, r in df.iterrows()]

# ── INDICADORES ───────────────────────────────────────────────────
def calc_rvol(candles):
    """RVOL mismo horario: volumen actual / promedio del mismo slot en últimos 7 días."""
    bpd = CFG["BARS_PER_DAY"]
    days = CFG["AVG_DAYS"]
    n = len(candles)
    if n < bpd * days + 1:
        return None
    current_vol = candles[-1]["volume"]
    same_slot_vols = [candles[-(1 + i * bpd)]["volume"]
                      for i in range(1, days + 1)
                      if n - 1 - i * bpd >= 0]
    if not same_slot_vols:
        return None
    avg = sum(same_slot_vols) / len(same_slot_vols)
    return (current_vol / avg) if avg > 0 else None

def calc_heatmap_z(candles):
    """Z-score del volumen respecto a media/stdev de los últimos HEAT_LEN periodos."""
    length = CFG["HEAT_LEN"]
    if len(candles) < length:
        return 0.0
    vols = [c["volume"] for c in candles[-length:]]
    mean = sum(vols) / length
    variance = sum((v - mean) ** 2 for v in vols) / length
    std = math.sqrt(variance) if variance > 0 else 0
    return ((candles[-1]["volume"] - mean) / std) if std > 0 else 0.0

def heatmap_label(z):
    if z >= CFG["Z_EXTRA_HIGH"]: return "🔴 EXTRA ALTO"
    if z >= CFG["Z_HIGH"]:       return "🟠 ALTO"
    if z <= CFG["Z_LOW"]:        return "🔵 BAJO"
    return "⚪ Normal"

def confirm_4h(candles_4h):
    """Volumen 4H actual >= promedio de los últimos VOL_4H_LEN periodos."""
    length = CFG["VOL_4H_LEN"]
    if len(candles_4h) < length + 1:
        return True  # si no hay suficiente historial, no bloquear
    vols = [c["volume"] for c in candles_4h[-(length+1):-1]]
    avg = sum(vols) / len(vols)
    return candles_4h[-1]["volume"] >= avg

def calc_stc(closes):
    """STC simplificado — detecta giro al alza o a la baja."""
    n = len(closes)
    fast = CFG["STC_FAST"]; slow = CFG["STC_SLOW"]
    cycle = CFG["STC_CYCLE"]; factor = CFG["STC_FACTOR"]
    if n < slow + cycle * 2:
        return None, None

    # EMA fast y slow
    def ema_arr(arr, p):
        k = 2/(p+1); res = [None]*len(arr); last = None
        for i, v in enumerate(arr):
            if last is None: res[i] = v; last = v
            else: res[i] = v*k + last*(1-k); last = res[i]
        return res

    ef = ema_arr(closes, fast)
    es = ema_arr(closes, slow)
    macd = [(ef[i]-es[i]) if (ef[i] and es[i]) else 0.0 for i in range(n)]

    def stoch_smooth(src, ln):
        out = [0.0]*len(src); f = [0.0]*len(src)
        for i in range(ln-1, len(src)):
            w = src[i-ln+1:i+1]; lo, hi = min(w), max(w)
            raw = ((src[i]-lo)/(hi-lo)*100) if hi != lo else 0.0
            f[i] = raw if i==ln-1 else f[i-1] + factor*(raw - f[i-1])
            out[i] = f[i]
        return out

    f2 = stoch_smooth(macd, cycle)
    stc_out = stoch_smooth(f2, cycle)

    cur  = stc_out[-1]
    prev = stc_out[-2]
    prev2= stc_out[-3] if len(stc_out) >= 3 else prev

    turn_up   = cur > prev and prev <= prev2
    turn_down = cur < prev and prev >= prev2
    return turn_up, turn_down

# ── ANALISIS POR PAR ──────────────────────────────────────────────
def analyze_pair(pair):
    sym = pair["symbol"]
    c15 = fetch_klines(sym, "15",  CFG["CANDLES_15"])
    c4h = fetch_klines(sym, "240", CFG["CANDLES_4H"])

    closes = [c["close"] for c in c15]
    now_utc = datetime.now(timezone.utc)
    is_weekend = now_utc.weekday() >= 5  # 5=Sat, 6=Sun

    rvol = calc_rvol(c15)
    z    = calc_heatmap_z(c15)
    ok4h = confirm_4h(c4h)
    threshold = CFG["RVOL_WEEKEND"] if is_weekend else CFG["RVOL_WEEKDAY"]

    rvol_alert = (rvol is not None) and (rvol >= threshold) and ok4h
    stc_up, stc_down = calc_stc(closes)

    return {
        "rvol":       round(rvol, 2) if rvol else None,
        "threshold":  threshold,
        "z":          round(z, 2),
        "z_label":    heatmap_label(z),
        "ok4h":       ok4h,
        "rvol_alert": rvol_alert,
        "stc_up":     stc_up,
        "stc_down":   stc_down,
        "is_weekend": is_weekend,
        "price":      closes[-1],
    }

# ── ESTADO ────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except: return {}

def save_state(s):
    with open(STATE_FILE, "w") as f: json.dump(s, f, indent=2)

# ── NOTIFICACIONES ────────────────────────────────────────────────
def notify(title, body, priority="default", tags="chart_bar"):
    """Envía SOLO al grupo RVOL (secrets independientes del bot Trifecta)."""
    topic = os.environ.get("RVOL_NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}",
                          data=body.encode("utf-8"),
                          headers={"Title": title, "Priority": priority, "Tags": tags},
                          timeout=10)
        except Exception as e: print("Error ntfy:", e)

    bot   = os.environ.get("RVOL_BOT_TOKEN")
    chat  = os.environ.get("RVOL_CHAT_ID")
    if bot and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{bot}/sendMessage",
                          json={"chat_id": chat,
                                "text": f"*{title}*\n{body}",
                                "parse_mode": "Markdown"},
                          timeout=10)
        except Exception as e: print("Error Telegram RVOL:", e)

    if not topic and not (bot and chat):
        print(f"[Sin canal RVOL] {title} | {body}")

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    state = load_state()
    new_state = {}
    alerts_sent = 0
    now_utc = datetime.now(timezone.utc)
    day_label = "🗓 FIN DE SEMANA" if now_utc.weekday() >= 5 else "📅 ENTRE SEMANA"

    for pair in PAIRS:
        sym = pair["symbol"]
        try:
            data = analyze_pair(pair)
        except Exception as e:
            print(f"Error en {sym}: {e}")
            continue

        prev = state.get(sym, {"rvol_alert": False, "stc_up": False, "stc_down": False})

        # ── 1. Alerta RVOL (nuevo trigger) ───────────────────────
        if data["rvol_alert"] and not prev.get("rvol_alert"):
            rvol_str  = f"{data['rvol']}x" if data["rvol"] else "—"
            threshold = data["threshold"]
            day_str   = "Fin de semana" if data["is_weekend"] else "Entre semana"
            notify(
                f"📊 RVOL Alto · {pair['name']}",
                f"⚠️ Volumen relativo inusual detectado\n"
                f"RVOL: *{rvol_str}* (umbral {day_str}: {threshold}x)\n"
                f"Heatmap: {data['z_label']} (z={data['z']})\n"
                f"4H confirmado: {'✅' if data['ok4h'] else '❌'}\n"
                f"💰 Precio: ${data['price']:.4f}\n"
                f"⚠️ NO es señal Trifecta · Revisar manualmente\n"
                f"🕐 {day_label}",
                priority="high",
                tags="bar_chart,warning",
            )
            alerts_sent += 1
            print(f"RVOL ALERT: {pair['name']} RVOL={rvol_str}")

        # ── 2. RVOL se normaliza ──────────────────────────────────
        if prev.get("rvol_alert") and not data["rvol_alert"]:
            notify(
                f"📉 RVOL Normalizado · {pair['name']}",
                f"El volumen relativo volvió a niveles normales\n"
                f"RVOL actual: {data['rvol']}x · Umbral: {data['threshold']}x\n"
                f"💰 Precio: ${data['price']:.4f}",
                priority="low",
                tags="bar_chart",
            )
            alerts_sent += 1

        # ── 3. STC gira al alza ───────────────────────────────────
        if data["stc_up"] and not prev.get("stc_up"):
            rvol_ctx = f"RVOL: {data['rvol']}x · " if data["rvol"] else ""
            notify(
                f"🟢 STC Giro Alcista · {pair['name']}",
                f"STC cambió de dirección: *giro ALCISTA*\n"
                f"{rvol_ctx}Heatmap: {data['z_label']}\n"
                f"💰 Precio: ${data['price']:.4f}\n"
                f"ℹ️ Contexto de volumen, revisar con Trifecta\n"
                f"🕐 {day_label}",
                priority="default",
                tags="green_circle,chart_with_upwards_trend",
            )
            alerts_sent += 1
            print(f"STC UP: {pair['name']}")

        # ── 4. STC gira a la baja ─────────────────────────────────
        if data["stc_down"] and not prev.get("stc_down"):
            rvol_ctx = f"RVOL: {data['rvol']}x · " if data["rvol"] else ""
            notify(
                f"🔴 STC Giro Bajista · {pair['name']}",
                f"STC cambió de dirección: *giro BAJISTA*\n"
                f"{rvol_ctx}Heatmap: {data['z_label']}\n"
                f"💰 Precio: ${data['price']:.4f}\n"
                f"ℹ️ Contexto de volumen, revisar con Trifecta\n"
                f"🕐 {day_label}",
                priority="default",
                tags="red_circle,chart_with_downwards_trend",
            )
            alerts_sent += 1
            print(f"STC DOWN: {pair['name']}")

        new_state[sym] = {
            "rvol_alert": data["rvol_alert"],
            "stc_up":     data["stc_up"],
            "stc_down":   data["stc_down"],
        }

    save_state(new_state)
    print(f"RVOL chequeo completo ({now_utc.isoformat()}). Alertas: {alerts_sent}")

if __name__ == "__main__":
    main()
