"""
Sistema Trifecta Pro v5 - Bot de notificaciones
Replica TrifectaPro_Dashboard_v5.html y TrifectaPro_Scanner_v5.pine

NOVEDADES v5 vs v4:
  + Choppiness Index como filtro de regimen (junto a ADX)
  + ATR Percentile reemplaza filtro binario ATR > avg
  + EMA 100 en 15M como contexto adicional
  + TPs automaticos calculados con SL en linea del Supertrend (1.5R/2.5R/4R)
  + EMA 200 en 4H incluida en cada alerta

SIEMPRE notifica cuando Supertrend cambia de color en cualquier par.
"""

import os, json, math, requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

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
    "ST_PERIOD":10,"ST_FACTOR":3.0,"EMA_FAST":9,"EMA_SLOW":21,"MFI_PERIOD":14,
    "STC_CYCLE":10,"STC_FAST":23,"STC_SLOW":50,"STC_BULL":25,"STC_BEAR":75,
    "SRSI_RSI":14,"SRSI_STOCH":14,"SRSI_K":3,"SRSI_D":3,"SRSI_OB":85,"SRSI_OS":15,
    "WR_LEN":14,"WR_OB":-20,"WR_OS":-80,
    "ADX_PERIOD":14,"ADX_MIN":25,"CHOP_LEN":14,"CHOP_MAX":61,
    "ATR_LEN":14,"ATR_PCT_LEN":100,"ATR_PCT_MIN":25,
    "VOL_AVG":20,"EMA100":100,"EMA200":200,"EMA50_4H":50,"EMA200_4H":200,
    "TP1_R":1.5,"TP2_R":2.5,"TP3_R":4.0,
    "CANDLES":300,"CANDLES_4H":250,
}

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
YF_SYMBOLS = {
    "SOLUSDT":"SOL-USD","ETHUSDT":"ETH-USD","BNBUSDT":"BNB-USD","AVAXUSDT":"AVAX-USD",
    "LINKUSDT":"LINK-USD","DOTUSDT":"DOT-USD","NEARUSDT":"NEAR-USD","ARBUSDT":"ARB-USD",
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
    "dead1":  {"operable":False,"caution":False,"label":"ZONA MUERTA","risk":"NO OPERAR"},
    "asia":   {"operable":False,"caution":True, "label":"ASIA PRECAUCION","risk":"50% riesgo"},
    "london": {"operable":True, "caution":False,"label":"APERTURA LONDRES","risk":"100% riesgo"},
    "ny":     {"operable":True, "caution":False,"label":"NY PRIME","risk":"OPTIMO 100%"},
    "ny_late":{"operable":True, "caution":False,"label":"NY TARDIO","risk":"60% riesgo"},
    "post":   {"operable":False,"caution":True, "label":"POST-NY","risk":"50% riesgo"},
    "dead2":  {"operable":False,"caution":False,"label":"BOTS/MUERTA","risk":"NO OPERAR"},
}

def fetch_klines(symbol,interval,limit):
    yf_sym=YF_SYMBOLS[symbol]
    if interval=="15":   yi,per="15m","59d"
    elif interval=="240": yi,per="1h","729d"
    else:                yi,per="1d","2y"
    df=yf.download(yf_sym,period=per,interval=yi,progress=False,auto_adjust=True)
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

