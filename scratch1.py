
import os, json, math, requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()  # No-op si no hay archivo .env (ej. en GitHub Actions)
except ImportError:
    pass  # En Actions las variables ya vienen inyectadas por el workflow (env:)

TELEGRAM_BOT_TOKEN          = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CANAL_PRINCIPAL_ID = os.environ.get("TELEGRAM_CANAL_PRINCIPAL_ID")
TELEGRAM_CANAL_RADAR_ID     = os.environ.get("TELEGRAM_CANAL_RADAR_ID")
NTFY_TOPIC                  = os.environ.get("NTFY_TOPIC")

PAIRS = [
    {"symbol": "SOLUSDT",  "name": "SOL/USDT"},
    {"symbol": "ETHUSDT",  "name": "ETH/USDT"},
    {"symbol": "BNBUSDT",  "name": "BNB/USDT"},
    {"symbol": "AVAXUSDT", "name": "AVAX/USDT"},
    {"symbol": "LINKUSDT", "name": "LINK/USDT"},
    {"symbol": "DOTUSDT",  "name": "DOT/USDT"},
    {"symbol": "NEARUSDT", "name": "NEAR/USDT"},
    {"symbol": "ARBUSDT",  "name": "ARB/USDT"},
    {"symbol": "SUIUSDT",  "name": "SUI/USDT"},
    {"symbol": "OPUSDT",   "name": "OP/USDT"},
    {"symbol": "INJUSDT",  "name": "INJ/USDT"},
    {"symbol": "WLDUSDT",  "name": "WLD/USDT"},
    {"symbol": "TIAUSDT",  "name": "TIA/USDT"},
]

CFG = {
    "ST_PERIOD":10,"ST_FACTOR":3.0,"EMA_FAST":9,"EMA_SLOW":21,"MFI_PERIOD":14,
    "STC_CYCLE":10,"STC_FAST":23,"STC_SLOW":50,"STC_BULL":25,"STC_BEAR":75,
    "SRSI_RSI":14,"SRSI_STOCH":14,"SRSI_K":3,"SRSI_D":3,"SRSI_OB":85,"SRSI_OS":15,
    "WR_LEN":14,"WR_OB":-20,"WR_OS":-80,
    "ADX_PERIOD":14,"ADX_MIN":25,"CHOP_LEN":14,"CHOP_MAX":61,
    "ATR_LEN":14,"ATR_PCT_LEN":100,"ATR_PCT_MIN":25,
    "VOL_AVG":20,"EMA100":100,"EMA200":200,"EMA50_4H":50,"EMA200_4H":200,
    "TP1_R":1.5,"TP2_R":2.5,"TP3_R":4.0,
    "CANDLES":300,"CANDLES_4H":250,
    "HEATMAP_LEN":50,
    "HEATMAP_EXTRA_HIGH":4.0,"HEATMAP_HIGH":2.5,"HEATMAP_MEDIUM":1.0,"HEATMAP_NORMAL":-0.5,
}

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
YF_SYMBOLS = {
    "SOLUSDT":"SOL-USD",  "ETHUSDT":"ETH-USD",  "BNBUSDT":"BNB-USD",  "AVAXUSDT":"AVAX-USD",
    "LINKUSDT":"LINK-USD","DOTUSDT":"DOT-USD",  "NEARUSDT":"NEAR-USD","ARBUSDT":"ARB-USD",
    "SUIUSDT":"SUI-USD",  "OPUSDT":"OP-USD",    "INJUSDT":"INJ-USD",
    "WLDUSDT":"WLD-USD",  "TIAUSDT":"TIA-USD",
}

def get_session():
    now=datetime.now(timezone.utc); m=now.hour*60+now.minute
    if   360<=m< 660: return "dead1"
    elif 660<=m< 720: return "asia"
    elif 720<=m< 780: return "london"
    elif 780<=m<1020: return "ny"
    elif 1020<=m<1170: return "ny_late"
    elif 1170<=m<1380: return "post"
    else:              return "dead2"

SESSION_META={
    "dead1":  {"operable":False,"caution":False,"label":"💀 ZONA MUERTA","risk":"NO OPERAR"},
    "asia":   {"operable":False,"caution":True, "label":"🌏 ASIA PRECAUCION","risk":"50% riesgo"},
    "london": {"operable":True, "caution":False,"label":"🇬🇧 APERTURA LONDRES","risk":"100% riesgo"},
    "ny":     {"operable":True, "caution":False,"label":"🗽 NY PRIME","risk":"OPTIMO 100%"},
    "ny_late":{"operable":True, "caution":False,"label":"🌆 NY TARDIO","risk":"60% riesgo"},
    "post":   {"operable":False,"caution":True, "label":"⚠️ POST-NY","risk":"50% riesgo"},
    "dead2":  {"operable":False,"caution":False,"label":"🤖 BOTS/MUERTA","risk":"NO OPERAR"},
}

def fetch_klines(symbol,interval,limit):
    yf_sym=YF_SYMBOLS[symbol]

    if interval=="15":
        df=yf.download(yf_sym,period="59d",interval="15m",progress=False,auto_adjust=True)

    elif interval=="240":
        # Yahoo Finance no ofrece velas nativas de 4H. Se descargan velas de
        # 1H y se re-muestrean (resample) a 4H reales (00/04/08/12/16/20 UTC),
        # agregando OHLCV correctamente en vez de usar el 1H tal cual.
        df=yf.download(yf_sym,period="729d",interval="1h",progress=False,auto_adjust=True)
        if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
        df=df.resample("4h").agg({"Open":"first","High":"max","Low":"min",
                                   "Close":"last","Volume":"sum"}).dropna()

    elif interval=="60":
        df=yf.download(yf_sym,period="729d",interval="1h",progress=False,auto_adjust=True)

    else:
        df=yf.download(yf_sym,period="2y",interval="1d",progress=False,auto_adjust=True)

    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.tail(limit).copy()
    return [{"time":int(ts.timestamp()*1000),"open":float(r["Open"]),"high":float(r["High"]),
             "low":float(r["Low"]),"close":float(r["Close"]),"volume":float(r["Volume"])}
            for ts,r in df.iterrows()]

def fetch_ticker(symbol):
    info=yf.Ticker(YF_SYMBOLS[symbol]).fast_info
    return {"lastPrice":str(round(float(info.last_price),6))}

def ema(arr,p):
    k=2/(p+1); res=[None]*len(arr); last=None; started=False
    for i,v in enumerate(arr):
        if v is None: continue
        if not started: res[i]=v; last=v; started=True; continue
        res[i]=v*k+last*(1-k); last=res[i]
    return res

def sma(arr,p):
    res=[None]*len(arr)
    for i in range(len(arr)):
        if i<p-1: continue
        res[i]=sum((v or 0) for v in arr[i-p+1:i+1])/p
    return res

def calc_atr(candles,p):
    n=len(candles); tr=[0.0]*n
    for i in range(n):
        if i==0: tr[i]=candles[i]["high"]-candles[i]["low"]
        else:
            pr=candles[i-1]
            tr[i]=max(candles[i]["high"]-candles[i]["low"],
                      abs(candles[i]["high"]-pr["close"]),abs(candles[i]["low"]-pr["close"]))
    res=[None]*n
    if n<p: return res
    res[p-1]=sum(tr[:p])/p
    for i in range(p,n): res[i]=(res[i-1]*(p-1)+tr[i])/p
    return res

