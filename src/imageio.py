"""
Gravação de imagem em disco.

Existe por causa de uma pegadinha do OpenCV: cv2.imwrite() não grava nada quando
o caminho tem caractere fora do ASCII (acento, cedilha) — e, pior, não levanta
exceção nenhuma: só devolve False, que ninguém olha. Isso derruba qualquer
máquina cujo caminho do projeto passe por uma pasta tipo "Área de Trabalho":
o avistamento entra no banco normalmente, mas a foto nunca é salva, e o
dashboard fica cheio de miniaturas quebradas sem nenhuma pista do motivo.

cv2.imencode() faz a codificação em memória (sem tocar no sistema de arquivos) e
quem grava é o pathlib, que lida com Unicode sem problema.
"""

from __future__ import annotations

import pathlib

import cv2
import numpy as np


def write_image(path: pathlib.Path, image: np.ndarray) -> None:
    """Grava a imagem no caminho dado. Levanta OSError se não der."""
    extension = path.suffix or ".jpg"
    ok, buffer = cv2.imencode(extension, image)
    if not ok:
        raise OSError(f"não consegui codificar a imagem como '{extension}': {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.tobytes())
