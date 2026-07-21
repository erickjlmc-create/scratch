name: Trifecta Bot

on:
  schedule:
    - cron: "*/15 * * * *"   # cada 15 minutos, ajusta a tu necesidad
  workflow_dispatch: {}       # permite correrlo manualmente desde la pestaña Actions

jobs:
  run-bot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Instalar dependencias
        run: pip install -r requirements.txt

      - name: Ejecutar bot
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CANAL_PRINCIPAL_ID: ${{ secrets.TELEGRAM_CANAL_PRINCIPAL_ID }}
          TELEGRAM_CANAL_RADAR_ID: ${{ secrets.TELEGRAM_CANAL_RADAR_ID }}
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: python trifecta_bot.py
