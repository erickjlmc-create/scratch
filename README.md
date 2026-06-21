# Trifecta Pro Bot

Bot gratuito que revisa SOL, ETH, BNB, AVAX, LINK, DOT, NEAR y ARB cada 15
minutos durante la sesion NY (07:00-13:00 hora Guatemala, Lunes a Sabado) y
te notifica cuando aparece una señal A++/A+/B, replicando exactamente la
logica de `TrifectaPro_Dashboard_v2.html` y `TrifectaPro_Scanner_v2.pine`.

Corre 100% gratis en GitHub Actions. No necesita servidor, no necesita tu
computadora prendida, no usa API key de Bybit (datos publicos).

## Configuracion (una sola vez)

1. Repository Settings → Secrets and variables → Actions → agrega:
   - `NTFY_TOPIC` = el nombre de canal que elegiste en ntfy.sh
   - (opcional) `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` si tambien quieres Telegram
2. Settings → Actions → General → Workflow permissions → marca
   "Read and write permissions" (para que el bot pueda guardar `state.json`).
3. Pestaña **Actions** → selecciona "Trifecta Pro Bot" → **Run workflow**
   para probarlo manualmente.

Despues de eso corre solo, cada 15 minutos, automaticamente.

## Archivos

- `trifecta_bot.py` — logica del scanner + envio de notificaciones
- `state.json` — recuerda la ultima señal de cada par (lo actualiza el bot)
- `.github/workflows/trifecta-bot.yml` — el cron gratuito
- `requirements.txt` — dependencia (solo `requests`)
