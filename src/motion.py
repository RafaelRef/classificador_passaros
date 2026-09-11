"""
Detecção de movimento por diferença de frames.

Objetivo: só disparar a captura/identificação quando algo realmente se mexeu perto
do prato de frutas, em vez de processar frame a frame o tempo todo. Isso economiza
CPU, bateria (importante já que não tem tomada perto) e chamadas de API de
identificação (que normalmente têm custo ou limite de uso).

É uma abordagem simples e robusta o suficiente para um MVP; dá pra trocar por algo
mais sofisticado (ex: detecção de objeto tipo "isso é um pássaro, não uma folha
balançando") depois, sem mudar o resto do pipeline.
"""

from __future__ import annotations

import cv2
import numpy as np


class MotionDetector:
    def __init__(self, min_area: int = 1500, blur_ksize: int = 21):
        """
        min_area: área mínima (em pixels) de uma região que mudou para considerar
            que houve movimento de verdade (filtra ruído pequeno, folhas, sombra leve).
        blur_ksize: tamanho do blur gaussiano aplicado antes de comparar frames,
            reduz falso-positivo por ruído de sensor da câmera.
        """
        self.min_area = min_area
        self.blur_ksize = blur_ksize
        self._prev_gray: np.ndarray | None = None

    def _preprocess(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (self.blur_ksize, self.blur_ksize), 0)

    def detected(self, frame: np.ndarray) -> bool:
        """Retorna True se houve movimento significativo desde o último frame."""
        gray = self._preprocess(frame)

        if self._prev_gray is None:
            self._prev_gray = gray
            return False

        diff = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray

        thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        return any(cv2.contourArea(c) >= self.min_area for c in contours)
