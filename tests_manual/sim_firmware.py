"""Porte fiel das funcoes de sono do main.cpp, com aritmetica uint32 do ESP32."""
U32 = 0xFFFFFFFF
HANGOVER_MS, MAX_AWAKE_MS, LIVE_GRACE_MS, MAX_LIVE_MS = 10000, 120000, 20000, 300000

def u32(x): return x & U32
def i32(x):
    x = u32(x)
    return x - (1 << 32) if x >= (1 << 31) else x

class Esp:
    def __init__(self, boot_em=0):
        self.agora = u32(boot_em)
        self.bootMs = self.agora
        self.lastMotionMs = self.agora
        self.liveAteMs = 0
        self.liveInicioMs = 0
        self.liveTetoAtingido = False
        self.dormiu = False

    def avanca(self, ms):
        for _ in range(0, ms, 100):
            self.agora = u32(self.agora + 100)
            self.loop()
        return self

    @staticmethod
    def restante(decorrido, limite):
        return 0 if decorrido >= limite else u32(limite - decorrido)

    def sessao_ativa(self):
        if self.liveAteMs == 0 or self.liveTetoAtingido: return False
        if u32(self.agora - self.liveInicioMs) > MAX_LIVE_MS:
            self.liveTetoAtingido = True
            return False
        if i32(self.agora - self.liveAteMs) >= 0: return False
        return True

    def dorme_em(self):
        por_mov = self.restante(u32(self.agora - self.lastMotionMs), HANGOVER_MS)
        por_teto = self.restante(u32(self.agora - self.bootMs), MAX_AWAKE_MS)
        menor = min(por_mov, por_teto)
        if self.sessao_ativa():
            por_ka = u32(self.liveAteMs - self.agora) if i32(self.liveAteMs - self.agora) > 0 else 0
            por_tl = self.restante(u32(self.agora - self.liveInicioMs), MAX_LIVE_MS)
            menor = min(por_ka, por_tl)
        return menor

    def keepalive(self):
        if self.liveTetoAtingido: return 409
        if not self.sessao_ativa(): self.liveInicioMs = self.agora
        self.liveAteMs = u32(self.agora + LIVE_GRACE_MS) or 1
        return 200

    def movimento(self): self.lastMotionMs = self.agora

    def loop(self):
        if self.dormiu: return
        if self.sessao_ativa(): return
        if u32(self.agora - self.lastMotionMs) > HANGOVER_MS or u32(self.agora - self.bootMs) > MAX_AWAKE_MS:
            self.dormiu = True

def t(nome, cond):
    print(f"{'OK  ' if cond else 'FALHA'} {nome}")
    return cond

ok = []
# 1. sem nada, dorme ~10s depois do boot
e = Esp(); ok.append(t("dorme 10s apos o boot sem movimento", Esp().avanca(9000).dormiu is False and Esp().avanca(11000).dormiu))
# 2. contagem regressiva bate
e = Esp(); e.avanca(4000); ok.append(t("contagem = 6s apos 4s parado", e.dorme_em() == 6000))
# 3. keepalive segura alem dos 10s e dos 120s
e = Esp()
for _ in range(40):           # 40 pings de 5s = 200s, passa dos 120s de MAX_AWAKE
    e.keepalive(); e.avanca(5000)
ok.append(t("keepalive segura acordado alem do MAX_AWAKE_MS", not e.dormiu))
# 4. parou de pingar -> dorme em ate LIVE_GRACE
e = Esp(); e.keepalive(); e.avanca(19000)
ok.append(t("ainda acordado 19s apos ultimo ping", not e.dormiu))
e.avanca(3000); ok.append(t("dormiu depois do keepalive vencer (20s)", e.dormiu))
# 5. teto de 5min e inviolavel
e = Esp()
for _ in range(80):           # 80 x 5s = 400s > MAX_LIVE_MS(300s)
    e.keepalive(); e.avanca(5000)
ok.append(t("teto MAX_LIVE_MS trava a sessao", e.liveTetoAtingido))
ok.append(t("keepalive apos o teto devolve 409", e.keepalive() == 409))
e2 = Esp(); e2.liveTetoAtingido = True; e2.keepalive(); e2.avanca(15000)
ok.append(t("com teto atingido, volta a dormir normal", e2.dormiu))
# 6. rollover do millis(): boot pertinho do limite de 32 bits
e = Esp(boot_em=U32 - 5000)   # da a volta 5s depois do boot
e.keepalive()
e.avanca(10000)               # atravessa o ponto onde millis() zera
ok.append(t("sessao sobrevive ao millis() dar a volta", not e.dormiu and e.sessao_ativa()))
e.avanca(15000)
ok.append(t("e dorme certo depois da volta", e.dormiu))

print()
print(f"{sum(ok)}/{len(ok)} passaram")
