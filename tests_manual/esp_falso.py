"""
ESP32-CAM falso: mesma logica de sono do firmware, pra testar o dashboard sem a
placa. Porta 8099.

  /status     JSON igual ao do firmware
  /capture    JPEG (barras coloridas + relogio, pra dar pra ver que muda)
  /keepalive  POST, 409 depois do teto
  /_dormir    (so no falso) forca dormir agora
  /_acordar   (so no falso) simula o PIR disparando
"""
import io, json, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import numpy as np, cv2

HANGOVER_MS, MAX_AWAKE_MS, LIVE_GRACE_MS, MAX_LIVE_MS = 10000, 120000, 20000, 300000

class Esp:
    def __init__(self):
        self.lock = threading.Lock()
        self.acordar()

    def acordar(self):
        self.boot = time.monotonic()
        self.last_motion = self.boot
        self.live_ate = 0.0
        self.live_inicio = 0.0
        self.teto = False
        self.dormindo = False

    def ms(self, s): return int(s * 1000)

    def sessao_ativa(self):
        if self.live_ate == 0 or self.teto: return False
        agora = time.monotonic()
        if agora - self.live_inicio > MAX_LIVE_MS / 1000:
            self.teto = True
            return False
        return agora < self.live_ate

    def tick(self):
        if self.dormindo: return
        if self.sessao_ativa(): return
        agora = time.monotonic()
        if (agora - self.last_motion) * 1000 > HANGOVER_MS or (agora - self.boot) * 1000 > MAX_AWAKE_MS:
            self.dormindo = True
            self.live_ate = self.live_inicio = 0.0
            self.teto = False

    def dorme_em_ms(self):
        agora = time.monotonic()
        por_mov = max(0, HANGOVER_MS - self.ms(agora - self.last_motion))
        por_teto = max(0, MAX_AWAKE_MS - self.ms(agora - self.boot))
        menor = min(por_mov, por_teto)
        if self.sessao_ativa():
            menor = min(max(0, self.ms(self.live_ate - agora)),
                        max(0, MAX_LIVE_MS - self.ms(agora - self.live_inicio)))
        return menor

    def status(self):
        agora = time.monotonic()
        ativo = self.sessao_ativa()
        return {
            "acordado_ms": self.ms(agora - self.boot),
            "sem_movimento_ms": self.ms(agora - self.last_motion),
            "dorme_em_ms": self.dorme_em_ms(),
            "pir": "HIGH" if (agora - self.last_motion) < 1.5 else "LOW",
            "ao_vivo": ativo,
            "ao_vivo_restante_ms": max(0, MAX_LIVE_MS - self.ms(agora - self.live_inicio)) if ativo else 0,
            "hangover_ms": HANGOVER_MS, "max_awake_ms": MAX_AWAKE_MS, "max_live_ms": MAX_LIVE_MS,
            "motivo_boot": "pir",
        }

    def keepalive(self):
        if self.teto: return None
        agora = time.monotonic()
        if not self.sessao_ativa(): self.live_inicio = agora
        self.live_ate = agora + LIVE_GRACE_MS / 1000
        return self.status()

esp = Esp()

def frame():
    img = np.zeros((480, 640, 3), np.uint8)
    for i, c in enumerate([(60,120,60),(40,90,170),(150,110,40),(90,60,140),(50,150,150)]):
        img[:, i*128:(i+1)*128] = c
    cv2.putText(img, time.strftime("%H:%M:%S"), (150, 260), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255,255,255), 5)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _j(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _rota(self):
        with esp.lock:
            esp.tick()
            p = self.path.split("?")[0]
            if p == "/_acordar":
                esp.acordar(); return ("json", 200, {"ok": "acordei"})
            if p == "/_dormir":
                esp.dormindo = True; return ("json", 200, {"ok": "dormi"})
            if esp.dormindo:
                return ("timeout", 0, None)          # deep sleep: nada responde
            if p == "/status":  return ("json", 200, esp.status())
            if p == "/keepalive":
                s = esp.keepalive()
                if s is None: return ("json", 409, {"erro": "teto da sessao ao vivo atingido", "ao_vivo": False})
                return ("json", 200, s)
            if p == "/capture": return ("jpeg", 200, frame())
            return ("json", 404, {"erro": "nao existe"})

    def _servir(self):
        tipo, code, dados = self._rota()
        if tipo == "timeout":
            time.sleep(30)                            # some, como um ESP dormindo
            return
        if tipo == "json": return self._j(code, dados)
        self.send_response(200); self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(dados))); self.end_headers(); self.wfile.write(dados)

    do_GET = _servir
    do_POST = _servir

print("ESP falso em http://127.0.0.1:8099  (/_dormir e /_acordar pra controlar)")
ThreadingHTTPServer(("127.0.0.1", 8099), H).serve_forever()
