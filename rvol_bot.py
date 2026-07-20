"""
RVOL Weekend Filter Bot v2 — replica RVOL_Weekend_Heatmap_1_.pine
Cambios vs v1:
  - STC calculado en 4H (no en 15M)
  - Alertas STC basadas en cruces de niveles: 25↑, 75↓, 90↑ (maduro), 10↓ (maduro)
  - RVOL sigue en 15M con confirmacion de volumen en 4H

Destino: grupo Telegram RVOL (secrets RVOL_BOT_TOKEN + RVOL_CHAT_ID)
"""

import os, json, math, requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────
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
    # RVOL en 15M
    "AVG_DAYS":         7,
    "BARS_PER_DAY":     96,       # velas de 15m en 24h
    "RVOL_WEEKEND":     2.0,
    "RVOL_WEEKDAY":     3.0,
    # Heatmap z-score en 15M
    "HEAT_LEN":         20,
    "Z_HIGH":           1.0,
    "Z_EXTRA_HIGH":     2.0,
    "Z_LOW":           -1.0,
    # Confirmacion volumen 4H
    "VOL_4H_LEN":       20,
    # STC en 4H (replica Pine)
    "STC_CYCLE":        10,
    "STC_FAST":         23,
    "STC_SLOW":         50,
    "STC_FACTOR":       0.5,
    "STC_LEVEL_LOW":    25,       # cruce arriba → alcista iniciando
    "STC_LEVEL_HIGH":   75,       # cruce abajo  → bajista
    "STC_MATURE_HIGH":  90,       # zona madura alcista (agotamiento)
    "STC_MATURE_LOW":   10,       # zona madura bajista (posible reversión)
    # Candles
    "CANDLES_15":       800,      # ~8 dias de 15m
    "CANDLES_4H":       150,      # suficiente para STC + vol promedio en 4H
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
        yi, per = "1h", "729d"   # 1h como proxy de 4H, tomamos cada 4 velas
    df = yf.download(yf_sym, period=per, interval=yi,
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.tail(limit).copy()
    return [{"time":   int(ts.timestamp() * 1000),
             "open":   float(r["Open"]),
             "high":   float(r["High"]),
             "low":    float(r["Low"]),
             "close":  float(r["Close"]),
             "volume": float(r["Volume"])}
            for ts, r in df.iterrows()]

def resample_to_4h(candles_1h):
    """Agrupa velas de 1H en grupos de 4 para simular el timeframe 4H."""
    result = []
    chunk = 4
    # trabajamos en lotes de 4 desde el final hacia atrás para alinear la vela actual
    n = len(candles_1h)
    # tomar solo los bloques completos + el incompleto más reciente
    start = n % chunk  # resto → primer bloque puede ser incompleto
    groups = []
    if start > 0:
        groups.append(candles_1h[:start])
    for i in range(start, n, chunk):
        groups.append(candles_1h[i:i+chunk])
    for g in groups:
        if not g: continue
        result.append({
            "time":   g[-1]["time"],
            "close":  g[-1]["close"],
            "volume": sum(c["volume"] for c in g),
        })
    return result

# ── INDICADORES ───────────────────────────────────────────────────
def calc_rvol(candles_15):
    """RVOL mismo horario: vol actual / promedio del mismo slot en últimos 7 días."""
    bpd  = CFG["BARS_PER_DAY"]
    days = CFG["AVG_DAYS"]
    n    = len(candles_15)
    if n < bpd * days + 1:
        return None
    current_vol = candles_15[-1]["volume"]
    slots = [candles_15[-(1 + i * bpd)]["volume"]
             for i in range(1, days + 1) if n - 1 - i * bpd >= 0]
    if not slots:
        return None
    avg = sum(slots) / len(slots)
    return (current_vol / avg) if avg > 0 else None

def calc_heatmap_z(candles_15):
    """Z-score del volumen en 15M."""
    length = CFG["HEAT_LEN"]
    if len(candles_15) < length:
        return 0.0
    vols = [c["volume"] for c in candles_15[-length:]]
    mean = sum(vols) / length
    std  = math.sqrt(sum((v - mean) ** 2 for v in vols) / length)
    return ((candles_15[-1]["volume"] - mean) / std) if std > 0 else 0.0

def heatmap_label(z):
    if z >= CFG["Z_EXTRA_HIGH"]: return "🔴 EXTRA ALTO"
    if z >= CFG["Z_HIGH"]:       return "🟠 ALTO"
    if z <= CFG["Z_LOW"]:        return "🔵 BAJO"
    return "⚪ Normal"

def confirm_vol_4h(candles_4h):
    """Volumen 4H actual >= promedio de los últimos VOL_4H_LEN periodos."""
    length = CFG["VOL_4H_LEN"]
    if len(candles_4h) < length + 1:
        return True
    vols = [c["volume"] for c in candles_4h[-(length + 1):-1]]
    avg  = sum(vols) / len(vols)
    return candles_4h[-1]["volume"] >= avg

def calc_stc_4h(candles_4h):
    """
    STC calculado sobre cierres de 4H — replica exacta del Pine:
      f_stc(close, stcLength, stcFastLen, stcSlowLen, stcFactor)
    Devuelve (stc_current, stc_prev) para detectar cruces de nivel.
    """
    closes = [c["close"] for c in candles_4h]
    n      = len(closes)
    fast   = CFG["STC_FAST"]
    slow   = CFG["STC_SLOW"]
    cycle  = CFG["STC_CYCLE"]
    factor = CFG["STC_FACTOR"]

    if n < slow + cycle * 2 + 2:
        return None, None

    # EMA helper
    def ema_arr(arr, p):
        k   = 2 / (p + 1)
        res = [None] * len(arr)
        last = None
        for i, v in enumerate(arr):
            if last is None:
                res[i] = v; last = v
            else:
                res[i] = v * k + last * (1 - k); last = res[i]
        return res

    ef   = ema_arr(closes, fast)
    es   = ema_arr(closes, slow)
    macd = [(ef[i] - es[i]) if (ef[i] is not None and es[i] is not None) else 0.0
            for i in range(n)]

    # Stoch suavizado con factor (replica nz + var float en Pine)
    def stoch_smooth(src, ln):
        out = [0.0] * len(src)
        f   = [0.0] * len(src)
        for i in range(ln - 1, len(src)):
            w      = src[i - ln + 1:i + 1]
            lo, hi = min(w), max(w)
            raw    = ((src[i] - lo) / (hi - lo) * 100) if hi != lo else 0.0
            f[i]   = raw if i == ln - 1 else f[i-1] + factor * (raw - f[i-1])
            out[i] = f[i]
        return out

    f2      = stoch_smooth(macd,  cycle)
    stc_arr = stoch_smooth(f2,    cycle)

    stc_cur  = stc_arr[-1]
    stc_prev = stc_arr[-2]
    return stc_cur, stc_prev

def detect_stc_events(stc_cur, stc_prev):
    """
    Detecta los 4 eventos del Pine Script:
      crossover(stc4h, 25)    → ciclo alcista iniciando
      crossunder(stc4h, 75)   → ciclo bajista
      crossover(stc4h, 90)    → zona madura alcista (agotamiento)
      crossunder(stc4h, 10)   → zona madura bajista (posible reversión)
    """
    if stc_cur is None or stc_prev is None:
        return {"cross_up_25": False, "cross_dn_75": False,
                "mature_up_90": False, "mature_dn_10": False, "stc_val": None}

    cross_up_25  = stc_prev < 25  and stc_cur >= 25
    cross_dn_75  = stc_prev > 75  and stc_cur <= 75
    mature_up_90 = stc_prev < 90  and stc_cur >= 90
    mature_dn_10 = stc_prev > 10  and stc_cur <= 10

    return {
        "cross_up_25":  cross_up_25,
        "cross_dn_75":  cross_dn_75,
        "mature_up_90": mature_up_90,
        "mature_dn_10": mature_dn_10,
        "stc_val":      round(stc_cur, 1),
    }

# ── ANALISIS POR PAR ──────────────────────────────────────────────
def analyze_pair(pair):
    sym = pair["symbol"]

    c15  = fetch_klines(sym, "15",  CFG["CANDLES_15"])
    c1h  = fetch_klines(sym, "240", CFG["CANDLES_4H"])  # yfinance devuelve 1H
    c4h  = resample_to_4h(c1h)                          # agrupamos a 4H

    closes_4h  = [c["close"] for c in c4h]
    now_utc    = datetime.now(timezone.utc)
    is_weekend = now_utc.weekday() >= 5

    rvol      = calc_rvol(c15)
    z         = calc_heatmap_z(c15)
    ok4h      = confirm_vol_4h(c4h)
    threshold = CFG["RVOL_WEEKEND"] if is_weekend else CFG["RVOL_WEEKDAY"]

    rvol_alert = (rvol is not None) and (rvol >= threshold) and ok4h

    stc_cur, stc_prev = calc_stc_4h(c4h)
    stc_events        = detect_stc_events(stc_cur, stc_prev)

    return {
        "rvol":       round(rvol, 2) if rvol else None,
        "threshold":  threshold,
        "z":          round(z, 2),
        "z_label":    heatmap_label(z),
        "ok4h":       ok4h,
        "rvol_alert": rvol_alert,
        "is_weekend": is_weekend,
        "price":      c15[-1]["close"],
        **stc_events,
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
    topic = os.environ.get("RVOL_NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}",
                          data=body.encode("utf-8"),
                          headers={"Title": title, "Priority": priority, "Tags": tags},
                          timeout=10)
        except Exception as e: print("Error ntfy:", e)

    bot  = os.environ.get("RVOL_BOT_TOKEN")
    chat = os.environ.get("RVOL_CHAT_ID")
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
    state       = load_state()
    new_state   = {}
    alerts_sent = 0
    now_utc     = datetime.now(timezone.utc)
    day_label   = "🗓 FIN DE SEMANA" if now_utc.weekday() >= 5 else "📅 ENTRE SEMANA"

    for pair in PAIRS:
        sym = pair["symbol"]
        try:
            data = analyze_pair(pair)
        except Exception as e:
            print(f"Error en {sym}: {e}")
            continue

        prev = state.get(sym, {
            "rvol_alert": False,
            "cross_up_25": False, "cross_dn_75": False,
            "mature_up_90": False, "mature_dn_10": False,
        })

        stc_str = f"STC 4H: {data['stc_val']}" if data["stc_val"] is not None else ""

        # ── 1. RVOL Alto ─────────────────────────────────────────
        if data["rvol_alert"] and not prev.get("rvol_alert"):
            rvol_str = f"{data['rvol']}x" if data["rvol"] else "—"
            day_str  = "Fin de semana" if data["is_weekend"] else "Entre semana"
            notify(
                f"🟥 PRIORIDAD ALTA · RVOL Alto · {pair['name']}",
                f"⚠️ Volumen relativo inusual detectado\n"
                f"📊 RVOL: *{rvol_str}* (umbral {day_str}: {data['threshold']}x)\n"
                f"🔥 Heatmap: {data['z_label']} (z={data['z']})\n"
                f"4H vol confirmado: {'✅' if data['ok4h'] else '❌'}\n"
                f"{stc_str}\n"
                f"💰 Precio: ${data['price']:.4f}\n"
                f"⚠️ NO es señal Trifecta · Revisar manualmente\n"
                f"🕐 {day_label}",
                priority="urgent",
                tags="bar_chart,rotating_light",
            )
            alerts_sent += 1
            print(f"RVOL ALERT: {pair['name']} RVOL={rvol_str}")

        # ── 2. RVOL Normalizado ───────────────────────────────────
        if prev.get("rvol_alert") and not data["rvol_alert"]:
            notify(
                f"📉 RVOL Normalizado · {pair['name']}",
                f"Volumen relativo volvió a nivel normal\n"
                f"RVOL: {data['rvol']}x · Umbral: {data['threshold']}x\n"
                f"💰 Precio: ${data['price']:.4f}",
                priority="low",
                tags="bar_chart",
            )
            alerts_sent += 1

        # ── 3. STC 4H cruza 25 hacia arriba (ciclo alcista) ──────
        if data["cross_up_25"] and not prev.get("cross_up_25"):
            notify(
                f"🟥 PRIORIDAD ALTA · STC 4H cruza 25 ↑ · {pair['name']}",
                f"🟢 *Ciclo alcista iniciando en 4H*\n"
                f"STC cruzó el nivel 25 hacia arriba\n"
                f"Poner atención en Longs de 15M\n"
                f"💰 Precio: ${data['price']:.4f} · {stc_str}\n"
                f"🕐 {day_label}",
                priority="high",
                tags="green_circle,chart_with_upwards_trend",
            )
            alerts_sent += 1
            print(f"STC 4H CROSS UP 25: {pair['name']} STC={data['stc_val']}")

        # ── 4. STC 4H cruza 75 hacia abajo (ciclo bajista) ───────
        if data["cross_dn_75"] and not prev.get("cross_dn_75"):
            notify(
                f"🟥 PRIORIDAD ALTA · STC 4H cruza 75 ↓ · {pair['name']}",
                f"🔴 *Ciclo bajista en 4H*\n"
                f"STC cruzó el nivel 75 hacia abajo\n"
                f"Poner atención en Shorts o cerrar runners\n"
                f"💰 Precio: ${data['price']:.4f} · {stc_str}\n"
                f"🕐 {day_label}",
                priority="high",
                tags="red_circle,chart_with_downwards_trend",
            )
            alerts_sent += 1
            print(f"STC 4H CROSS DN 75: {pair['name']} STC={data['stc_val']}")

        # ── 5. STC 4H zona madura >90 (agotamiento alcista) ──────
        if data["mature_up_90"] and not prev.get("mature_up_90"):
            notify(
                f"🟨 PRIORIDAD MEDIA · STC 4H >90 · {pair['name']}",
                f"🔴 *Movimiento 4H agotado (alcista)*\n"
                f"STC cruzó el nivel 90 hacia arriba\n"
                f"Reducir tamaño en señales 15M\n"
                f"💰 Precio: ${data['price']:.4f} · {stc_str}\n"
                f"🕐 {day_label}",
                priority="default",
                tags="warning,chart_with_downwards_trend",
            )
            alerts_sent += 1
            print(f"STC 4H MATURE >90: {pair['name']} STC={data['stc_val']}")

        # ── 6. STC 4H zona madura <10 (agotamiento bajista) ──────
        if data["mature_dn_10"] and not prev.get("mature_dn_10"):
            notify(
                f"🟨 PRIORIDAD MEDIA · STC 4H <10 · {pair['name']}",
                f"🟢 *Movimiento bajista agotado en 4H*\n"
                f"STC cruzó el nivel 10 hacia abajo\n"
                f"Posible reversión pronto\n"
                f"💰 Precio: ${data['price']:.4f} · {stc_str}\n"
                f"🕐 {day_label}",
                priority="default",
                tags="warning,chart_with_upwards_trend",
            )
            alerts_sent += 1
            print(f"STC 4H MATURE <10: {pair['name']} STC={data['stc_val']}")

        new_state[sym] = {
            "rvol_alert":  data["rvol_alert"],
            "cross_up_25": data["cross_up_25"],
            "cross_dn_75": data["cross_dn_75"],
            "mature_up_90":data["mature_up_90"],
            "mature_dn_10":data["mature_dn_10"],
        }

    save_state(new_state)
    print(f"RVOL chequeo completo ({now_utc.isoformat()}). Alertas: {alerts_sent}")

if __name__ == "__main__":
    main()
