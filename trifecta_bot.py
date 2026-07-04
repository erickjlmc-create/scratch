"""
Sistema Trifecta Pro v4 - Bot de notificaciones
Replica TrifectaPro_Dashboard_v4.html y TrifectaPro_Scanner_v4.pine

NOVEDADES v4:
- STC (Schaff Trend Cycle) como filtro de ciclo obligatorio
- Williams %R como segunda guardia de sobreextension
- EMA 200 reemplaza EMA 100 en 15M
- Franja Precaucion (Asia y Post-NY): señal valida con 50% riesgo
- STC Maduro (aviso de agotamiento cuando STC >= 95 o <= 5)
- Matriz horaria completa con 7 franjas Guatemala

SIEMPRE notifica cuando Supertrend cambia de color en cualquier par.

Corre gratis en GitHub Actions. Datos via yfinance (sin geo-bloqueo).
"""

import os
import json
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

# ── CONFIG (igual que v4 dashboard) ─────────────────────────────────
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
    # Pilares
    "ST_PERIOD": 10, "ST_FACTOR": 3.0,
    "EMA_FAST": 9,   "EMA_SLOW": 21,
    "MFI_PERIOD": 14,
    # STC
    "STC_CYCLE": 10, "STC_FAST": 23, "STC_SLOW": 50,
    "STC_BULL": 25,  "STC_BEAR": 75,
    # Guardia 1: StochRSI
    "SRSI_RSI": 14, "SRSI_STOCH": 14, "SRSI_K": 3, "SRSI_D": 3,
    "SRSI_OB": 85,  "SRSI_OS": 15,
    # Guardia 2: Williams %R
    "WR_LEN": 14, "WR_OB": -20, "WR_OS": -80,
    # Filtros
    "ADX_PERIOD": 14, "ADX_MIN": 25,
    "ATR_PERIOD": 14, "ATR_AVG": 20,
    "VOL_AVG": 20,
    "EMA200": 200,   # reemplaza EMA100
    "EMA50_4H": 50,
    "CANDLES": 250, "CANDLES_4H": 100,
}

GT_OFFSET = -6   # Guatemala = UTC-6
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

YF_SYMBOLS = {
    "SOLUSDT":  "SOL-USD",
    "ETHUSDT":  "ETH-USD",
    "BNBUSDT":  "BNB-USD",
    "AVAXUSDT": "AVAX-USD",
    "LINKUSDT": "LINK-USD",
    "DOTUSDT":  "DOT-USD",
    "NEARUSDT": "NEAR-USD",
    "ARBUSDT":  "ARB-USD",
}


# ── FRANJA HORARIA GUATEMALA ─────────────────────────────────────────
def get_session():
    """Retorna la franja horaria actual segun la logica v4."""
    utc_min = datetime.now(timezone.utc).hour * 60 + datetime.now(timezone.utc).minute
    if  360 <= utc_min <  660: return "dead1"    # 00-05 GT
    if  660 <= utc_min <  720: return "asia"     # 05-06 GT  (precaucion)
    if  720 <= utc_min <  780: return "london"   # 06-07 GT  (viable)
    if  780 <= utc_min < 1020: return "ny"       # 07-11 GT  (prime)
    if 1020 <= utc_min < 1170: return "ny_late"  # 11-13:30 GT (tardio)
    if 1170 <= utc_min < 1380: return "post"     # 13:30-17 GT (precaucion)
    return "dead2"                               # 17-23:59 GT

SESSION_META = {
    "dead1":   {"operable": False, "caution": False, "label": "☠ ZONA MUERTA"},
    "asia":    {"operable": False, "caution": True,  "label": "⚠ ASIA PRECAUCION"},
    "london":  {"operable": True,  "caution": False, "label": "★ APERTURA LONDRES"},
    "ny":      {"operable": True,  "caution": False, "label": "✓ NY PRIME"},
    "ny_late": {"operable": True,  "caution": False, "label": "★ NY TARDIO"},
    "post":    {"operable": False, "caution": True,  "label": "⚠ POST-NY"},
    "dead2":   {"operable": False, "caution": False, "label": "☠ BOTS/MUERTA"},
}


