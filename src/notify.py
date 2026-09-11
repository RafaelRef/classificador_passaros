"""
Envio da notificação para a família quando um pássaro é identificado.

Backend plugável, igual identify.py. Comece com ConsoleNotifier (zero setup) pra
testar o pipeline. Para uso real, comece por Telegram: é uma API oficial, grátis,
sem risco de bloqueio e leva ~5 minutos pra configurar (fale com @BotFather).

Sobre WhatsApp (que foi a opção que você mencionou): dá pra fazer, mas vale saber
o trade-off antes de escolher:

- API oficial (WhatsApp Cloud API, da Meta): robusta, sem risco de bloqueio, mas
  exige criar uma conta Business, verificar um número e ter mensagens fora da
  janela de 24h aprovadas como "template" pela Meta. Setup mais burocrático.
- Bibliotecas não-oficiais (ex: whatsapp-web.js, Baileys — ambas em Node.js, não
  Python): simulam um WhatsApp Web logado via QR code, sem aprovação nenhuma, e
  começam a funcionar em minutos. É o caminho mais comum em projetos hobby como
  esse. O risco real é baixo pra poucas mensagens por dia (tipo alertas de
  pássaro), mas tecnicamente não é suportado pelo WhatsApp e usa um número de
  telefone de verdade rodando um processo que precisa ficar ligado.

Sugestão: valide tudo com Telegram primeiro (mais simples e mais estável), e se
depois de usar um tempo vocês realmente preferirem WhatsApp por já ser o app que a
família usa, a gente troca só esta peça do sistema (é um WhatsAppNotifier novo,
implementado como um pequeno serviço Node ao lado, comunicando por HTTP local com
este pipeline Python). O resto do sistema não muda nada.
"""

from __future__ import annotations

import abc
import pathlib
import sys
from datetime import datetime

from .identify import Identification

if sys.platform == "win32":
    # Console do Windows por padrão não sabe exibir emoji (usado na mensagem);
    # sem isso, print() derruba o processo com UnicodeEncodeError.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _build_caption(identification: Identification, sighting_count: int) -> str:
    conf_pct = round(identification.confidence * 100)
    horario = datetime.now().strftime("%H:%M")
    vezes = "1ª vez que vemos essa espécie!" if sighting_count <= 1 else f"já vimos essa espécie {sighting_count}x"

    lines = [
        identification.species_common_name,
        identification.species_scientific_name or "",
        f"Horário: {horario}",
        f"Confiança: {conf_pct}% (fonte: {identification.source})",
        vezes,
    ]
    if identification.taxon_id is not None:
        lines.append(f"Saiba mais: https://www.inaturalist.org/taxa/{identification.taxon_id}")

    return "\n".join(line for line in lines if line)


class Notifier(abc.ABC):
    @abc.abstractmethod
    def notify(self, identification: Identification, image_path: pathlib.Path, sighting_count: int) -> None:
        """Envia a notificação com a identificação e a foto do avistamento.

        sighting_count: quantas vezes essa espécie já foi vista, incluindo esta.
        """


class ConsoleNotifier(Notifier):
    """Só imprime no terminal. Bom para testar o pipeline sem configurar nada."""

    def notify(self, identification: Identification, image_path: pathlib.Path, sighting_count: int) -> None:
        caption = _build_caption(identification, sighting_count).replace("\n", " - ")
        print(f"[notificação] {caption} - foto: {image_path}")


class TelegramNotifier(Notifier):
    """
    Manda a foto + identificação para um chat/grupo do Telegram via bot.

    Setup (veja .env.example):
    1. Fale com @BotFather no Telegram, crie um bot com /newbot, guarde o token.
    2. Adicione o bot num grupo com a família (ou converse direto com ele).
    3. Mande qualquer mensagem no grupo, depois acesse
       https://api.telegram.org/bot<TOKEN>/getUpdates para achar o chat_id.
    """

    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError(
                "TelegramNotifier precisa de TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env."
            )
        self.bot_token = bot_token
        self.chat_id = chat_id

    def notify(self, identification: Identification, image_path: pathlib.Path, sighting_count: int) -> None:
        import requests

        caption = _build_caption(identification, sighting_count)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": self.chat_id, "caption": caption},
                files={"photo": f},
                timeout=15,
            )
        resp.raise_for_status()


def build_notifier(backend: str, telegram_token: str | None = None, telegram_chat_id: str | None = None) -> Notifier:
    """Fábrica simples usada pelo main.py a partir da variável NOTIFY_BACKEND."""
    if backend == "console":
        return ConsoleNotifier()
    if backend == "telegram":
        return TelegramNotifier(bot_token=telegram_token or "", chat_id=telegram_chat_id or "")
    raise ValueError(f"Backend de notificação desconhecido: {backend!r}")
