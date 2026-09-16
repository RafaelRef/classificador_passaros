"""
Guarda o token de acesso do iNaturalist em um arquivo local, pra sobreviver a
reinícios do programa e poder ser atualizado sem precisar editar o .env.

O valor inicial vem do .env (INATURALIST_TOKEN); depois disso, quem manda a
palavra final é o arquivo em disco — atualizado sempre que alguém manda um
token novo pelo grupo do Telegram (veja telegram_token_listener.py).
"""

from __future__ import annotations

import pathlib
import threading


class TokenStore:
    def __init__(self, path: pathlib.Path, initial_token: str = ""):
        self._path = path
        self._lock = threading.Lock()
        if not self._path.exists() and initial_token.strip():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(initial_token.strip())

    def get(self) -> str:
        with self._lock:
            if self._path.exists():
                return self._path.read_text().strip()
            return ""

    def set(self, token: str) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(token.strip())