def calc_atr_percentile(candles,atr_len,pct_len):
    atr_a=calc_atr(candles,atr_len); n=len(candles)
    if n<pct_len or atr_a[-1] is None: return 50.0
    cur=atr_a[-1]; win=[atr_a[i] for i in range(n-pct_len,n) if atr_a[i] is not None]
    return (sum(1 for v in win if cur>=v)/len(win))*100 if win else 50.0

def calc_choppiness(candles,p):
    n=len(candles)
    if n<p: return 50.0
    win=candles[-p:]
    atr1=[max(c["high"]-c["low"],
              abs(c["high"]-candles[i-1+n-p]["close"]) if i>0 else 0,
              abs(c["low"] -candles[i-1+n-p]["close"]) if i>0 else 0)
          for i,c in enumerate(win)]
    atr_sum=sum(atr1); hh=max(c["high"] for c in win); ll=min(c["low"] for c in win)
    rng=hh-ll
    if rng==0 or atr_sum==0: return 50.0
    return 100*math.log10(atr_sum/rng)/math.log10(p)

def calc_supertrend(candles, p, f):
    """
    Replica EXACTA del Supertrend en Pine Script v5:
      up_band := close[1] > up_band_prev ? math.max(up_band, up_band_prev) : up_band
      dn_band := close[1] < dn_band_prev ? math.min(dn_band, dn_band_prev) : dn_band
      trend_dir := close > dn_band_prev ? 1 : close < up_band_prev ? -1 : nz(trend_dir[1], 1)
    La clave: la direccion se calcula con las bandas PREVIAS (antes de actualizar),
    no con las bandas ya actualizadas. Eso evita los flips falsos.
    """
    n   = len(candles)
    atr = calc_atr(candles, p)
    trend   = [1]    * n
    st_line = [None] * n
    up_arr  = [None] * n
    dn_arr  = [None] * n

    for i in range(p, n):
        hl2    = (candles[i]["high"] + candles[i]["low"]) / 2
        a      = atr[i] or 0
        raw_up = hl2 - f * a
        raw_dn = hl2 + f * a

        # Bandas previas (antes de actualizar) — equivale a up_band[1] / dn_band[1] en Pine
        up_prev = up_arr[i-1] if up_arr[i-1] is not None else raw_up
        dn_prev = dn_arr[i-1] if dn_arr[i-1] is not None else raw_dn

        # Actualizar bandas usando el cierre ANTERIOR (close[1] en Pine)
        prev_close = candles[i-1]["close"]
        up_arr[i]  = max(raw_up, up_prev) if prev_close > up_prev else raw_up
        dn_arr[i]  = min(raw_dn, dn_prev) if prev_close < dn_prev else raw_dn

        # Tendencia con bandas PREVIAS — replica exacta de Pine
        cur_close = candles[i]["close"]
        if   cur_close > dn_prev: trend[i] = 1
        elif cur_close < up_prev: trend[i] = -1
        else:                     trend[i] = trend[i-1]

        st_line[i] = up_arr[i] if trend[i] == 1 else dn_arr[i]

    return trend, st_line

def calc_rsi(closes,p):
    n=len(closes); res=[None]*n
    if n<p+1: return res
    g=l=0.0
    for i in range(1,p+1):
        d=closes[i]-closes[i-1]
        if d>0: g+=d
        else:   l-=d
    g/=p; l/=p; res[p]=100 if l==0 else 100-100/(1+g/l)
    for i in range(p+1,n):
        d=closes[i]-closes[i-1]; g=(g*(p-1)+max(d,0))/p; l=(l*(p-1)+max(-d,0))/p
        res[i]=100 if l==0 else 100-100/(1+g/l)
    return res

def calc_stochrsi_k(closes,rl,sl,ks,ds):
    rsi=calc_rsi(closes,rl); n=len(closes); stoch=[None]*n
    for i in range(rl+sl-1,n):
        w=[v for v in rsi[i-sl+1:i+1] if v is not None]
        if len(w)<sl: continue
        lo,hi=min(w),max(w); stoch[i]=50 if hi==lo else ((rsi[i]-lo)/(hi-lo))*100
    k=sma(stoch,ks); return k[n-1] if k[n-1] is not None else 50

def calc_mfi(candles,p):
    n=len(candles)
    tp=[(c["high"]+c["low"]+c["close"])/3 for c in candles]
    mf=[tp[i]*candles[i]["volume"] for i in range(n)]; res=[None]*n
    for i in range(p,n):
        pos=neg=0.0
        for j in range(i-p+1,i+1):
            if j==0: continue
            if tp[j]>tp[j-1]: pos+=mf[j]
            else:              neg+=mf[j]
        res[i]=100 if neg==0 else 100-100/(1+pos/neg)
    return res

def calc_williams_r(candles,p):
    n=len(candles)
    if n<p: return -50
    w=candles[-p:]; hh=max(c["high"] for c in w); ll=min(c["low"] for c in w)
    cl=candles[-1]["close"]
    return -50 if hh==ll else ((hh-cl)/(hh-ll))*-100

def calc_adx(candles,p):
    n=len(candles)
    if n<2: return 0
    tr,dm_p,dm_m=[],[],[]
    for i in range(1,n):
        hi=candles[i]["high"]-candles[i-1]["high"]; lo=candles[i-1]["low"]-candles[i]["low"]
        tr.append(max(candles[i]["high"]-candles[i]["low"],
                      abs(candles[i]["high"]-candles[i-1]["close"]),
                      abs(candles[i]["low"]-candles[i-1]["close"])))
        dm_p.append(hi if (hi>lo and hi>0) else 0)
        dm_m.append(lo if (lo>hi and lo>0) else 0)
    if len(tr)<p: return 0
    av=sum(tr[:p]); p14=sum(dm_p[:p]); m14=sum(dm_m[:p]); dx=[]
    for i in range(p,len(tr)):
        av=av-av/p+tr[i]; p14=p14-p14/p+dm_p[i]; m14=m14-m14/p+dm_m[i]
        dip=(p14/av)*100 if av else 0; dim=(m14/av)*100 if av else 0
        s=dip+dim; dx.append((abs(dip-dim)/s)*100 if s else 0)
    if len(dx)<p: return 0
    adxv=sum(dx[:p])/p
    for i in range(p,len(dx)): adxv=(adxv*(p-1)+dx[i])/p
    return adxv

def calc_stc(closes,cycle,fast,slow):
    n=len(closes); ef=ema(closes,fast); es=ema(closes,slow)
    macd=[(ef[i]-es[i]) if (ef[i] and es[i]) else 0 for i in range(n)]
    def st1(src,ln):
        r=[0.0]*len(src)
        for i in range(ln-1,len(src)):
            w=src[i-ln+1:i+1]; lo,hi=min(w),max(w)
            r[i]=((src[i]-lo)/(hi-lo)*100) if hi!=lo else 0
        return r
    f1=st1(macd,cycle); pf=[0.0]*n
    for i in range(n): pf[i]=f1[i] if i==0 else pf[i-1]+0.5*(f1[i]-pf[i-1])
    f2=st1(pf,cycle); pff=[0.0]*n
    for i in range(n): pff[i]=f2[i] if i==0 else pff[i-1]+0.5*(f2[i]-pff[i-1])
    return pff[-1]