# ── DATOS via yfinance ───────────────────────────────────────────────
def fetch_klines(symbol, interval, limit):
    yf_sym = YF_SYMBOLS[symbol]
    if interval == "15":
        yf_interval, period = "15m", "59d"
    elif interval == "240":
        yf_interval, period = "1h", "729d"
    else:
        yf_interval, period = "1d", "2y"

    df = yf.download(yf_sym, period=period, interval=yf_interval,
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.tail(limit).copy()
    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "time":   int(ts.timestamp() * 1000),
            "open":   float(row["Open"]),
            "high":   float(row["High"]),
            "low":    float(row["Low"]),
            "close":  float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    return candles


def fetch_ticker(symbol):
    info = yf.Ticker(YF_SYMBOLS[symbol]).fast_info
    return {"lastPrice": str(round(float(info.last_price), 6))}


# ── INDICADORES ──────────────────────────────────────────────────────
def ema(arr, p):
    k = 2 / (p + 1)
    res, last, started = [None]*len(arr), None, False
    for i, v in enumerate(arr):
        if v is None: continue
        if not started:
            res[i] = v; last = v; started = True; continue
        res[i] = v * k + last * (1 - k); last = res[i]
    return res

def sma(arr, p):
    res = [None]*len(arr)
    for i in range(len(arr)):
        if i < p - 1: continue
        w = arr[i-p+1:i+1]
        res[i] = sum((v or 0) for v in w) / p
    return res

def calc_atr(candles, p):
    n = len(candles); tr = [0.0]*n
    for i in range(n):
        if i == 0: tr[i] = candles[i]["high"] - candles[i]["low"]
        else:
            prev = candles[i-1]
            tr[i] = max(candles[i]["high"]-candles[i]["low"],
                        abs(candles[i]["high"]-prev["close"]),
                        abs(candles[i]["low"]-prev["close"]))
    res = [None]*n
    if n < p: return res
    res[p-1] = sum(tr[:p]) / p
    for i in range(p, n):
        res[i] = (res[i-1]*(p-1) + tr[i]) / p
    return res

def calc_supertrend(candles, p, f):
    n = len(candles); atr = calc_atr(candles, p)
    trend = [1]*n; up = dn = None
    for i in range(p, n):
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2
        a = atr[i] or 0
        nu, nd = hl2 - f*a, hl2 + f*a
        pc = candles[i-1]["close"]
        up = max(nu, up) if (up is not None and pc > up) else nu
        dn = min(nd, dn) if (dn is not None and pc < dn) else nd
        if trend[i-1] == 1:
            trend[i] = -1 if candles[i]["close"] < up else 1
        else:
            trend[i] = 1 if candles[i]["close"] > dn else -1
    return trend

def calc_rsi(closes, p):
    n = len(closes); res = [None]*n
    if n < p+1: return res
    g = l = 0.0
    for i in range(1, p+1):
        d = closes[i]-closes[i-1]
        if d > 0: g += d
        else: l -= d
    g /= p; l /= p
    res[p] = 100 if l == 0 else 100 - 100/(1+g/l)
    for i in range(p+1, n):
        d = closes[i]-closes[i-1]
        g = (g*(p-1)+max(d,0))/p; l = (l*(p-1)+max(-d,0))/p
        res[i] = 100 if l == 0 else 100 - 100/(1+g/l)
    return res

def calc_stochrsi_k(closes, rl, sl, ks, ds):
    rsi = calc_rsi(closes, rl); n = len(closes)
    stoch = [None]*n
    for i in range(rl+sl-1, n):
        w = [v for v in rsi[i-sl+1:i+1] if v is not None]
        if len(w) < sl: continue
        lo, hi = min(w), max(w)
        stoch[i] = 50 if hi==lo else ((rsi[i]-lo)/(hi-lo))*100
    k = sma(stoch, ks)
    last = n-1
    return k[last] if k[last] is not None else 50

def calc_mfi(candles, p):
    n = len(candles)
    tp = [(c["high"]+c["low"]+c["close"])/3 for c in candles]
    mf = [tp[i]*candles[i]["volume"] for i in range(n)]
    res = [None]*n
    for i in range(p, n):
        pos = neg = 0.0
        for j in range(i-p+1, i+1):
            if j == 0: continue
            if tp[j] > tp[j-1]: pos += mf[j]
            else: neg += mf[j]
        res[i] = 100 if neg==0 else 100 - 100/(1+pos/neg)
    return res

def calc_williams_r(candles, p):
    n = len(candles)
    if n < p: return -50
    highs = [c["high"]  for c in candles[-p:]]
    lows  = [c["low"]   for c in candles[-p:]]
    close = candles[-1]["close"]
    hh, ll = max(highs), min(lows)
    if hh == ll: return -50
    return ((hh - close) / (hh - ll)) * -100

def calc_adx(candles, p):
    n = len(candles)
    if n < 2: return 0
    tr, dm_p, dm_m = [], [], []
    for i in range(1, n):
        hi = candles[i]["high"]-candles[i-1]["high"]
        lo = candles[i-1]["low"]-candles[i]["low"]
        tr.append(max(candles[i]["high"]-candles[i]["low"],
                      abs(candles[i]["high"]-candles[i-1]["close"]),
                      abs(candles[i]["low"]-candles[i-1]["close"])))
        dm_p.append(hi if (hi>lo and hi>0) else 0)
        dm_m.append(lo if (lo>hi and lo>0) else 0)
    if len(tr) < p: return 0
    atr_v = sum(tr[:p]); p14 = sum(dm_p[:p]); m14 = sum(dm_m[:p])
    dx = []
    for i in range(p, len(tr)):
        atr_v = atr_v-atr_v/p+tr[i]; p14 = p14-p14/p+dm_p[i]; m14 = m14-m14/p+dm_m[i]
        di_p = (p14/atr_v)*100 if atr_v else 0
        di_m = (m14/atr_v)*100 if atr_v else 0
        diff, s = abs(di_p-di_m), di_p+di_m
        dx.append((diff/s)*100 if s else 0)
    if len(dx) < p: return 0
    adx_v = sum(dx[:p])/p
    for i in range(p, len(dx)):
        adx_v = (adx_v*(p-1)+dx[i])/p
    return adx_v

def calc_stc(closes, cycle, fast, slow):
    """Schaff Trend Cycle."""
    n = len(closes)
    ema_f = ema(closes, fast)
    ema_s = ema(closes, slow)
    macd  = [(ema_f[i] - ema_s[i]) if (ema_f[i] is not None and ema_s[i] is not None) else 0
             for i in range(n)]

    def stoch1(src, ln):
        res = [0.0]*len(src)
        for i in range(ln-1, len(src)):
            w = src[i-ln+1:i+1]
            lo, hi = min(w), max(w)
            res[i] = ((src[i]-lo)/(hi-lo)*100) if hi != lo else 0
        return res

    f1 = stoch1(macd, cycle)
    pf = [0.0]*n
    for i in range(n):
        pf[i] = f1[i] if i==0 else pf[i-1] + 0.5*(f1[i]-pf[i-1])

    f2 = stoch1(pf, cycle)
    pff = [0.0]*n
    for i in range(n):
        pff[i] = f2[i] if i==0 else pff[i-1] + 0.5*(f2[i]-pff[i-1])

    return pff[-1]   # valor actual


# ── ANALISIS POR PAR ─────────────────────────────────────────────────
def analyze_pair(pair):
    symbol = pair["symbol"]
    c15  = fetch_klines(symbol, "15",  CFG["CANDLES"])
    c4h  = fetch_klines(symbol, "240", CFG["CANDLES_4H"])
    ticker = fetch_ticker(symbol)

    closes = [c["close"]  for c in c15]
    vols   = [c["volume"] for c in c15]
    n = len(c15) - 1

    # ── Pilar 1: Supertrend ──────────────────────────────────────────
    st_trend = calc_supertrend(c15, CFG["ST_PERIOD"], CFG["ST_FACTOR"])
    st_bull  = st_trend[n] == 1
    st_bear  = st_trend[n] == -1

    # ── Pilar 2: EMA 9/21 ────────────────────────────────────────────
    ema9_a  = ema(closes, CFG["EMA_FAST"])
    ema21_a = ema(closes, CFG["EMA_SLOW"])
    ema_bull = ema9_a[n] > ema21_a[n]
    ema_bear = ema9_a[n] < ema21_a[n]

    # ── Pilar 3: MFI ─────────────────────────────────────────────────
    mfi_a    = calc_mfi(c15, CFG["MFI_PERIOD"])
    mfi_val  = mfi_a[n] if mfi_a[n] is not None else 50
    mfi_bull = mfi_val > 50
    mfi_bear = mfi_val < 50

    # ── STC (filtro de ciclo) ────────────────────────────────────────
    stc_val     = calc_stc(closes, CFG["STC_CYCLE"], CFG["STC_FAST"], CFG["STC_SLOW"])
    stc_bull    = stc_val > CFG["STC_BULL"]
    stc_bear    = stc_val < CFG["STC_BEAR"]
    stc_mature_l = stc_val >= 95
    stc_mature_s = stc_val <= 5
    stc_ok_bull  = stc_bull  and not stc_mature_l
    stc_ok_bear  = stc_bear  and not stc_mature_s

    # ── Guardia 1: StochRSI ──────────────────────────────────────────
    k_val            = calc_stochrsi_k(closes, CFG["SRSI_RSI"], CFG["SRSI_STOCH"],
                                        CFG["SRSI_K"], CFG["SRSI_D"])
    block_long_srsi  = k_val > CFG["SRSI_OB"]
    block_short_srsi = k_val < CFG["SRSI_OS"]

    # ── Guardia 2: Williams %R ───────────────────────────────────────
    wr_val          = calc_williams_r(c15, CFG["WR_LEN"])
    block_long_wr   = wr_val > CFG["WR_OB"]
    block_short_wr  = wr_val < CFG["WR_OS"]

    # Guardias combinadas
    block_l = block_long_srsi  or block_long_wr
    block_s = block_short_srsi or block_short_wr

    # ── EMA 200 en 15M ───────────────────────────────────────────────
    ema200_a    = ema(closes, CFG["EMA200"])
    price_above = closes[n] > (ema200_a[n] or 0)
    price_below = closes[n] < (ema200_a[n] or 0)

    # ── Filtros ADX / ATR / Volumen ──────────────────────────────────
    adx_val = calc_adx(c15, CFG["ADX_PERIOD"])
    adx_ok  = adx_val > CFG["ADX_MIN"]

    atr_a   = calc_atr(c15, CFG["ATR_PERIOD"])
    atr_sma = sma(atr_a, CFG["ATR_AVG"])
    atr_ok  = (atr_a[n] or 0) > (atr_sma[n] or 0)

    vol_sma = sma(vols, CFG["VOL_AVG"])
    vol_ok  = vols[n] > (vol_sma[n] or 0)

    # ── EMA 50 en 4H ─────────────────────────────────────────────────
    closes4h   = [c["close"] for c in c4h]
    ema50_4h_a = ema(closes4h, CFG["EMA50_4H"])
    above4h    = closes[n] > (ema50_4h_a[-1] or 0)
    below4h    = closes[n] < (ema50_4h_a[-1] or 0)

    # ── Conteo de pilares ─────────────────────────────────────────────
    bull_p = sum([st_bull, ema_bull, mfi_bull])
    bear_p = sum([st_bear, ema_bear, mfi_bear])

    # ── Contexto base ─────────────────────────────────────────────────
    bull_ctx = price_above and adx_ok and atr_ok and vol_ok and above4h and not block_l
    bear_ctx = price_below and adx_ok and atr_ok and vol_ok and below4h and not block_s

    # ── Franja horaria ────────────────────────────────────────────────
    sess = get_session()
    meta = SESSION_META[sess]
    in_operable = meta["operable"]
    in_caution  = meta["caution"]

    # ── Calificacion (misma logica que el Pine v4) ────────────────────
    # A++ = 3/3 pilares + ctx + STC ok + NY Prime o Londres
    app_long  = bull_p == 3 and bull_ctx and stc_ok_bull and sess in ("ny", "london")
    app_short = bear_p == 3 and bear_ctx and stc_ok_bear and sess in ("ny", "london")

    # A+ = 3/3 pilares + ctx + STC ok + cualquier franja operable
    ap_long   = bull_p == 3 and bull_ctx and stc_ok_bull and in_operable and not app_long
    ap_short  = bear_p == 3 and bear_ctx and stc_ok_bear and in_operable and not app_short

    # B = 2/3 pilares + filtros basicos + STC + operable
    b_long    = bull_p >= 2 and adx_ok and atr_ok and stc_ok_bull and above4h and not block_l and in_operable and not ap_long
    b_short   = bear_p >= 2 and adx_ok and atr_ok and stc_ok_bear and below4h and not block_s and in_operable and not ap_short

    # Precaucion = A++ calidad pero en franja caution (Asia / Post-NY)
    prec_long  = bull_p == 3 and bull_ctx and stc_ok_bull and in_caution
    prec_short = bear_p == 3 and bear_ctx and stc_ok_bear and in_caution

    # Bloqueado = setup valido pero guardia activa
    would_long  = (bull_p >= 2 and adx_ok and atr_ok and stc_ok_bull and above4h) and in_operable
    would_short = (bear_p >= 2 and adx_ok and atr_ok and stc_ok_bear and below4h) and in_operable
    blocked_long  = would_long  and block_l
    blocked_short = would_short and block_s

    # STC maduro en señal activa
    stc_warn = ((stc_mature_l and (app_long or ap_long)) or
                (stc_mature_s and (app_short or ap_short)))

    if app_long or app_short:       grade = "App"
    elif ap_long or ap_short:       grade = "Ap"
    elif b_long or b_short:         grade = "B"
    elif prec_long or prec_short:   grade = "prec"
    elif blocked_long or blocked_short: grade = "blocked"
    else:                           grade = "none"

    if app_long or ap_long or b_long or prec_long:     direction = "long"
    elif app_short or ap_short or b_short or prec_short: direction = "short"
    elif blocked_long:   direction = "long"
    elif blocked_short:  direction = "short"
    else:                direction = "none"

    return {
        "grade": grade, "direction": direction,
        "blocked": blocked_long or blocked_short,
        "stc_warn": stc_warn,
        "price": float(ticker["lastPrice"]),
        "bull_p": bull_p, "bear_p": bear_p,
        "k": round(k_val), "wr": round(wr_val),
        "adx": round(adx_val), "stc": round(stc_val),
        "st_direction": "bull" if st_bull else "bear",
        "session": sess, "session_label": meta["label"],
    }


# ── ESTADO ───────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_state(state):
    with open(STATE_FILE, "w") as f: json.dump(state, f, indent=2)


# ── NOTIFICACIONES ────────────────────────────────────────────────────
def notify(title, body, priority="default", tags="rotating_light"):
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}",
                          data=body.encode("utf-8"),
                          headers={"Title": title, "Priority": priority, "Tags": tags},
                          timeout=10)
        except Exception as e: print("Error ntfy:", e)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        try:
            requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                          json={"chat_id": chat_id, "text": f"*{title}*\n{body}",
                                "parse_mode": "Markdown"},
                          timeout=10)
        except Exception as e: print("Error Telegram:", e)

    if not topic and not (bot_token and chat_id):
        print(f"[Sin canal] {title} — {body}")


# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    state = load_state()
    new_state = {}
    alerts_sent = 0

    grade_label = {"App": "A++", "Ap": "A+", "B": "B", "prec": "⚠ PRECAUCIÓN"}

    for pair in PAIRS:
        symbol = pair["symbol"]
        try:
            data = analyze_pair(pair)
        except Exception as e:
            print(f"Error en {symbol}: {e}")
            continue

        prev  = state.get(symbol, {"grade": "none", "blocked": False,
                                    "stc_warn": False, "st_direction": None})
        grade = data["grade"]
        sess_label = data["session_label"]

        # ── 1. Cambio de Supertrend (SIEMPRE notifica) ────────────────
        st_now  = data["st_direction"]
        st_prev = prev.get("st_direction")
        if st_prev is not None and st_now != st_prev:
            if st_now == "bull":
                notify(
                    f"🟢 Supertrend VERDE · {pair['name']}",
                    f"Supertrend cambio a ALCISTA en 15M\n"
                    f"Precio: ${data['price']} · STC: {data['stc']}\n"
                    f"Sesion: {sess_label}",
                    priority="high",
                    tags="green_circle,chart_with_upwards_trend",
                )
            else:
                notify(
                    f"🔴 Supertrend ROJO · {pair['name']}",
                    f"Supertrend cambio a BAJISTA en 15M\n"
                    f"Precio: ${data['price']} · STC: {data['stc']}\n"
                    f"Sesion: {sess_label}",
                    priority="high",
                    tags="red_circle,chart_with_downwards_trend",
                )
            alerts_sent += 1
            print(f"ST FLIP: {pair['name']} → {st_now.upper()} · ${data['price']}")

        # ── 2. Señal nueva A++/A+/B/Precaucion ───────────────────────
        if grade in ("App", "Ap", "B", "prec") and grade != prev.get("grade"):
            gl = grade_label[grade]
            dir_icon = "📈" if data["direction"] == "long" else "📉"
            priority = "urgent" if grade == "App" else "high" if grade in ("Ap", "prec") else "default"
            notify(
                f"{dir_icon} {gl} {data['direction'].upper()} · {pair['name']}",
                f"{max(data['bull_p'], data['bear_p'])}/3 pilares · "
                f"STC {data['stc']} · ADX {data['adx']} · WR {data['wr']}\n"
                f"Precio: ${data['price']} · Sesion: {sess_label}",
                priority=priority,
                tags="rotating_light," + ("chart_with_upwards_trend" if data["direction"]=="long"
                                          else "chart_with_downwards_trend"),
            )
            alerts_sent += 1
            print(f"SEÑAL: {gl} {data['direction'].upper()} {pair['name']} · ${data['price']}")

        # ── 3. Bloqueado (nuevo bloqueo) ──────────────────────────────
        if data["blocked"] and not prev.get("blocked"):
            notify(
                f"🔒 Bloqueado · {pair['name']}",
                f"Setup valido pero guardia activa\n"
                f"StochRSI K:{data['k']} · Williams %R:{data['wr']}\n"
                f"Sesion: {sess_label}",
                priority="low", tags="lock,warning",
            )
            alerts_sent += 1

        # ── 4. STC maduro en señal activa ────────────────────────────
        if data["stc_warn"] and not prev.get("stc_warn"):
            notify(
                f"⚡ STC Maduro · {pair['name']}",
                f"Señal activa pero STC en extremo ({data['stc']})\n"
                f"Movimiento posiblemente agotado · Reducir tamaño\n"
                f"Precio: ${data['price']}",
                priority="default", tags="warning",
            )
            alerts_sent += 1

        new_state[symbol] = {
            "grade":        grade,
            "blocked":      data["blocked"],
            "stc_warn":     data["stc_warn"],
            "st_direction": data["st_direction"],
        }

    save_state(new_state)
    print(f"Chequeo completo ({datetime.now(timezone.utc).isoformat()}). "
          f"Alertas enviadas: {alerts_sent}")


if __name__ == "__main__":
    main()
