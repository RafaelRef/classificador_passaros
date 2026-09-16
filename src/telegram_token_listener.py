"""
Escuta o grupo do Telegram por mensagens "/token <valor>" pra atualizar o
token do iNaturalist sem precisar abrir o editor e mexer no .env.

Fluxo:
1. O token do iNaturalist expira a cada ~24h. Quando isso acontece, o app
   manda um aviso no grupo pedindo um token novo (veja main.py).
2. Você gera um token novo em https://www.inaturalist.org/users/api_token e
   cola no grupo assim: /token eyJhbGciOi...
3. Este listener detecta a mensagem, salva o token novo (via TokenStore) e
   responde confirmando.

Usa long polling (getUpdates) — não precisa expor porta nem configurar
webhook, só ficar rodando em background junto com o resto do app.
"""

from __future__ import annotations

import threading
import time

import requests

from .token_store import TokenStore

TOKEN_COMMAND_PREFIX = "/token"


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    except requests.RequestException as exc:
        print(f"[aviso] não consegui mandar mensagem no Telegram: {exc}")


class TelegramTokenListener:
    def __init__(self, bot_token: str, chat_id: str, token_store: TokenStore):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.token_store = token_store
        self._offset = 0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        while True:
            try:
                resp = requests.get(
                    url, params={"offset": self._offset, "timeout": 30}, timeout=35
                )
                resp.raise_for_status()
                updates = resp.json().get("result", [])
            except requests.RequestException as exc:
                print(f"[aviso] erro ao consultar Telegram pra renovação de token: {exc}")
                time.sleep(5)
                continue

            for update in updates:
                self._offset = update["update_id"] + 1
                self._handle_update(update)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()

        if chat_id != self.chat_id or not text.lower().startswith(TOKEN_COMMAND_PREFIX):
            return

        new_token = text[len(TOKEN_COMMAND_PREFIX):].strip()
        if not new_token:
            send_telegram_message(
                self.bot_token, self.chat_id, "Manda assim: /token SEU_TOKEN_AQUI"
            )
            return

        self.token_store.set(new_token)
        send_telegram_message(
            self.bot_token,
            self.chat_id,
            "✅ Token do iNaturalist atualizado! Já pode voltar a identificar.",
        )
        print("[info] token do iNaturalist atualizado via Telegram")
