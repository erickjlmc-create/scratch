"""
Sistema Trifecta Pro v2 - Bot de notificaciones
Replica EXACTAMENTE la logica de TrifectaPro_Dashboard_v2.html
(Supertrend + cruce EMA 9/21 + MFI, guardia StochRSI, filtros ADX/ATR/Volumen,
 contexto EMA50 4H, ventana de sesion NY) y avisa por ntfy.sh y/o Telegram
 cuando aparece una senal nueva (A++/A+/B) o cuando una senal valida queda
 bloqueada por sobreextension.

Corre sin costo en GitHub Actions (cron). No requiere API key de Bybit:
usa los mismos endpoints publicos que ya usa el dashboard.
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG (igual que CFG en el dashboard) ───────────────────────────
PAIRS = [
    {"symbol": "SOLUSDT",  "name": "SOL/USDT"},
    {"symbol": "ETHUSDT",  "name": "ETH/USDT"},
    {"symbol": "BNBUSDT",  "name": "BNB/USDT"},
    {"symbol": "AVAXUSDT", "name": "AVAX/USDT"},
    {"symbol": "LINKUSDT", "name": "LINK/USDT"},
    {"symbol": "DOTUSDT",  "name": "DOT/USDT"},
    {"symbol": "NEARUSDT", "name": "NEAR/USDT"},
    {"symbol": "ARBUSDT",  "name": "ARB/USDT"},
]

CFG = {
    "ST_PERIOD": 9, "ST_FACTOR": 3.0,
    "EMA_FAST": 9, "EMA_SLOW": 21,
    "MFI_PERIOD": 14,
    "SRSI_RSI": 14, "SRSI_STOCH": 14, "SRSI_K": 3, "SRSI_D": 3,
    "SRSI_OB": 85, "SRSI_OS": 15,
    "ADX_PERIOD": 14, "ADX_MIN": 25,
    "ATR_PERIOD": 14, "ATR_AVG": 20,
    "VOL_AVG": 20, "EMA100": 100, "EMA50_4H": 50,
    "CANDLES": 200, "CANDLES_4H": 80,
}

GT_OFFSET_HOURS = -6   # Guatemala = UTC-6
SESSION_START = 7
SESSION_END = 13

# Si SESSION_ONLY=true (variable de entorno), el bot vuelve a limitarse a la
# ventana 07:00-13:00 GT del manual. Por defecto corre 24/7.
SESSION_ONLY = os.environ.get("SESSION_ONLY", "false").lower() == "true"

BYBIT_BASE = "https://api.bybit.com/v5/market"
BINANCE_BASE = "https://api.binance.com/api/v3"
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


# ── SESION ─────────────────────────────────────────────────────────
def is_in_session():
    if not SESSION_ONLY:
        return True
    now_utc = datetime.now(timezone.utc)
    gt = now_utc + timedelta(hours=GT_OFFSET_HOURS)
    return SESSION_START <= gt.hour < SESSION_END


# ── DATOS (Bybit publico, sin API key) ───────────────────────────────
def fetch_klines(symbol, interval, limit):
    interval_map = {"15": "15m", "240": "4h", "D": "1d"}
    binance_interval = interval_map.get(interval, interval + "m")
    r = requests.get(
        f"{BINANCE_BASE}/klines",
        params={"symbol": symbol, "interval": binance_interval, "limit": limit},
        timeout=15,
    )
    try:
        d = r.json()
    except Exception:
        print(f"[DEBUG fetch_klines {symbol}] HTTP {r.status_code} — respuesta: {r.text[:300]}")
        raise
    print(f"[DEBUG {symbol} {interval}] tipo={type(d).__name__} contenido={str(d)[:150]}")
    if isinstance(d, dict) and d.get("code"):
        raise RuntimeError(d.get("msg"))
    if not isinstance(d, list):
        raise RuntimeError(f"Respuesta inesperada: {str(d)[:200]}")
    return [
        {"time": int(c[0]), "open": float(c[1]), "high": float(c[2]),
         "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])}
        for c in d
    ]


def fetch_ticker(symbol):
    r = requests.get(
        f"{BINANCE_BASE}/ticker/price",
        params={"symbol": symbol},
        timeout=15,
    )
    try:
        d = r.json()
        return {"lastPrice": d["price"]}
    except Exception:
        print(f"[DEBUG fetch_ticker {symbol}] HTTP {r.status_code} — respuesta: {r.text[:300]}")
        raise


# ── INDICADORES (mismo algoritmo que el dashboard) ───────────────────
def sma(arr, p):
    res = [None] * len(arr)
    for i in range(len(arr)):
        if i < p - 1:
            continue
        window = arr[i - p + 1:i + 1]
        res[i] = sum((v or 0) for v in window) / p
    return res


def ema(arr, p):
    k = 2 / (p + 1)
    res = [None] * len(arr)
    started, last = False, None
    for i, v in enumerate(arr):
        if v is None:
            continue
        if not started:
            res[i] = v
            last = v
            started = True
            continue
        res[i] = v * k + last * (1 - k)
        last = res[i]
    return res


def calc_atr(candles, p):
    n = len(candles)
    tr = [0.0] * n
    for i in range(n):
        if i == 0:
            tr[i] = candles[i]["high"] - candles[i]["low"]
        else:
            prev = candles[i - 1]
            tr[i] = max(
                candles[i]["high"] - candles[i]["low"],
                abs(candles[i]["high"] - prev["close"]),
                abs(candles[i]["low"] - prev["close"]),
            )
    res = [None] * n
    if n < p:
        return res
    s = sum(tr[0:p]) / p
    res[p - 1] = s
    for i in range(p, n):
        res[i] = (res[i - 1] * (p - 1) + tr[i]) / p
    return res


def calc_supertrend(candles, p, f):
    n = len(candles)
    atr = calc_atr(candles, p)
    trend = [1] * n
    up = dn = None
    for i in range(p, n):
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2
        a = atr[i] or 0
        nu, nd = hl2 - f * a, hl2 + f * a
        pc = candles[i - 1]["close"]
        up = max(nu, up) if (up is not None and pc > up) else nu
        dn = min(nd, dn) if (dn is not None and pc < dn) else nd
        if trend[i - 1] == 1:
            trend[i] = -1 if candles[i]["close"] < up else 1
        else:
            trend[i] = 1 if candles[i]["close"] > dn else -1
    return trend


def calc_rsi(closes, p):
    n = len(closes)
    res = [None] * n
    if n < p + 1:
        return res
    gain_avg = loss_avg = 0.0
    for i in range(1, p + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gain_avg += d
        else:
            loss_avg -= d
    gain_avg /= p
    loss_avg /= p
    res[p] = 100 if loss_avg == 0 else 100 - 100 / (1 + gain_avg / loss_avg)
    for i in range(p + 1, n):
        d = closes[i] - closes[i - 1]
        gain_avg = (gain_avg * (p - 1) + max(d, 0)) / p
        loss_avg = (loss_avg * (p - 1) + max(-d, 0)) / p
        res[i] = 100 if loss_avg == 0 else 100 - 100 / (1 + gain_avg / loss_avg)
    return res


def calc_stochrsi(closes, rl, sl, ks, ds):
    rsi = calc_rsi(closes, rl)
    n = len(closes)
    stoch = [None] * n
    for i in range(rl + sl - 1, n):
        window = [v for v in rsi[i - sl + 1:i + 1] if v is not None]
        if len(window) < sl:
            continue
        lo, hi = min(window), max(window)
        stoch[i] = 50 if hi == lo else ((rsi[i] - lo) / (hi - lo)) * 100
    k_arr = sma(stoch, ks)
    d_arr = sma([v if v is not None else 0 for v in k_arr], ds)
    last = n - 1
    k_val = k_arr[last] if k_arr[last] is not None else 50
    return k_val


def calc_mfi(candles, p):
    n = len(candles)
    tp = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
    mf = [tp[i] * candles[i]["volume"] for i in range(n)]
    res = [None] * n
    for i in range(p, n):
        pos = neg = 0.0
        for j in range(i - p + 1, i + 1):
            if j == 0:
                continue
            if tp[j] > tp[j - 1]:
                pos += mf[j]
            else:
                neg += mf[j]
        res[i] = 100 if neg == 0 else 100 - 100 / (1 + pos / neg)
    return res


def calc_adx(candles, p):
    n = len(candles)
    if n < 2:
        return 0
    tr, dm_p, dm_m = [], [], []
    for i in range(1, n):
        hi = candles[i]["high"] - candles[i - 1]["high"]
        lo = candles[i - 1]["low"] - candles[i]["low"]
        tr.append(max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        ))
        dm_p.append(hi if (hi > lo and hi > 0) else 0)
        dm_m.append(lo if (lo > hi and lo > 0) else 0)
    if len(tr) < p:
        return 0
    atr = sum(tr[0:p])
    p14 = sum(dm_p[0:p])
    m14 = sum(dm_m[0:p])
    dx = []
    for i in range(p, len(tr)):
        atr = atr - atr / p + tr[i]
        p14 = p14 - p14 / p + dm_p[i]
        m14 = m14 - m14 / p + dm_m[i]
        di_p = (p14 / atr) * 100 if atr else 0
        di_m = (m14 / atr) * 100 if atr else 0
        diff, s = abs(di_p - di_m), di_p + di_m
        dx.append((diff / s) * 100 if s else 0)
    if len(dx) < p:
        return 0
    adx = sum(dx[0:p]) / p
    for i in range(p, len(dx)):
        adx = (adx * (p - 1) + dx[i]) / p
    return adx


# ── ANALISIS POR PAR ───────────────────────────────────────────────
def analyze_pair(pair):
    symbol = pair["symbol"]
    c15 = fetch_klines(symbol, "15", CFG["CANDLES"])
    c4h = fetch_klines(symbol, "240", CFG["CANDLES_4H"])
    ticker = fetch_ticker(symbol)

    closes = [c["close"] for c in c15]
    vols = [c["volume"] for c in c15]
    n = len(c15) - 1

    st_trend = calc_supertrend(c15, CFG["ST_PERIOD"], CFG["ST_FACTOR"])
    st_bull, st_bear = st_trend[n] == 1, st_trend[n] == -1

    ema9_arr = ema(closes, CFG["EMA_FAST"])
    ema21_arr = ema(closes, CFG["EMA_SLOW"])
    ema9, ema21 = ema9_arr[n], ema21_arr[n]
    ema_bull, ema_bear = ema9 > ema21, ema9 < ema21

    mfi_arr = calc_mfi(c15, CFG["MFI_PERIOD"])
    mfi_val = mfi_arr[n] if mfi_arr[n] is not None else 50
    mfi_bull, mfi_bear = mfi_val > 50, mfi_val < 50

    k_val = calc_stochrsi(closes, CFG["SRSI_RSI"], CFG["SRSI_STOCH"], CFG["SRSI_K"], CFG["SRSI_D"])
    srsi_blocks_long = k_val > CFG["SRSI_OB"]
    srsi_blocks_short = k_val < CFG["SRSI_OS"]

    ema100_arr = ema(closes, CFG["EMA100"])
    ema100 = ema100_arr[n]
    price_above, price_below = closes[n] > ema100, closes[n] < ema100

    adx_val = calc_adx(c15, CFG["ADX_PERIOD"])
    adx_ok = adx_val > CFG["ADX_MIN"]

    atr_arr = calc_atr(c15, CFG["ATR_PERIOD"])
    atr_sma = sma(atr_arr, CFG["ATR_AVG"])
    atr_ok = (atr_arr[n] or 0) > (atr_sma[n] or 0)

    vol_sma = sma(vols, CFG["VOL_AVG"])
    vol_ok = vols[n] > (vol_sma[n] or 0)

    closes4h = [c["close"] for c in c4h]
    ema50_4h_arr = ema(closes4h, CFG["EMA50_4H"])
    above4h = closes[n] > ema50_4h_arr[-1]
    below4h = closes[n] < ema50_4h_arr[-1]

    in_session = is_in_session()

    bull_p = sum([st_bull, ema_bull, mfi_bull])
    bear_p = sum([st_bear, ema_bear, mfi_bear])

    bull_ok = price_above and adx_ok and atr_ok and vol_ok and not srsi_blocks_long and in_session
    bear_ok = price_below and adx_ok and atr_ok and vol_ok and not srsi_blocks_short and in_session

    is_app_long = bull_p == 3 and bull_ok and above4h
    is_ap_long = bull_p >= 2 and bull_ok and above4h
    is_b_long = bull_p >= 2 and adx_ok and atr_ok and not srsi_blocks_long and above4h and in_session

    is_app_short = bear_p == 3 and bear_ok and below4h
    is_ap_short = bear_p >= 2 and bear_ok and below4h
    is_b_short = bear_p >= 2 and adx_ok and atr_ok and not srsi_blocks_short and below4h and in_session

    would_be_long = bull_p >= 2 and price_above and adx_ok and atr_ok and above4h and in_session
    would_be_short = bear_p >= 2 and price_below and adx_ok and atr_ok and below4h and in_session
    blocked = (would_be_long and srsi_blocks_long) or (would_be_short and srsi_blocks_short)

    if is_app_long or is_app_short:
        grade = "App"
    elif is_ap_long or is_ap_short:
        grade = "Ap"
    elif is_b_long or is_b_short:
        grade = "B"
    elif blocked:
        grade = "blocked"
    else:
        grade = "none"

    if is_app_long or is_ap_long or is_b_long:
        direction = "long"
    elif is_app_short or is_ap_short or is_b_short:
        direction = "short"
    elif blocked:
        direction = "blocked"
    else:
        direction = "none"

    return {
        "grade": grade, "direction": direction, "blocked": blocked,
        "price": float(ticker["lastPrice"]),
        "bull_p": bull_p, "bear_p": bear_p,
        "k": round(k_val), "adx": round(adx_val),
        "st_direction": "bull" if st_bull else "bear",
    }


# ── ESTADO (para no repetir la misma alerta cada corrida) ────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── NOTIFICACIONES (gratis) ───────────────────────────────────────────
def notify(title, body, priority="default", tags="rotating_light"):
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(
                f"https://ntfy.sh/{topic}",
                data=body.encode("utf-8"),
                headers={"Title": title, "Priority": priority, "Tags": tags},
                timeout=10,
            )
        except Exception as e:
            print("Error enviando ntfy:", e)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": f"{title}\n{body}"},
                timeout=10,
            )
        except Exception as e:
            print("Error enviando Telegram:", e)

    if not topic and not (bot_token and chat_id):
        print(f"[Sin canal configurado] {title} — {body}")


# ── MAIN ───────────────────────────────────────────────────────────
def main():
    if not is_in_session():
        print("Fuera de sesion NY, no se revisan pares.")
        return

    state = load_state()
    new_state = {}
    alerts_sent = 0
    grade_label = {"App": "A++", "Ap": "A+", "B": "B"}

    for pair in PAIRS:
        symbol = pair["symbol"]
        try:
            data = analyze_pair(pair)
        except Exception as e:
            print(f"Error en {symbol}: {e}")
            continue

        prev = state.get(symbol, {"grade": "none", "blocked": False, "st_direction": None})
        grade = data["grade"]

        # ── Alerta cambio de color Supertrend ──────────────────────────
        st_now = data["st_direction"]
        st_prev = prev.get("st_direction")
        if st_prev is not None and st_now != st_prev:
            if st_now == "bull":
                notify(
                    f"🟢 Supertrend VERDE · {pair['name']}",
                    f"Supertrend cambio a ALCISTA en 15M · ${data['price']}",
                    priority="high", tags="green_circle,chart_with_upwards_trend",
                )
            else:
                notify(
                    f"🔴 Supertrend ROJO · {pair['name']}",
                    f"Supertrend cambio a BAJISTA en 15M · ${data['price']}",
                    priority="high", tags="red_circle,chart_with_downwards_trend",
                )
            alerts_sent += 1
            print(f"SUPERTREND FLIP: {pair['name']} → {st_now.upper()} · ${data['price']}")

        if grade not in ("none", "blocked") and grade != prev.get("grade"):
            title = f"{grade_label[grade]} {data['direction'].upper()} · {pair['name']}"
            body = (f"{max(data['bull_p'], data['bear_p'])}/3 pilares · "
                    f"ADX {data['adx']} · ${data['price']}")
            tags = "rotating_light," + ("chart_with_upwards_trend" if data["direction"] == "long" else "chart_with_downwards_trend")
            notify(title, body, priority="urgent" if grade == "App" else "high", tags=tags)
            alerts_sent += 1
            print(f"ALERTA: {title} — {body}")

        if data["blocked"] and not prev.get("blocked"):
            notify(
                f"⚠ Bloqueado · {pair['name']}",
                f"Setup valido pero StochRSI K:{data['k']} en sobreextension. Esperar reset.",
                priority="low", tags="warning",
            )
            alerts_sent += 1

        new_state[symbol] = {"grade": grade, "blocked": data["blocked"], "st_direction": data["st_direction"]}

    save_state(new_state)
    print(f"Chequeo completo ({datetime.now(timezone.utc).isoformat()}). Alertas enviadas: {alerts_sent}")


if __name__ == "__main__":
    main()
