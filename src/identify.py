"""
Identificação da espécie a partir de uma imagem.

Backend plugável: comece com MockIdentifier pra validar o pipeline inteiro sem
precisar de nenhuma conta/token. Quando quiser identificação de verdade, troque
para InaturalistIdentifier (precisa de um token gratuito do iNaturalist).

Por que iNaturalist como primeira opção "real": é a API com melhor cobertura de
fauna brasileira entre as opções gratuitas/acessíveis, já que o modelo é treinado
com dados da comunidade global (inclusive observadores brasileiros), diferente de
produtos tipo Bird Buddy que são focados em América do Norte/Europa.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Callable

import numpy as np


@dataclasses.dataclass
class Identification:
    species_common_name: str
    species_scientific_name: str | None
    confidence: float  # 0.0 a 1.0
    source: str  # nome do backend que gerou essa identificação
    taxon_id: int | None = None  # id do táxon no iNaturalist, usado pra montar o link


class Identifier(abc.ABC):
    @abc.abstractmethod
    def identify(self, frame: np.ndarray) -> Identification | None:
        """Retorna a melhor identificação para o frame, ou None se não conseguir."""


class MockIdentifier(Identifier):
    """
    Não chama nenhuma API. Serve só para testar o pipeline (captura -> movimento ->
    'identificação' -> log -> notificação) de ponta a ponta antes de configurar
    qualquer credencial.
    """

    def identify(self, frame: np.ndarray) -> Identification | None:
        return Identification(
            species_common_name="Pássaro não identificado (modo mock)",
            species_scientific_name=None,
            confidence=0.0,
            source="mock",
        )


class InaturalistTokenExpired(Exception):
    """Levantado quando o token salvo não é mais aceito pela API (HTTP 401)."""


class InaturalistIdentifier(Identifier):
    """
    Usa a Computer Vision API do iNaturalist (score_image).

    Requer um token de acesso pessoal (veja .env.example para como gerar). O
    token dura só ~24h, então em vez de um valor fixo recebemos um
    "token_provider": uma função que devolve o token mais atual (veja
    TokenStore em token_store.py, atualizado via Telegram em
    telegram_token_listener.py).

    Docs da API: https://api.inaturalist.org/v1/docs/
    """

    API_URL = "https://api.inaturalist.org/v1/computervision/score_image"

    def __init__(self, token_provider: Callable[[], str], min_confidence: float = 0.15):
        self.token_provider = token_provider
        self.min_confidence = min_confidence

    def identify(self, frame: np.ndarray) -> Identification | None:
        import cv2
        import requests

        token = self.token_provider()
        if not token:
            raise InaturalistTokenExpired("Nenhum token do iNaturalist configurado ainda.")

        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            return None

        resp = requests.post(
            self.API_URL,
            headers={"Authorization": f"Bearer {token}"},
            files={"image": ("frame.jpg", buf.tobytes(), "image/jpeg")},
            data={"locale": "pt-BR"},  # nome comum sempre em português (Brasil)
            timeout=15,
        )
        if resp.status_code == 401:
            raise InaturalistTokenExpired("Token do iNaturalist expirado ou inválido.")
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        top = results[0]
        taxon = top.get("taxon", {})
        confidence = float(top.get("combined_score", 0)) / 100.0

        if confidence < self.min_confidence:
            return None

        return Identification(
            species_common_name=taxon.get("preferred_common_name") or taxon.get("name", "?"),
            species_scientific_name=taxon.get("name"),
            confidence=confidence,
            source="inaturalist",
            taxon_id=taxon.get("id"),
        )


def build_identifier(
    backend: str, inaturalist_token_provider: Callable[[], str] | None = None
) -> Identifier:
    """Fábrica simples usada pelo main.py a partir da variável IDENTIFY_BACKEND."""
    if backend == "mock":
        return MockIdentifier()
    if backend == "inaturalist":
        if inaturalist_token_provider is None:
            raise ValueError("InaturalistIdentifier precisa de um inaturalist_token_provider.")
        return InaturalistIdentifier(token_provider=inaturalist_token_provider)
    raise ValueError(f"Backend de identificação desconhecido: {backend!r}")
