"""
Detecção de objetos por YOLO (ultralytics), pra saber SE o que apareceu na
câmera é um ser vivo (não planta) antes de gastar uma chamada de identificação
de espécie com ele.

Substitui a abordagem antiga por diferença de frame / subtração de fundo
(motion.py) — que não conseguia diferenciar "pássaro parado comendo" de
"pássaro foi embora", nem "isso é um pássaro" de "isso é uma folha balançando"
ou "minha mão passou na frente". YOLO já entrega a caixa certa por objeto e
sabe o que cada objeto é, então resolve os dois problemas de uma vez.

Modelo: YOLOv8n ("nano", a versão mais leve/rápida), pré-treinado no COCO.
Baixa o peso (~6MB) automaticamente na primeira execução, com internet.
"""

from __future__ import annotations

import dataclasses

import numpy as np

Box = tuple[int, int, int, int]

# Classes do COCO que contam como "ser vivo, não-planta". A única classe de
# planta no COCO é "potted plant", que fica de fora de propósito.
LIVING_BEING_CLASSES = {
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "person",
}


@dataclasses.dataclass
class Detection:
    box: Box  # (x, y, w, h)
    class_name: str
    confidence: float


class AnimalDetector:
    def __init__(
        self,
        min_confidence: float = 0.4,
        imgsz: int = 480,
        ignore_humans: bool = True,
    ):
        import torch
        from ultralytics import YOLO

        self.model = YOLO("yolov8n.pt")
        # Roda na GPU do Mac (MPS) quando disponível — em CPU pura a inferência
        # fica lenta o bastante pra derrubar o FPS efetivo, o que faz um mesmo
        # bicho "pular" mais entre frames processados do que o rastreamento
        # consegue acompanhar (main.py trata cada salto grande como objeto novo).
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.min_confidence = min_confidence
        self.imgsz = imgsz
        # ignore_humans=True tira "person" da lista de classes aceitas, pra
        # não disparar identificação/notificação toda vez que alguém passa na
        # frente da câmera — controlado pelo IGNORE_HUMANS no .env (main.py).
        self.allowed_classes = LIVING_BEING_CLASSES - {"person"} if ignore_humans else LIVING_BEING_CLASSES

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """
        Retorna só as detecções de seres vivos não-planta (e não-humanos, se
        ignore_humans) acima do limiar de confiança nesse frame. Qualquer
        outro objeto do COCO (cadeira, garrafa, etc.) é descartado aqui mesmo,
        antes de chegar no resto do pipeline.
        """
        results = self.model.predict(
            frame, device=self.device, imgsz=self.imgsz, verbose=False
        )[0]

        detections = []
        for box in results.boxes:
            class_name = self.model.names[int(box.cls[0])]
            confidence = float(box.conf[0])
            if class_name not in self.allowed_classes or confidence < self.min_confidence:
                continue
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append(
                Detection(
                    box=(int(x1), int(y1), int(x2 - x1), int(y2 - y1)),
                    class_name=class_name,
                    confidence=confidence,
                )
            )
        return detections