def calc_tps(entry,sl,direction):
    r=abs(entry-sl)
    if r==0: return None,None,None
    if direction=="long":
        return entry+CFG["TP1_R"]*r,entry+CFG["TP2_R"]*r,entry+CFG["TP3_R"]*r
    return entry-CFG["TP1_R"]*r,entry-CFG["TP2_R"]*r,entry-CFG["TP3_R"]*r

def analyze_pair(pair):
    sym=pair["symbol"]
    c15=fetch_klines(sym,"15",CFG["CANDLES"])
    c4h=fetch_klines(sym,"240",CFG["CANDLES_4H"])
    ticker=fetch_ticker(sym)
    closes=[c["close"] for c in c15]; vols=[c["volume"] for c in c15]
    # n-1 = ultima vela CERRADA (evita ruido de la vela en formacion)
    # n   = indice total por compatibilidad con funciones que usan arrays completos
    n   = len(c15)-1
    nc  = n - 1   # vela confirmada (penultima)

    st_trend,st_line_arr=calc_supertrend(c15,CFG["ST_PERIOD"],CFG["ST_FACTOR"])
    # Usar vela confirmada para el ST — evita flips falsos por vela en formacion
    st_bull=st_trend[nc]==1; st_line_val=st_line_arr[nc]

    ema9a=ema(closes,CFG["EMA_FAST"]); ema21a=ema(closes,CFG["EMA_SLOW"])
    ema_bull=ema9a[n]>ema21a[n]; ema_bear=ema9a[n]<ema21a[n]

    mfi_a=calc_mfi(c15,CFG["MFI_PERIOD"]); mfi_val=mfi_a[n] if mfi_a[n] is not None else 50
    mfi_bull=mfi_val>50; mfi_bear=mfi_val<50

    stc_val=calc_stc(closes,CFG["STC_CYCLE"],CFG["STC_FAST"],CFG["STC_SLOW"])
    stc_bull=stc_val>CFG["STC_BULL"]; stc_bear=stc_val<CFG["STC_BEAR"]
    stc_mature_l=stc_val>=95; stc_mature_s=stc_val<=5

    k_val=calc_stochrsi_k(closes,CFG["SRSI_RSI"],CFG["SRSI_STOCH"],CFG["SRSI_K"],CFG["SRSI_D"])
    wr_val=calc_williams_r(c15,CFG["WR_LEN"])
    block_l=(k_val>CFG["SRSI_OB"]) or (wr_val>CFG["WR_OB"])
    block_s=(k_val<CFG["SRSI_OS"]) or (wr_val<CFG["WR_OS"])

    adx_val=calc_adx(c15,CFG["ADX_PERIOD"]); adx_ok=adx_val>CFG["ADX_MIN"]
    chop_val=calc_choppiness(c15,CFG["CHOP_LEN"]); chop_ok=chop_val<CFG["CHOP_MAX"]
    regime_ok=adx_ok and chop_ok

    atr_pct=calc_atr_percentile(c15,CFG["ATR_LEN"],CFG["ATR_PCT_LEN"])
    atr_ok=atr_pct>=CFG["ATR_PCT_MIN"]

    vol_sma=sma(vols,CFG["VOL_AVG"]); vol_ok=vols[n]>(vol_sma[n] or 0)

    # RVOL (Volumen Relativo) — reemplaza ATR/ADX como filtro del Setup B.
    # Umbral: >=4.0 entre semana, >=3.0 fin de semana (hora UTC nativa de Python)
    rvol_val = (vols[n]/vol_sma[n]) if vol_sma[n] else None
    es_fin_de_semana = datetime.now(timezone.utc).weekday() >= 5  # 5=Sab, 6=Dom
    umbral_rvol = 3.0 if es_fin_de_semana else 4.0
    rvol_ok = rvol_val is not None and rvol_val >= umbral_rvol

    ema200a=ema(closes,CFG["EMA200"]); ema200_val=ema200a[n] or 0
    ema100a=ema(closes,CFG["EMA100"]); ema100_val=ema100a[n] or 0
    above200=closes[n]>ema200_val; below200=closes[n]<ema200_val

    closes4h=[c["close"] for c in c4h]
    ema50_4h_val =ema(closes4h,CFG["EMA50_4H"])[-1]  or 0
    ema200_4h_val=ema(closes4h,CFG["EMA200_4H"])[-1] or 0
    above4h=closes[n]>ema50_4h_val; below4h=closes[n]<ema50_4h_val

    bull_p=sum([st_bull,ema_bull,mfi_bull]); bear_p=sum([not st_bull,ema_bear,mfi_bear])

    bull_ctx=above200 and rvol_ok and vol_ok and above4h and not block_l and stc_bull and not stc_mature_l
    bear_ctx=below200 and rvol_ok and vol_ok and below4h and not block_s and stc_bear and not stc_mature_s

    sess=get_session(); meta=SESSION_META[sess]
    in_op=meta["operable"]; in_caut=meta["caution"]; in_ny_lon=sess in ("ny","london")

    app_l=bull_p==3 and bull_ctx and in_ny_lon
    app_s=bear_p==3 and bear_ctx and in_ny_lon
    ap_l =bull_p==3 and bull_ctx and in_op and not app_l
    ap_s =bear_p==3 and bear_ctx and in_op and not app_s
    b_l  =bull_p>=2 and rvol_ok and above4h and not block_l and stc_bull and in_op and not ap_l
    b_s  =bear_p>=2 and rvol_ok and below4h and not block_s and stc_bear and in_op and not ap_s
    prec_l=bull_p==3 and bull_ctx and in_caut; prec_s=bear_p==3 and bear_ctx and in_caut
    blk_l=(bull_p>=2 and above200 and rvol_ok and above4h and stc_bull and in_op) and block_l
    blk_s=(bear_p>=2 and below200 and rvol_ok and below4h and stc_bear and in_op) and block_s
    stc_warn=(stc_mature_l and (app_l or ap_l)) or (stc_mature_s and (app_s or ap_s))

    # ── Setup en formación (2/3 pilares, falta 1) ───────────────────
    forming_long  = bull_p == 2 and above200 and stc_bull and above4h and not block_l and in_op
    forming_short = bear_p == 2 and below200 and stc_bear and below4h and not block_s and in_op
    if forming_long:
        missing = "MFI" if not mfi_bull else ("EMA cruce" if not ema_bull else "Supertrend")
        forming_dir = "long"; forming_missing = missing
    elif forming_short:
        missing = "MFI" if not mfi_bear else ("EMA cruce" if not ema_bear else "Supertrend")
        forming_dir = "short"; forming_missing = missing
    else:
        forming_dir = "none"; forming_missing = ""

    # ── Confirmación 1H para señales B ──────────────────────────────
    h1_confirmed = False
    if b_l or b_s:
        try:
            c1h = fetch_klines(pair["symbol"], "60", 60) if hasattr(pair,"__getitem__") else []
            if len(c1h) >= 30:
                cl1h = [c["close"] for c in c1h]
                st1h,_ = calc_supertrend(c1h, CFG["ST_PERIOD"], CFG["ST_FACTOR"])
                e9_1h  = ema(cl1h, 9)[-1] or 0
                e21_1h = ema(cl1h, 21)[-1] or 0
                if b_l:
                    h1_confirmed = st1h[-1]==1 and e9_1h > e21_1h
                else:
                    h1_confirmed = st1h[-1]==-1 and e9_1h < e21_1h
        except Exception:
            h1_confirmed = False

    if   app_l or app_s:    grade="App"
    elif ap_l  or ap_s:     grade="Ap"
    elif b_l   or b_s:      grade="B"
    elif prec_l or prec_s:  grade="prec"
    elif blk_l or blk_s:    grade="blocked"
    else:                    grade="none"

    if   app_l or ap_l or b_l or prec_l:   direction="long"
    elif app_s or ap_s or b_s or prec_s:   direction="short"
    elif blk_l: direction="long"
    elif blk_s: direction="short"
    else:       direction="none"

    price=float(ticker["lastPrice"])
    sl_val=st_line_val if st_line_val else price*(0.98 if direction=="long" else 1.02)
    tp1,tp2,tp3=calc_tps(price,sl_val,direction)

    return {
        "grade":grade,"direction":direction,
        "blocked":blk_l or blk_s,"stc_warn":stc_warn,
        "forming_dir":forming_dir,"forming_missing":forming_missing,
        "h1_confirmed":h1_confirmed,
        "chop_val":round(chop_val,1),
        "price":price,
        "sl":  round(sl_val,4)  if sl_val else None,
        "tp1": round(tp1,4)     if tp1    else None,
        "tp2": round(tp2,4)     if tp2    else None,
        "tp3": round(tp3,4)     if tp3    else None,
        "ema200_15m": round(ema200_val,4),
        "ema100_15m": round(ema100_val,4),
        "ema50_4h":   round(ema50_4h_val,4),
        "ema200_4h":  round(ema200_4h_val,4),
        "bull_p":bull_p,"bear_p":bear_p,
        "k":round(k_val),"wr":round(wr_val),
        "adx":round(adx_val),"chop":round(chop_val,1),"atr_pct":round(atr_pct,1),
        "stc":round(stc_val),
        "rvol": round(rvol_val,2) if rvol_val is not None else None,
        "rvol_umbral": umbral_rvol,
        "st_direction":"bull" if st_bull else "bear",
        "st_icon": "🟢" if st_bull else "🔴",
        "session":sess,"session_label":meta["label"],"session_risk":meta["risk"],
    }