def calc_supertrend(candles,p,f):
    n=len(candles); atr=calc_atr(candles,p); trend=[1]*n; st_line=[None]*n
    up=dn=None
    for i in range(p,n):
        hl2=(candles[i]["high"]+candles[i]["low"])/2; a=atr[i] or 0
        nu,nd=hl2-f*a,hl2+f*a; pc=candles[i-1]["close"]
        up=max(nu,up) if (up is not None and pc>up) else nu
        dn=min(nd,dn) if (dn is not None and pc<dn) else nd
        if trend[i-1]==1: trend[i]=-1 if candles[i]["close"]<up else 1
        else:             trend[i]=1  if candles[i]["close"]>dn else -1
        st_line[i]=up if trend[i]==1 else dn
    return trend,st_line

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
    closes=[c["close"] for c in c15]; vols=[c["volume"] for c in c15]; n=len(c15)-1

    st_trend,st_line_arr=calc_supertrend(c15,CFG["ST_PERIOD"],CFG["ST_FACTOR"])
    st_bull=st_trend[n]==1; st_line_val=st_line_arr[n]

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

    ema200a=ema(closes,CFG["EMA200"]); ema200_val=ema200a[n] or 0
    ema100a=ema(closes,CFG["EMA100"]); ema100_val=ema100a[n] or 0
    above200=closes[n]>ema200_val; below200=closes[n]<ema200_val

    closes4h=[c["close"] for c in c4h]
    ema50_4h_val =ema(closes4h,CFG["EMA50_4H"])[-1]  or 0
    ema200_4h_val=ema(closes4h,CFG["EMA200_4H"])[-1] or 0
    above4h=closes[n]>ema50_4h_val; below4h=closes[n]<ema50_4h_val

    bull_p=sum([st_bull,ema_bull,mfi_bull]); bear_p=sum([not st_bull,ema_bear,mfi_bear])

    bull_ctx=above200 and regime_ok and atr_ok and vol_ok and above4h and not block_l and stc_bull and not stc_mature_l
    bear_ctx=below200 and regime_ok and atr_ok and vol_ok and below4h and not block_s and stc_bear and not stc_mature_s

    sess=get_session(); meta=SESSION_META[sess]
    in_op=meta["operable"]; in_caut=meta["caution"]; in_ny_lon=sess in ("ny","london")

    app_l=bull_p==3 and bull_ctx and in_ny_lon
    app_s=bear_p==3 and bear_ctx and in_ny_lon
    ap_l =bull_p==3 and bull_ctx and in_op and not app_l
    ap_s =bear_p==3 and bear_ctx and in_op and not app_s
    b_l  =bull_p>=2 and regime_ok and atr_ok and above4h and not block_l and stc_bull and in_op and not ap_l
    b_s  =bear_p>=2 and regime_ok and atr_ok and below4h and not block_s and stc_bear and in_op and not ap_s
    prec_l=bull_p==3 and bull_ctx and in_caut; prec_s=bear_p==3 and bear_ctx and in_caut
    blk_l=(bull_p>=2 and above200 and regime_ok and atr_ok and above4h and stc_bull and in_op) and block_l
    blk_s=(bear_p>=2 and below200 and regime_ok and atr_ok and below4h and stc_bear and in_op) and block_s
    stc_warn=(stc_mature_l and (app_l or ap_l)) or (stc_mature_s and (app_s or ap_s))

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
        "st_direction":"bull" if st_bull else "bear",
        "session":sess,"session_label":meta["label"],"session_risk":meta["risk"],
    }

def load_state():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except: return {}

def save_state(s):
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2)

def notify(title,body,priority="default",tags="rotating_light"):
    topic=os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}",data=body.encode("utf-8"),
                          headers={"Title":title,"Priority":priority,"Tags":tags},timeout=10)
        except Exception as e: print("Error ntfy:",e)
    bot=os.environ.get("TELEGRAM_BOT_TOKEN"); chat=os.environ.get("TELEGRAM_CHAT_ID")
    if bot and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{bot}/sendMessage",
                          json={"chat_id":chat,"text":f"*{title}*\n{body}","parse_mode":"Markdown"},
                          timeout=10)
        except Exception as e: print("Error Telegram:",e)
    if not topic and not (bot and chat):
        print(f"[Sin canal] {title} | {body}")

