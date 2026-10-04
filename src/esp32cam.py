"""
Cliente HTTP do ESP32-CAM, usado pelo dashboard pra mostrar o estado da câmera
e servir a janela "ao vivo".

Por que existe uma camada aqui em vez de o navegador falar direto com o ESP:

- O ESP serve uma conexão por vez e trava se for bombardeado. O dashboard pode
  estar aberto no celular de três pessoas ao mesmo tempo; o cache de estado aqui
  transforma isso em uma consulta a cada CACHE_ESTADO_S, não uma por aba.
- O endereço do ESP sai do .env e não precisa vazar pro HTML.
- Quando ele está dormindo, quem espera o timeout é o servidor, não a página.

IMPORTANTE: "não respondeu" não quer dizer "está dormindo". Dormindo, sem
bateria, travado e fora do alcance do Wi-Fi são indistinguíveis daqui — em deep
sleep o rádio está desligado e não existe ninguém pra recusar a conexão. Por
isso o estado se chama SEM_RESPOSTA e a interface nunca afirma mais que isso.
"""

from __future__ import annotations

import threading
import time
import urllib.parse

import requests

# O ESP cai pra dormir em segundos, então esperar muito por ele não ajuda:
# é melhor dizer "não respondeu" rápido do que segurar a página.
TIMEOUT_ESTADO_S = 2.0
TIMEOUT_KEEPALIVE_S = 3.0
# Capturar é lento no AI-Thinker (0,3 a 8s, segundo o README), então aqui a
# paciência é outra.
TIMEOUT_FRAME_S = 12.0

# Quanto tempo o estado lido do ESP é reaproveitado entre requisições.
CACHE_ESTADO_S = 3.0

ACORDADO = "acordado"
SEM_RESPOSTA = "sem_resposta"
NAO_CONFIGURADO = "nao_configurado"


class ESP32Cam:
    def __init__(self, capture_url: str | None):
        """
        Recebe o mesmo ESP32_CAM_URL que o pipeline usa (ex:
        http://192.168.0.50/capture) e deriva a base dele, pra não precisar de
        uma variável nova no .env de quem já tinha o projeto rodando.
        """
        self.base_url: str | None = None
        if capture_url:
            partes = urllib.parse.urlsplit(capture_url.strip())
            if partes.scheme and partes.netloc:
                self.base_url = f"{partes.scheme}://{partes.netloc}"

        self._lock = threading.Lock()
        self._cache: dict | None = None
        self._cache_em: float = 0.0

    @property
    def configurado(self) -> bool:
        return self.base_url is not None

    def _url(self, caminho: str) -> str:
        return f"{self.base_url}{caminho}"

    def estado(self, forcar: bool = False) -> dict:
        """
        Estado atual da câmera, com cache curto. Nunca levanta exceção: quando o
        ESP não responde, devolve SEM_RESPOSTA — do ponto de vista de quem abre
        a página, "não deu pra falar com a câmera" é uma resposta, não um erro.
        """
        if not self.configurado:
            return {"estado": NAO_CONFIGURADO}

        with self._lock:
            agora = time.monotonic()
            if not forcar and self._cache and (agora - self._cache_em) < CACHE_ESTADO_S:
                return self._cache

            resultado = self._ler_estado()
            self._cache = resultado
            self._cache_em = agora
            return resultado

    def _ler_estado(self) -> dict:
        try:
            resp = requests.get(self._url("/status"), timeout=TIMEOUT_ESTADO_S)
            resp.raise_for_status()
            dados = resp.json()
        except (requests.RequestException, ValueError):
            return {"estado": SEM_RESPOSTA}

        return self._normalizar(dados)

    @staticmethod
    def _normalizar(dados: dict) -> dict:
        """
        Traduz o JSON do firmware pro que a interface precisa. Tolera campo
        faltando: um ESP com firmware antigo (sem /status) nunca chega aqui, mas
        uma versão intermediária pode chegar sem algum campo, e nesse caso é
        melhor mostrar o que dá do que estourar.
        """
        def ms(chave, padrao=0):
            try:
                return max(0, int(dados.get(chave, padrao)))
            except (TypeError, ValueError):
                return padrao

        return {
            "estado": ACORDADO,
            "acordado_ha_s": ms("acordado_ms") // 1000,
            "dorme_em_s": ms("dorme_em_ms") // 1000,
            "ao_vivo": bool(dados.get("ao_vivo")),
            "ao_vivo_restante_s": ms("ao_vivo_restante_ms") // 1000,
            "movimento_agora": dados.get("pir") == "HIGH",
            "acordou_por_movimento": dados.get("motivo_boot") == "pir",
            "teto_sessao_s": ms("max_live_ms") // 1000,
        }

    def keepalive(self) -> dict:
        """
        Pede pro ESP continuar acordado. Devolve o estado já atualizado, e
        {"estado": "teto"} quando o firmware recusa por ter batido o teto da
        sessão ao vivo (HTTP 409) — nesse caso o dashboard para de pingar.
        """
        if not self.configurado:
            return {"estado": NAO_CONFIGURADO}
        try:
            resp = requests.post(self._url("/keepalive"), timeout=TIMEOUT_KEEPALIVE_S)
            if resp.status_code == 409:
                self._invalidar()
                return {"estado": "teto"}
            resp.raise_for_status()
            estado = self._normalizar(resp.json())
        except (requests.RequestException, ValueError):
            self._invalidar()
            return {"estado": SEM_RESPOSTA}

        with self._lock:
            self._cache = estado
            self._cache_em = time.monotonic()
        return estado

    def frame(self) -> bytes | None:
        """Um JPEG da câmera agora, ou None se não deu."""
        if not self.configurado:
            return None
        try:
            resp = requests.get(self._url("/capture"), timeout=TIMEOUT_FRAME_S)
            resp.raise_for_status()
        except requests.RequestException:
            self._invalidar()
            return None
        if not resp.content:
            return None
        return resp.content

    def _invalidar(self) -> None:
        """Descarta o cache: a próxima leitura vai no ESP de novo."""
        with self._lock:
            self._cache = None
            self._cache_em = 0.0