def obtener_estado_heatmap(symbol):
    """
    Replica el indicador 'Heatmap Volume [xdecow]' en Pine Script:
      mean   = SMA(volume, HEATMAP_LEN)
      std    = desviacion estandar poblacional de volume, HEATMAP_LEN
      stdbar = (volume_actual - mean) / std

    Retorna la zona exacta, igual que las 5 zonas de color del indicador:
      "extra_high" -> stdbar > 4.0   (rojo)
      "high"       -> stdbar > 2.5   (naranja)
      "medium"     -> stdbar > 1.0   (amarillo)
      "normal"     -> stdbar > -0.5  (celeste)
      "low"        -> resto          (teal)
    """
    largo = CFG["HEATMAP_LEN"]
    try:
        candles = fetch_klines(symbol, "15", largo + 5)
        if len(candles) < largo:
            return "normal"  # datos insuficientes, no arriesgar falso positivo

        vols = [c["volume"] for c in candles]
        ventana = vols[-largo:]
        mean = sum(ventana) / largo
        var = sum((v - mean) ** 2 for v in ventana) / largo   # stdev poblacional, como pstdev() en Pine
        std = var ** 0.5
        if std == 0:
            return "normal"

        stdbar = (vols[-1] - mean) / std
        if stdbar > CFG["HEATMAP_EXTRA_HIGH"]: return "extra_high"
        if stdbar > CFG["HEATMAP_HIGH"]:        return "high"
        if stdbar > CFG["HEATMAP_MEDIUM"]:      return "medium"
        if stdbar > CFG["HEATMAP_NORMAL"]:      return "normal"
        return "low"
    except Exception as e:
        print(f"Error calculando heatmap de {symbol}: {e}")
        return "normal"

def calc_position(price, sl):
    """Calculadora de posicion: capital $100, riesgo 1%."""
    CAPITAL   = 100.0
    RISK_PCT  = 0.01
    risk_usd  = CAPITAL * RISK_PCT          # $1.00
    dist      = abs(price - sl)
    if dist == 0: return None
    contracts = risk_usd / dist
    pos_value = contracts * price
    dist_pct  = (dist / price) * 100
    return {
        "risk_usd":  round(risk_usd, 2),
        "dist":      round(dist, 4),
        "dist_pct":  round(dist_pct, 2),
        "contracts": round(contracts, 4),
        "pos_value": round(pos_value, 2),
        "margin_5x": round(pos_value / 5, 2),
    }

def get_btc_context():
    """Evalua el estado actual de BTC para filtro de correlacion."""
    try:
        c = fetch_klines("BTCUSDT" if "BTCUSDT" in YF_SYMBOLS else "BTCUSDT_fake",
                         "15", 50)
    except Exception:
        # BTC no esta en PAIRS, descargarlo directo
        try:
            df = yf.download("BTC-USD", period="5d", interval="15m",
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.tail(50)
            c = [{"time":int(ts.timestamp()*1000),"open":float(r["Open"]),
                  "high":float(r["High"]),"low":float(r["Low"]),
                  "close":float(r["Close"]),"volume":float(r["Volume"])}
                 for ts,r in df.iterrows()]
        except Exception as e:
            print(f"BTC fetch error: {e}")
            return None
    if len(c) < 20: return None
    closes = [x["close"] for x in c]
    st_trend, _ = calc_supertrend(c, CFG["ST_PERIOD"], CFG["ST_FACTOR"])
    bt = st_trend[-1]
    chg1h = ((closes[-1] - closes[-4]) / closes[-4]) * 100 if len(closes) >= 4 else 0
    e9  = ema(closes, 9)[-1] or closes[-1]
    e21 = ema(closes, 21)[-1] or closes[-1]
    strong = bt == 1 and e9 > e21 and chg1h > 0
    weak   = bt == -1 or (bt == 1 and chg1h < -0.5)
    return {
        "bull": bt == 1, "strong": strong, "weak": weak,
        "chg1h": round(chg1h, 2), "price": round(closes[-1], 2),
    }

def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except: return {}

def save_state(s):
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2)

def notify(title,body,priority="default",tags="rotating_light",destino="principal"):
    """
    destino="principal" -> señales ejecutables, canal con sonido (por defecto).
    destino="radar"     -> análisis pasivo, canal silenciado.
    """
    if NTFY_TOPIC:
        try:
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",data=body.encode("utf-8"),
                          headers={"Title":title,"Priority":priority,"Tags":tags},timeout=10)
        except Exception as e: print("Error ntfy:",e)

    chat = TELEGRAM_CANAL_RADAR_ID if destino=="radar" else TELEGRAM_CANAL_PRINCIPAL_ID
    silenciar = (destino=="radar")
    if TELEGRAM_BOT_TOKEN and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id":chat,"text":f"*{title}*\n{body}","parse_mode":"Markdown",
                                "disable_notification":silenciar},
                          timeout=10)
        except Exception as e: print("Error Telegram:",e)
    if not NTFY_TOPIC and not (TELEGRAM_BOT_TOKEN and chat):
        print(f"[Sin canal - {destino}] {title} | {body}")

