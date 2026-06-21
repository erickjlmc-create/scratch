name: Trifecta Pro Bot

on:
  schedule:
    # Cada 15 min, 24/7, todos los dias
    - cron: '*/15 * * * *'
  workflow_dispatch: {}   # permite correrlo manualmente desde la pestaña Actions

permissions:
  contents: write   # necesario para que el bot pueda guardar state.json

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - run: pip install -r requirements.txt

      - run: python trifecta_bot.py
        env:
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}

      - name: Guardar estado
        run: |
          git config user.name "trifecta-bot"
          git config user.email "bot@users.noreply.github.com"
          git add state.json
          git diff --staged --quiet || git commit -m "Actualiza estado [skip ci]"
          git push