def main():
    state=load_state(); new_state={}; alerts_sent=0
    gl={"App":"A++","Ap":"A+","B":"B","prec":"PRECAUCION"}

    for pair in PAIRS:
        sym=pair["symbol"]
        try: data=analyze_pair(pair)
        except Exception as e: print(f"Error en {sym}: {e}"); continue

        prev=state.get(sym,{"grade":"none","blocked":False,"stc_warn":False,"st_direction":None})
        grade=data["grade"]

        def fmt_levels(d):
            sl  = f"${d['sl']}"  if d['sl']  else "--"
            tp1 = f"${d['tp1']}" if d['tp1'] else "--"
            tp2 = f"${d['tp2']}" if d['tp2'] else "--"
            tp3 = f"${d['tp3']}" if d['tp3'] else "--"
            return (
                f"SL: {sl}\n"
                f"TP1: {tp1} (1.5R · 40%)\n"
                f"TP2: {tp2} (2.5R · 35%)\n"
                f"TP3: {tp3} (4R · runner)\n"
                f"---\n"
                f"EMA200 4H: ${d['ema200_4h']}\n"
                f"EMA50  4H: ${d['ema50_4h']}\n"
                f"EMA200 15M: ${d['ema200_15m']}"
            )

        # 1. Supertrend flip — SIEMPRE notifica
        st_now=data["st_direction"]; st_prev=prev.get("st_direction")
        if st_prev is not None and st_now!=st_prev:
            icon="verde ALCISTA" if st_now=="bull" else "rojo BAJISTA"
            tag ="green_circle,chart_with_upwards_trend" if st_now=="bull" else "red_circle,chart_with_downwards_trend"
            emoji="ST VERDE" if st_now=="bull" else "ST ROJO"
            notify(
                f"{emoji} · {pair['name']}",
                f"Supertrend cambio a {icon} en 15M\n"
                f"Precio: ${data['price']} · STC: {data['stc']}\n"
                f"EMA200 4H: ${data['ema200_4h']} · EMA50 4H: ${data['ema50_4h']}\n"
                f"Sesion: {data['session_label']}",
                priority="high", tags=tag,
            )
            alerts_sent+=1
            print(f"ST FLIP: {pair['name']} -> {st_now.upper()} ${data['price']}")

        # 2. Senal nueva
        if grade in ("App","Ap","B","prec") and grade!=prev.get("grade"):
            p=max(data["bull_p"],data["bear_p"])
            pri="urgent" if grade=="App" else "high" if grade in ("Ap","prec") else "default"
            dir_sym="LONG" if data["direction"]=="long" else "SHORT"
            tag="rotating_light,"+("chart_with_upwards_trend" if data["direction"]=="long" else "chart_with_downwards_trend")
            notify(
                f"{gl[grade]} {dir_sym} · {pair['name']}",
                f"{p}/3 pilares · STC:{data['stc']} · ADX:{data['adx']} · Chop:{data['chop']}\n"
                f"ATR%:{data['atr_pct']}% · WR:{data['wr']} · StochRSI K:{data['k']}\n"
                f"Precio: ${data['price']}\n"
                f"{fmt_levels(data)}\n"
                f"Sesion: {data['session_label']} · {data['session_risk']}",
                priority=pri, tags=tag,
            )
            alerts_sent+=1
            print(f"SENAL: {gl[grade]} {dir_sym} {pair['name']} ${data['price']}")

        # 3. Bloqueado
        if data["blocked"] and not prev.get("blocked"):
            notify(
                f"BLOQUEADO · {pair['name']}",
                f"Setup valido pero guardia activa\n"
                f"StochRSI K:{data['k']} · Williams %R:{data['wr']}\n"
                f"EMA200 4H: ${data['ema200_4h']} · Precio: ${data['price']}\n"
                f"Sesion: {data['session_label']}",
                priority="low", tags="lock,warning",
            )
            alerts_sent+=1

        # 4. STC maduro
        if data["stc_warn"] and not prev.get("stc_warn"):
            notify(
                f"STC MADURO · {pair['name']}",
                f"Senal activa pero STC en extremo ({data['stc']})\n"
                f"Posible agotamiento · Reducir tamano\n"
                f"Precio: ${data['price']} · EMA200 4H: ${data['ema200_4h']}",
                priority="default", tags="warning",
            )
            alerts_sent+=1

        new_state[sym]={"grade":grade,"blocked":data["blocked"],
                        "stc_warn":data["stc_warn"],"st_direction":data["st_direction"]}

    save_state(new_state)
    print(f"Chequeo completo ({datetime.now(timezone.utc).isoformat()}). Alertas: {alerts_sent}")

if __name__=="__main__":
    main()