def main():
    state     = load_state()
    new_state = {}
    alerts_sent = 0
    session_signals = []
    expired_this_run = []         # para circuit breaker
    gl = {"App":"A++","Ap":"A+","B":"B","prec":"PRECAUCION"}

    now_utc  = datetime.now(timezone.utc)
    gt_hour  = (now_utc.hour - 6) % 24
    gt_min   = now_utc.minute
    today_str = now_utc.strftime("%Y-%m-%d")
    weekday   = now_utc.weekday()   # 0=Lun … 6=Dom

    is_open_window    = (gt_hour == 7  and gt_min < 15)
    is_close_window   = (gt_hour == 13 and 30 <= gt_min < 45)
    is_weekly_window  = (weekday == 0  and gt_hour == 6 and 50 <= gt_min <= 59)

    # ── Calendario macro hardcodeado (YYYY-MM-DD HH UTC) ────────────
    # Actualiza estas fechas cada mes con el calendario económico
    MACRO_EVENTS = [
        {"date":"2026-07-29","hour_utc":18,"name":"FOMC Decision"},
        {"date":"2026-07-30","hour_utc":12,"name":"GDP Q2"},
        {"date":"2026-08-01","hour_utc":12,"name":"NFP Julio"},
        {"date":"2026-08-12","hour_utc":12,"name":"CPI Julio"},
    ]

    # ── Contador diario ──────────────────────────────────────────────
    trade_count = state.get("_trade_count", {})
    today_count = trade_count.get(today_str, 0)
    DAILY_LIMIT = 3

    def register_trade():
        nonlocal today_count
        today_count += 1
        trade_count[today_str] = today_count
        new_state["_trade_count"] = trade_count

    def check_trade_limit():
        return today_count < DAILY_LIMIT

    # ── Circuit breaker: señales caducadas consecutivas ──────────────
    consecutive_expired = state.get("_consecutive_expired", 0)

    # ── BTC context (se evalua una sola vez, antes de usarse) ───────
    btc = get_btc_context()
    btc_ok_long  = btc is None or btc["bull"]
    btc_ok_short = btc is None or not btc["bull"]

    # ── Aviso evento macro (15 min antes) ───────────────────────────
    for ev in MACRO_EVENTS:
        ev_dt = datetime.strptime(f"{ev['date']} {ev['hour_utc']:02d}:00",
                                  "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        mins_to_event = (ev_dt - now_utc).total_seconds() / 60
        ev_key = f"_macro_{ev['date']}_{ev['hour_utc']}"
        if 0 < mins_to_event <= 15 and not state.get(ev_key):
            notify(
                f"📅 EVENTO MACRO en {int(mins_to_event)} min",
                f"⚠️ {ev['name']}\n"
                f"🚫 No abrir nuevas posiciones\n"
                f"🛡️ Si tienes trade abierto: mueve SL a breakeven\n"
                f"🕐 Hora GT: {int(mins_to_event)} minutos",
                priority="urgent", tags="calendar,warning",
            )
            new_state[ev_key] = True
            alerts_sent += 1
            print(f"MACRO: {ev['name']} en {int(mins_to_event)} min")
        elif mins_to_event <= 0 and state.get(ev_key):
            new_state[ev_key] = False   # resetear despues del evento

    # ── Resumen semanal (Lunes 06:50 GT) ────────────────────────────
    if is_weekly_window and not state.get("_weekly_sent"):
        weekly_lines = []
        for pair in PAIRS:
            try:
                d = analyze_pair(pair)
                st  = "🟢" if d["st_direction"]=="bull" else "🔴"
                e4h = "↑" if d["price"] > d["ema200_4h"] else "↓"
                stc = "▲" if d["stc"] > 50 else "▼"
                weekly_lines.append(
                    f"{st} {pair['name']:<12} 4H:{e4h} STC:{stc}{d['stc']:>3} ADX:{d['adx']:>3}"
                )
            except Exception:
                weekly_lines.append(f"❓ {pair['name']:<12} error")
        btc_str = f"BTC ${btc['price']} ({'🟢' if btc['bull'] else '🔴'} {btc['chg1h']:+.1f}%)" if btc else "BTC N/D"
        notify(
            f"📅 RESUMEN SEMANAL · {now_utc.strftime('%d %b %Y')}",
            f"Vista 4H de los 13 pares\n"
            f"{btc_str}\n\n"
            + "\n".join(weekly_lines) +
            f"\n\nSesion NY abre en 10 min · 07:00 GT",
            priority="default", tags="calendar,chart_bar",
        )
        new_state["_weekly_sent"] = True
        alerts_sent += 1
    elif weekday != 0:
        pass   # resetea el flag en dias que no son lunes


    def btc_line(direction):
        if btc is None: return ""
        if direction == "long" and btc["weak"]:
            return f"⚠️ CONTEXTO BTC: Bajando {btc['chg1h']}% en 1H · Reducir tamano 60%\n"
        if direction == "short" and btc["strong"]:
            return f"⚠️ CONTEXTO BTC: Subiendo {btc['chg1h']}% en 1H · Reducir tamano 60%\n"
        trend = "ALCISTA" if btc["bull"] else "BAJISTA"
        return f"✅ BTC {trend} ({btc['chg1h']:+.1f}% 1H) · Alineado\n"

    def checklist(grade, direction):
        """Decision binaria segun el grade."""
        if grade == "App":
            return "📋 CHECKLIST: A++ → EJECUTAR DIRECTO"
        if grade == "Ap":
            return "📋 CHECKLIST: A+ → EJECUTAR (confirmar 1H alineado)"
        if grade == "B":
            return ("📋 CHECKLIST B → verificar:\n"
                    "  1. Vela 15M cierra limpia\n"
                    "  2. Volumen > promedio\n"
                    "  3. No hay resistencia/soporte cercano")
        if grade == "prec":
            return "📋 CHECKLIST PREC → Solo con contexto muy claro · 50% tamano"
        return ""

    # ── RESUMEN APERTURA NY ──────────────────────────────────────────
    if is_open_window and not state.get("_open_sent"):
        btc_str = f"BTC ${btc['price']} ({'🟢' if btc['bull'] else '🔴'} {btc['chg1h']:+.1f}%)" if btc else "BTC N/D"
        # estado de todos los pares
        lines = []
        for pair in PAIRS:
            try:
                d = analyze_pair(pair)
                st = "🟢" if d["st_direction"]=="bull" else "🔴"
                e4h = "↑" if d["price"] > d["ema200_4h"] else "↓"
                lines.append(f"{st} {pair['name']:<12} EMA200 4H:{e4h}  STC:{d['stc']:>3}")
            except Exception:
                lines.append(f"❓ {pair['name']:<12} error")
        notify(
            "📊 APERTURA SESION NY · Trifecta Pro",
            f"🕖 07:00 GT · {now_utc.strftime('%a %d %b')}\n"
            f"{btc_str}\n\n"
            + "\n".join(lines),
            priority="default", tags="chart_bar", destino="radar",
        )
        new_state["_open_sent"] = True
        alerts_sent += 1
    elif not is_open_window:
        # resetear flag fuera de la ventana
        pass

    for pair in PAIRS:
        sym = pair["symbol"]
        try: data = analyze_pair(pair)
        except Exception as e:
            print(f"Error en {sym}: {e}"); continue

        prev  = state.get(sym, {"grade":"none","blocked":False,
                                "stc_warn":False,"st_direction":None})
        grade = data["grade"]

        # ── Volatilidad extrema: RVOL + Heatmap → canal radar ─────────
        # Se calcula una sola vez por par y se reutiliza mas abajo para el
        # prefijo 🔥 ALTA DENSIDAD del Setup B.
        heatmap_zone = obtener_estado_heatmap(sym)
        rvol_ok_general = data["rvol"] is not None and data["rvol"] >= data["rvol_umbral"]
        heatmap_denso = heatmap_zone in ("extra_high", "high", "medium")
        vol_extrema_ahora = rvol_ok_general and heatmap_denso

        vol_key = f"_vol_extrema_{sym}"
        vol_extrema_prev = state.get(vol_key, False)
        if vol_extrema_ahora and not vol_extrema_prev:
            zona_label = {"extra_high":"Extra High","high":"High","medium":"Medium"}[heatmap_zone]
            notify(
                f"💥 Volatilidad extrema · {pair['name']}",
                f"📶 RVOL: {data['rvol']} (min. {data['rvol_umbral']})\n"
                f"🌡️ Heatmap: {zona_label}\n"
                f"{data['st_icon']} ST {'ALCISTA' if data['st_direction']=='bull' else 'BAJISTA'} · Precio: ${data['price']}\n"
                f"🕐 {data['session_label']}",
                priority="default", tags="fire,warning",
                destino="radar",
            )
            alerts_sent += 1
        new_state[vol_key] = vol_extrema_ahora

        # ── Gestión activa manual: TP1 -> Breakeven -> TP2 -> TP1 -> TP3 -> Cierre ──
        # No hay bróker conectado (Yahoo Finance solo da precios, no ejecuta
        # órdenes), así que esto son AVISOS para que muevas el SL/cierres tú mismo.
        trade_key = f"_trade_{sym}"
        trade_activo = state.get(trade_key)
        if trade_activo:
            direccion = trade_activo["direction"]

            # TP1 -> mover SL a Breakeven (entrada)
            if not trade_activo.get("tp1_hit") and trade_activo.get("tp1"):
                tp1_tocado = (
                    (direccion=="long"  and data["price"] >= trade_activo["tp1"]) or
                    (direccion=="short" and data["price"] <= trade_activo["tp1"])
                )
                if tp1_tocado:
                    notify(
                        f"🛡️ POSICIÓN ASEGURADA · {pair['name']} · TP1 Alcanzado.",
                        f"SL movido a Breakeven. Trade libre de riesgo.\n"
                        f"👉 Muévelo tú manualmente a ${trade_activo['entry']} en tu plataforma "
                        f"(no hay bróker conectado, solo Yahoo Finance como fuente de precio).",
                        priority="high", tags="shield", destino="principal",
                    )
                    trade_activo["tp1_hit"] = True
                    alerts_sent += 1

            # TP2 -> mover SL a TP1 (solo si TP1 ya se aseguro)
            elif trade_activo.get("tp1_hit") and not trade_activo.get("tp2_hit") and trade_activo.get("tp2"):
                tp2_tocado = (
                    (direccion=="long"  and data["price"] >= trade_activo["tp2"]) or
                    (direccion=="short" and data["price"] <= trade_activo["tp2"])
                )
                if tp2_tocado:
                    notify(
                        f"🎯 TP2 ALCANZADO · {pair['name']}",
                        f"SL movido a TP1 (${trade_activo['tp1']}). Ganancia asegurada.\n"
                        f"👉 Muévelo tú manualmente en tu plataforma "
                        f"(no hay bróker conectado, solo Yahoo Finance como fuente de precio).",
                        priority="high", tags="dart", destino="principal",
                    )
                    trade_activo["tp2_hit"] = True
                    alerts_sent += 1

            # TP3 -> cierre total del runner (solo si TP2 ya se aseguro)
            elif trade_activo.get("tp2_hit") and not trade_activo.get("tp3_hit") and trade_activo.get("tp3"):
                tp3_tocado = (
                    (direccion=="long"  and data["price"] >= trade_activo["tp3"]) or
                    (direccion=="short" and data["price"] <= trade_activo["tp3"])
                )
                if tp3_tocado:
                    notify(
                        f"🏆 TP3 ALCANZADO · {pair['name']} · Trade completo.",
                        f"Cierra el runner restante manualmente en tu plataforma.\n"
                        f"👉 Objetivo final cumplido (4R).",
                        priority="high", tags="trophy", destino="principal",
                    )
                    trade_activo["tp3_hit"] = True
                    alerts_sent += 1
                    new_state.pop(trade_key, None)  # trade cerrado, dejar de rastrear
                    trade_activo = None

            if trade_activo is not None:
                new_state[trade_key] = trade_activo

        # ── Calculadora de posicion ──────────────────────────────────
        pos = None
        if data["sl"] and data["price"]:
            pos = calc_position(data["price"], data["sl"])

        def pos_block():
            if not pos: return ""
            return (
                f"💵 Capital: $100 · Riesgo 1% = ${pos['risk_usd']}\n"
                f"📏 Distancia SL: ${pos['dist']} ({pos['dist_pct']}%)\n"
                f"📦 Contratos: {pos['contracts']}\n"
                f"💼 Valor posicion: ${pos['pos_value']} (margen 5x: ~${pos['margin_5x']})\n"
            )

        # ── 1. Confluencia Supertrend 15M + EMA200 ────────────────────
        # Antes se avisaba cada vez que el Supertrend cambiaba de color.
        # Ahora solo se avisa cuando el Supertrend y la EMA200 quedan
        # alineados en la misma dirección:
        #   ST BAJISTA (rojo)  + precio por DEBAJO de la EMA200  -> confluencia bajista
        #   ST ALCISTA (verde) + precio por ENCIMA de la EMA200  -> confluencia alcista
        st_now = data["st_direction"]
        ema200_15m = data["ema200_15m"]
        confluencia_alcista = (st_now == "bull" and data["price"] > ema200_15m)
        confluencia_bajista = (st_now == "bear" and data["price"] < ema200_15m)
        confluencia_ahora = "bull" if confluencia_alcista else "bear" if confluencia_bajista else None
        confluencia_prev = prev.get("st_confluencia")

        if confluencia_ahora is not None and confluencia_ahora != confluencia_prev:
            if confluencia_ahora == "bull":
                notify(
                    f"🟢 Confluencia ST+EMA200 ALCISTA · {pair['name']}",
                    f"📈 Supertrend 15M VERDE y precio por ENCIMA de la EMA200\n"
                    f"💰 Precio: ${data['price']} · EMA200 15M: ${ema200_15m}\n"
                    f"📊 ADX:{data['adx']} · STC:{data['stc']}\n"
                    f"📊 EMA200 4H: ${data['ema200_4h']} · EMA50 4H: ${data['ema50_4h']}\n"
                    f"{btc_line('long')}"
                    f"🕐 {data['session_label']}",
                    priority="high", tags="green_circle,chart_with_upwards_trend",
                    destino="radar",
                )
            else:
                notify(
                    f"🔴 Confluencia ST+EMA200 BAJISTA · {pair['name']}",
                    f"📉 Supertrend 15M ROJO y precio por DEBAJO de la EMA200\n"
                    f"💰 Precio: ${data['price']} · EMA200 15M: ${ema200_15m}\n"
                    f"📊 ADX:{data['adx']} · STC:{data['stc']}\n"
                    f"📊 EMA200 4H: ${data['ema200_4h']} · EMA50 4H: ${data['ema50_4h']}\n"
                    f"{btc_line('short')}"
                    f"🕐 {data['session_label']}",
                    priority="high", tags="red_circle,chart_with_downwards_trend",
                    destino="radar",
                )
            alerts_sent += 1
            print(f"CONFLUENCIA ST+EMA200: {pair['name']} -> {confluencia_ahora.upper()} ${data['price']}")

        # ── 2. Señal nueva ───────────────────────────────────────────
        if grade in ("App","Ap","B","prec") and grade != prev.get("grade"):
            p   = max(data["bull_p"], data["bear_p"])
            pri = "urgent" if grade=="App" else "high" if grade in ("Ap","prec") else "default"
            if data["direction"] == "long":
                dir_icon="📈"; dir_sym="LONG"
                tag="rotating_light,chart_with_upwards_trend"
                btc_aligned = btc_ok_long
            else:
                dir_icon="📉"; dir_sym="SHORT"
                tag="rotating_light,chart_with_downwards_trend"
                btc_aligned = btc_ok_short

            grade_icons = {"App":"🚨","Ap":"⭐","B":"✅","prec":"⚠️"}
            sl_txt  = f"${data['sl']}"  if data['sl']  else "--"
            tp1_txt = f"${data['tp1']}" if data['tp1'] else "--"
            tp2_txt = f"${data['tp2']}" if data['tp2'] else "--"
            tp3_txt = f"${data['tp3']}" if data['tp3'] else "--"

            # Setup B: mostrar RVOL y marcar Heatmap caliente si aplica
            rvol_line = ""
            heatmap_prefix = ""
            if grade == "B":
                rvol_line = f"📶 RVOL: {data['rvol']} (min. {data['rvol_umbral']})\n"
                if heatmap_zone in ("extra_high", "high"):
                    heatmap_prefix = "🔥 ALTA DENSIDAD "

            # advertencia si BTC no alineado
            btc_warn = ""
            if not btc_aligned and btc:
                trend_btc = "cayendo" if data["direction"]=="long" else "subiendo"
                btc_warn = f"⚠️ BTC {trend_btc} {btc['chg1h']:+.1f}% · Senal valida pero reducir 60%\n"

            # contador de trades del dia
            register_trade()
            trades_restantes = DAILY_LIMIT - today_count
            if trades_restantes > 0:
                counter_line = f"📊 Trade #{today_count}/{DAILY_LIMIT} hoy · Quedan {trades_restantes}\n"
            else:
                counter_line = f"🚫 Trade #{today_count}/{DAILY_LIMIT} · LIMITE DIARIO ALCANZADO\n"

            notify(
                f"{heatmap_prefix}{grade_icons[grade]} {gl[grade]} {dir_icon} {dir_sym} · {pair['name']}",
                f"🎯 {p}/3 pilares · STC:{data['stc']} · ADX:{data['adx']} · Chop:{data['chop']}\n"
                f"{rvol_line}"
                f"📐 ATR%:{data['atr_pct']}% · WR:{data['wr']} · K:{data['k']}\n"
                f"💰 Precio: ${data['price']}\n"
                f"🛑 SL: {sl_txt} (Supertrend)\n"
                f"🎯 TP1: {tp1_txt} (1.5R · 40%)\n"
                f"🎯 TP2: {tp2_txt} (2.5R · 35%)\n"
                f"🏆 TP3: {tp3_txt} (4R · runner)\n"
                f"─────────────────\n"
                f"{pos_block()}"
                f"─────────────────\n"
                f"📊 EMA200 4H: ${data['ema200_4h']} · EMA50 4H: ${data['ema50_4h']}\n"
                f"📊 EMA200 15M: ${data['ema200_15m']}\n"
                f"─────────────────\n"
                f"{btc_warn}"
                f"{counter_line}"
                f"{checklist(grade, data['direction'])}\n"
                f"🕐 {data['session_label']} · {data['session_risk']}",
                priority=pri, tags=tag,
            )
            alerts_sent += 1
            session_signals.append({"pair":pair["name"],"grade":grade,"dir":data["direction"]})
            print(f"SENAL: {gl[grade]} {dir_sym} {pair['name']} ${data['price']}")

            # Iniciar seguimiento de TP1/TP2/TP3 para las alertas de gestion activa
            new_state[trade_key] = {
                "entry": data["price"], "sl": data["sl"],
                "tp1": data["tp1"], "tp2": data["tp2"], "tp3": data["tp3"],
                "direction": data["direction"],
                "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
            }

            # aviso especial al alcanzar el limite
            if today_count == DAILY_LIMIT:
                notify(
                    "🚫 LIMITE DIARIO ALCANZADO",
                    f"Ya completaste {DAILY_LIMIT} trades hoy\n"
                    f"El manual indica DETENER operaciones\n"
                    f"Proxima sesion: 07:00 GT manana",
                    priority="high", tags="stop_sign,warning",
                )
                alerts_sent += 1

        # ── 3. Señal caducada ────────────────────────────────────────
        prev_grade = prev.get("grade","none")
        if prev_grade in ("App","Ap","B") and grade == "none":
            expired_this_run.append(pair["name"])
            notify(
                f"❌ Señal caducada · {pair['name']}",
                f"La señal {gl[prev_grade]} ya no esta activa\n"
                f"{data['st_icon']} ST {'ALCISTA' if data['st_direction']=='bull' else 'BAJISTA'} · Precio: ${data['price']}\n"
                f"🕐 {data['session_label']}",
                priority="low", tags="x,warning",
            )
            alerts_sent += 1
            new_state.pop(trade_key, None)  # dejar de rastrear TP1/breakeven


        # ── 5. Setup en formación (2/3 pilares) ──────────────────────
        form_key = f"_forming_{sym}"
        prev_forming = state.get(form_key, "none")
        fd = data["forming_dir"]
        if fd != "none" and grade == "none" and fd != prev_forming:
            dir_icon = "📈" if fd == "long" else "📉"
            notify(
                f"👀 Setup formándose · {pair['name']}",
                f"{dir_icon} 2/3 pilares · Falta: {data['forming_missing']}\n"
                f"{data['st_icon']} ST {'ALCISTA' if data['st_direction']=='bull' else 'BAJISTA'} · ADX:{data['adx']} · STC:{data['stc']}\n"
                f"💰 Precio: ${data['price']} · EMA200 4H: ${data['ema200_4h']}\n"
                f"⏳ Esperar confirmacion del tercer pilar\n"
                f"🕐 {data['session_label']}",
                priority="low", tags="eyes,hourglass_flowing_sand",
            )
            new_state[form_key] = fd
            alerts_sent += 1
        elif fd == "none":
            new_state[form_key] = "none"

        # ── 6. Par desbloqueado ───────────────────────────────────────
        was_blocked = prev.get("blocked", False)
        if was_blocked and not data["blocked"] and grade in ("App","Ap","B"):
            dir_icon = "📈" if data["direction"]=="long" else "📉"
            notify(
                f"🔓 Desbloqueado · {pair['name']}",
                f"{dir_icon} Guardia liberada · Setup {gl[grade]} sigue valido\n"
                f"{data['st_icon']} ST {'ALCISTA' if data['st_direction']=='bull' else 'BAJISTA'} · ADX:{data['adx']}\n"
                f"StochRSI K:{data['k']} · Williams %R:{data['wr']}\n"
                f"💰 Precio: ${data['price']} · Posible entrada ahora\n"
                f"🕐 {data['session_label']}",
                priority="high", tags="unlock,rotating_light",
            )
            alerts_sent += 1

        # ── 7. Confirmacion 1H para señal B ──────────────────────────
        b_key = f"_b_1h_{sym}"
        if grade == "B" and data["h1_confirmed"] and not state.get(b_key):
            dir_icon = "📈" if data["direction"]=="long" else "📉"
            sl_txt_b  = f"${data['sl']}"  if data['sl']  else "--"
            tp1_txt_b = f"${data['tp1']}" if data['tp1'] else "--"
            notify(
                f"⭐ B+ confirmado · {pair['name']}",
                f"{dir_icon} Señal B con 1H alineado\n"
                f"{data['st_icon']} ST {'ALCISTA' if data['st_direction']=='bull' else 'BAJISTA'} · ADX:{data['adx']} · STC:{data['stc']}\n"
                f"ST 1H verde + EMA9>21 en 1H ✓\n"
                f"💰 Precio: ${data['price']}\n"
                f"🛑 SL: {sl_txt_b}\n"
                f"🎯 TP1: {tp1_txt_b} (1.5R)\n"
                f"📊 EMA200 4H: ${data['ema200_4h']}\n"
                f"🕐 {data['session_label']}",
                priority="high", tags="star,chart_with_upwards_trend",
            )
            new_state[b_key] = True
            alerts_sent += 1
        elif grade != "B":
            new_state[b_key] = False

        # ── 4. Bloqueado ─────────────────────────────────────────────
        if data["blocked"] and not prev.get("blocked"):
            notify(
                f"🔒 Bloqueado · {pair['name']}",
                f"{data['st_icon']} ST {'ALCISTA' if data['st_direction']=='bull' else 'BAJISTA'} · ADX:{data['adx']} · Setup valido pero guardia activa\n"
                f"📐 StochRSI K:{data['k']} · Williams %R:{data['wr']}\n"
                f"📊 EMA200 4H: ${data['ema200_4h']} · Precio: ${data['price']}\n"
                f"🕐 {data['session_label']}",
                priority="low", tags="lock,warning",
            )
            alerts_sent += 1

        # ── 5. STC maduro ────────────────────────────────────────────
        if data["stc_warn"] and not prev.get("stc_warn"):
            notify(
                f"⚡ STC Maduro · {pair['name']}",
                f"{data['st_icon']} ST {'ALCISTA' if data['st_direction']=='bull' else 'BAJISTA'} · ADX:{data['adx']} · STC en extremo ({data['stc']})\n"
                f"⚠️ Posible agotamiento · Reducir tamano\n"
                f"💰 Precio: ${data['price']} · EMA200 4H: ${data['ema200_4h']}",
                priority="default", tags="warning",
                destino="radar",
            )
            alerts_sent += 1

        new_state[sym] = {"grade":grade,"blocked":data["blocked"],
                          "stc_warn":data["stc_warn"],"st_direction":data["st_direction"],
                          "st_confluencia":confluencia_ahora}

    # ── Circuit breaker: 2 señales caducadas consecutivas ───────────
    if expired_this_run:
        consecutive_expired += len(expired_this_run)
        new_state["_consecutive_expired"] = consecutive_expired
        if consecutive_expired >= 2 and not state.get("_circuit_breaker_sent"):
            notify(
                "⛔ CIRCUIT BREAKER · Señales fallando",
                f"{consecutive_expired} señales caducadas consecutivas\n"
                f"📉 El mercado no esta respondiendo al sistema\n"
                f"🛡️ Reduce tamaño al 50% o para por hoy\n"
                f"🔄 Se resetea automaticamente manana",
                priority="high", tags="stop_sign,warning",
            )
            new_state["_circuit_breaker_sent"] = True
            alerts_sent += 1
    else:
        # si hubo señales activas sin caducar, resetear contador
        active_now = any(
            new_state.get(p["symbol"],{}).get("grade","none") in ("App","Ap","B")
            for p in PAIRS
        )
        if active_now:
            new_state["_consecutive_expired"] = 0
        else:
            new_state["_consecutive_expired"] = consecutive_expired

    # Resetear circuit breaker al dia siguiente
    cb_date = state.get("_circuit_breaker_date","")
    if cb_date != today_str:
        new_state["_circuit_breaker_sent"] = False
        new_state["_circuit_breaker_date"] = today_str
        new_state["_consecutive_expired"]  = 0

    # ── Confluencia multi-par (3+ pares misma direccion) ────────────
    active = [s for s in session_signals if s["grade"] in ("App","Ap","B")]
    longs  = [s for s in active if s["dir"]=="long"]
    shorts = [s for s in active if s["dir"]=="short"]
    for group, label, icon in [(longs,"LONG","📈"),(shorts,"SHORT","📉")]:
        if len(group) >= 3:
            names = ", ".join(s["pair"] for s in group)
            notify(
                f"🌊 CONFLUENCIA {label} · {len(group)} pares",
                f"{icon} Movimiento de mercado detectado\n"
                f"Pares: {names}\n"
                f"💡 Alta probabilidad de continuacion\n"
                f"🕐 {SESSION_META[get_session()]['label']}",
                priority="urgent", tags="wave,rotating_light", destino="radar",
            )
            alerts_sent += 1

    # ── Resumen de cierre NY ─────────────────────────────────────────
    if is_close_window and not state.get("_close_sent"):
        # calcular mejor oportunidad de la sesion
        best_pair_lines = []
        for sig in session_signals:
            sym_key = next((p["symbol"] for p in PAIRS if p["name"]==sig["pair"]), None)
            if not sym_key: continue
            try:
                d = analyze_pair({"symbol":sym_key,"name":sig["pair"]})
                entry = state.get(sym_key,{}).get("price", d["price"])
                move_pct = ((d["price"]-entry)/entry*100) if sig["dir"]=="long" else ((entry-d["price"])/entry*100)
                best_pair_lines.append((sig["pair"], sig["grade"], move_pct, d["price"]))
            except Exception:
                pass
        best_str = ""
        if best_pair_lines:
            best = max(best_pair_lines, key=lambda x: x[2])
            icon = "🏆" if best[2] > 0 else "📉"
            best_str = (f"\n{icon} Mejor oportunidad: {best[0]} ({gl[best[1]]})\n"
                        f"   Movimiento: {best[2]:+.2f}% · Precio actual: ${best[3]}")

        btc_str = f"BTC ${btc['price']} ({'🟢' if btc['bull'] else '🔴'})" if btc else ""
        notify(
            "📊 CIERRE SESION NY · Trifecta Pro",
            f"🕑 13:30 GT · {now_utc.strftime('%a %d %b')}\n"
            f"{btc_str}\n\n"
            f"Señales esta sesion: {len(session_signals)}\n"
            + (f"  A++:{sum(1 for s in session_signals if s['grade']=='App')} · "
               f"A+:{sum(1 for s in session_signals if s['grade']=='Ap')} · "
               f"B:{sum(1 for s in session_signals if s['grade']=='B')}\n"
               if session_signals else "  Ninguna\n")
            + f"Trades ejecutados hoy: {today_count}/{DAILY_LIMIT}\n"
            + best_str +
            f"\nProxima sesion: 07:00 GT manana",
            priority="default", tags="chart_bar", destino="radar",
        )
        new_state["_close_sent"] = True
        alerts_sent += 1

    # Preservar flags de sesion del state anterior si siguen vigentes
    if state.get("_open_sent")  and is_open_window:
        new_state["_open_sent"]  = True
    if state.get("_close_sent") and is_close_window:
        new_state["_close_sent"] = True

    save_state(new_state)
    print(f"Chequeo completo ({now_utc.isoformat()}). Alertas: {alerts_sent}")

if __name__=="__main__":
    main()
