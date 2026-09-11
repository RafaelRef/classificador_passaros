"""
Fonte de imagens do pipeline.

Fase 1 (agora): webcam USB ligada no computador (via OpenCV), pra testar tudo
sem precisar do hardware final.

Fase 2 (quando o ESP32-CAM/Raspberry Pi chegar): troca-se só esta classe por uma
que busca o frame via HTTP do ESP32-CAM (ele serve uma imagem JPEG numa URL tipo
http://<ip-da-camera>/capture). O resto do pipeline (motion, identify, notify,
storage) não muda nada.
"""

from __future__ import annotations

import abc

import cv2
import numpy as np


class FrameSource(abc.ABC):
    """Interface comum para qualquer fonte de frames (webcam, ESP32-CAM, arquivo...)."""

    @abc.abstractmethod
    def read(self) -> np.ndarray | None:
        """Retorna o frame mais recente (BGR, formato OpenCV) ou None se falhar."""

    def release(self) -> None:
        pass


class WebcamSource(FrameSource):
    """Webcam USB comum, ligada direto no computador. Bom para desenvolver/testar."""

    def __init__(self, camera_index: int = 0):
        self.camera_index = camera_index
        self._cap = cv2.VideoCapture(camera_index)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Não consegui abrir a webcam no índice {camera_index}. "
                "Confira se ela está conectada e se nenhum outro app está usando."
            )

    def read(self) -> np.ndarray | None:
        ok, frame = self._cap.read()
        return frame if ok else None

    def release(self) -> None:
        self._cap.release()


class ESP32CamSource(FrameSource):
    """
    Fonte de frames a partir de um ESP32-CAM na rede local.

    Esqueleto pronto para quando o hardware de campo chegar: o firmware padrão do
    ESP32-CAM (ex: CameraWebServer, exemplo oficial do Arduino IDE) expõe uma URL
    tipo http://<ip>/capture que retorna um JPEG a cada chamada.
    """

    def __init__(self, capture_url: str, timeout_s: float = 5.0):
        self.capture_url = capture_url
        self.timeout_s = timeout_s

    def read(self) -> np.ndarray | None:
        import requests

        try:
            resp = requests.get(self.capture_url, timeout=self.timeout_s)
            resp.raise_for_status()
        except requests.RequestException:
            return None
        arr = np.frombuffer(resp.content, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
