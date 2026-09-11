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


class InaturalistIdentifier(Identifier):
    """
    Usa a Computer Vision API do iNaturalist (score_image).

    Requer um token de acesso pessoal (veja .env.example para como gerar).
    Docs da API: https://api.inaturalist.org/v1/docs/
    """

    API_URL = "https://api.inaturalist.org/v1/computervision/score_image"

    def __init__(self, token: str, min_confidence: float = 0.15):
        if not token:
            raise ValueError(
                "InaturalistIdentifier precisa de um token (INATURALIST_TOKEN no .env)."
            )
        self.token = token
        self.min_confidence = min_confidence

    def identify(self, frame: np.ndarray) -> Identification | None:
        import cv2
        import requests

        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            return None

        resp = requests.post(
            self.API_URL,
            headers={"Authorization": f"Bearer {self.token}"},
            files={"image": ("frame.jpg", buf.tobytes(), "image/jpeg")},
            data={"locale": "pt-BR"},  # nome comum sempre em português (Brasil)
            timeout=15,
        )
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


def build_identifier(backend: str, inaturalist_token: str | None = None) -> Identifier:
    """Fábrica simples usada pelo main.py a partir da variável IDENTIFY_BACKEND."""
    if backend == "mock":
        return MockIdentifier()
    if backend == "inaturalist":
        return InaturalistIdentifier(token=inaturalist_token or "")
    raise ValueError(f"Backend de identificação desconhecido: {backend!r}")
