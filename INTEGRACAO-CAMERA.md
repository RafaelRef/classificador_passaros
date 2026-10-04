# Integração da câmera no dashboard — o que falta fazer

Documento de passagem de bastão. A parte de software está escrita e testada
contra um ESP32 simulado; **falta validar no hardware de verdade**, e isso exige
a placa na mão. Este arquivo existe pra quem for terminar — pessoa ou agente —
começar sabendo o que já foi decidido, o que foi verificado e o que não foi.

Quando a integração estiver validada na placa, este arquivo pode ser apagado: o
que é permanente já está no README (seções "Endpoints do firmware" e "A câmera
no dashboard").

---

## O que foi construído

Um cartão no topo do dashboard mostra o estado da câmera e, quando ela está
acordada, abre uma janela com as capturas se renovando.

| camada | arquivo | o que faz |
|---|---|---|
| firmware | `passaros-cam-teste/src/main.cpp` | `GET /status` (JSON com os tempos e a contagem pro sono) e `GET/POST /keepalive` (segura acordado) |
| cliente | `src/esp32cam.py` | fala com o ESP com timeout curto e cache de 3s |
| rotas | `src/dashboard.py` | `/camera/estado`, `/camera/frame`, `/camera/keepalive` |
| interface | `src/templates/dashboard.html` | o cartão, a janela ao vivo e o JS que pinga |

### O problema que isso resolve

Não era mostrar a imagem — era que ela some. O `HANGOVER_MS` do firmware manda
dormir **10 segundos** depois que o PIR para de ver movimento, que é exatamente
quando alguém ainda está olhando. Por isso o firmware ganhou o conceito de
sessão ao vivo: enquanto o dashboard pinga `/keepalive`, as duas condições de
sono ficam suspensas.

Com dois limites, porque o deep sleep existe pra poupar bateria e não pode ser
desligado por acidente:

- **`LIVE_GRACE_MS` (20s)**: um ping só segura por esse tempo. Aba fechada,
  travada, ou Wi-Fi caindo, os pings param e ele dorme sozinho.
- **`MAX_LIVE_MS` (5min)**: teto absoluto da sessão. Ao bater, o ESP recusa
  novos keepalives com HTTP 409 e só volta a aceitar depois de dormir e
  acordar.

> A trava do 409 (`liveTetoAtingido`) não é redundante. Sem ela o teto seria
> burlável: a sessão venceria, o ping seguinte abriria uma sessão nova do zero,
> e uma aba esquecida aberta na cozinha seguraria o ESP acordado até a bateria
> acabar. **Não remova achando que é código morto.**

---

## Decisões já tomadas (não desfaça sem conversar)

**Não existe botão de "acordar a câmera", e isso é intencional.** Em deep sleep
o ESP32 desliga o rádio Wi-Fi e a CPU. Não há nada escutando a rede pra receber
o pedido, e a única fonte de despertar armada é o GPIO 13 (o PIR) ficar HIGH.
Nenhum código no site muda isso. O caminho pra acordar remotamente seria armar
também `esp_sleep_enable_timer_wakeup()`, pro ESP acordar sozinho de tempos em
tempos e consultar um flag no servidor — **foi avaliado e recusado**, porque
custa bateria a cada despertar, mesmo quando ninguém pediu nada, e o projeto
roda em bateria mais painel solar.

**O cartão nunca diz "está dormindo", diz "sem resposta".** Da rede, dormindo,
sem bateria, travado e fora do alcance do Wi-Fi são indistinguíveis. Afirmar
mais que isso seria mentira na cara da família.

**O navegador não fala direto com o ESP.** Ele serve uma conexão por vez e trava
se for bombardeado; o dashboard pode estar aberto em três celulares da casa. O
Flask no meio cacheia o estado por 3s, então N abas viram uma consulta.

**O estado da câmera não entra no render da página.** Vem por `fetch` depois do
carregamento. Se entrasse, todo carregamento de página pagaria o timeout de 2s
quando a câmera estivesse dormindo — que é a maior parte do tempo.

---

## O que foi verificado, e como

Tudo contra `tests_manual/esp_falso.py`, um ESP32 simulado com a mesma lógica de
sono do firmware.

| teste | resultado |
|---|---|
| Keepalive segura além do `HANGOVER_MS` | 52s acordado sem movimento (normal: 10s) |
| Fechar a janela solta o ESP | dorme, cartão vira "sem resposta" |
| Teto da sessão (409) | cliente converte em `estado: "teto"` e para de pingar |
| Cache | 8 consultas viraram 1 ida ao ESP |
| Host morto | "sem resposta" em 2,0s, não trava a página |
| Sem `ESP32_CAM_URL` | cartão e janela nem renderizam; página normal |
| Lógica de sono em uint32 | 10/10, incluindo o rollover do `millis()` aos 49 dias |
| Regressão do dashboard | 6 rotas 200; smoke test do pipeline passa |

## O que NÃO foi verificado

**O firmware nunca foi compilado.** A máquina onde isso foi escrito não tinha
gcc nem PlatformIO. A *lógica* de sono foi validada portando as funções pra
Python com aritmética de 32 bits (`tests_manual/sim_firmware.py`, 10 casos), e a
ordem de definição das funções e o escape do JSON foram conferidos à mão — mas
nada disso substitui um `pio run`. **Assuma que o primeiro build pode falhar.**

Também não foram testados no hardware: a latência real do `/capture` durante uma
sessão ao vivo (o README fala em 0,3 a 8s por captura, o que pode deixar a
janela "ao vivo" bem travada), o consumo de bateria de uma sessão de 5 minutos,
e o comportamento do Wi-Fi do AI-Thinker sob o polling contínuo de frames.

---

## Passo a passo pra validar na placa

Siga nessa ordem. Ela existe pra isolar a camada que falhar: se você for direto
pro site e não funcionar, não vai saber se o problema é firmware, rede ou
Python.

### Passo 1 — gravar o firmware

Precisa da placa conectada por USB, com `include/wifi_credentials.h` já
preenchido (ele é gitignored, então existe só na máquina de quem gravou antes).

```bash
cd passaros-cam-teste && pio run -t upload --upload-port /dev/cu.usbserial-XXX
```

Se não compilar, o erro é aqui e agora — veja "O que NÃO foi verificado". Depois
abra o Serial Monitor a 115200 pra pegar o IP que ele recebeu.

### Passo 2 — testar só o ESP, sem envolver o site

```bash
curl http://<ip-da-camera>/status
```

Tem que sair um JSON com `acordado_ms`, `sem_movimento_ms`, `dorme_em_ms`,
`ao_vivo`, `motivo_boot`. Depois:

```bash
curl -X POST http://<ip-da-camera>/keepalive
```

Tem que voltar `"ao_vivo":true` e `dorme_em_ms` perto de 20000. Se esses dois
responderem, o firmware está bom e todo o resto é software.

### Passo 3 — ligar o dashboard no ESP

No `.env`:

```
ESP32_CAM_URL=http://<ip-da-camera>/capture
```

O dashboard agora precisa de `python-dotenv` e `requests` além do que o atalho
antigo do README instalava. Com o servidor rodando (`python -m src.dashboard`):

```bash
curl http://localhost:5050/camera/estado
```

Se o passo 2 funcionou e esse não, o problema é o `.env` ou firewall.

### Passo 4 — o site

Abra `http://localhost:5050`, ou o IP da máquina na porta 5050 pelo celular.
Acene pro PIR: o cartão tem que ficar verde com a contagem regressiva, e o botão
"Ver ao vivo" aparecer.

O que observar nessa etapa, que é onde o simulador não ajuda:

- Quanto tempo leva entre uma captura e outra na janela ao vivo. Se ficar
  intolerável, o caminho é reduzir a resolução da captura no firmware durante a
  sessão ao vivo, não aumentar o polling.
- Se o ESP aguenta o polling contínuo sem travar o Wi-Fi. O README já registra
  que conexões TCP mal fechadas travavam a câmera; `/capture` já fecha
  explicitamente, mas o polling é um padrão de uso novo.
- Se a sessão sobrevive 5 minutos inteiros até o 409.

### A pegadinha que vai te irritar

**O ESP dorme 10 segundos depois que o movimento para.** Entre um `curl` e outro
ele já foi dormir, e parece que está quebrado. Acene pro PIR antes de cada
teste.

Pra depurar com calma, suba o `HANGOVER_MS` temporariamente em
`passaros-cam-teste/src/main.cpp`:

```c
#define HANGOVER_MS 60000   // 10s -> 1min, só enquanto testa
```

Grave, faça os testes com folga, e **volte pra 10000 e grave de novo** — em 60s
ele gasta 6x mais bateria por detecção.

---

## Trabalhando sem a placa

Dá pra mexer em tudo que não é firmware sem o ESP:

```bash
python tests_manual/esp_falso.py
```

Sobe um ESP32 falso na porta 8099. E no `.env`:
`ESP32_CAM_URL=http://127.0.0.1:8099/capture`.

O falso tem duas rotas que o de verdade não tem, pra controlar o ciclo sem
esperar os tempos reais: `GET /_dormir` e `GET /_acordar`. Quando ele está
"dormindo", ele some da rede (a requisição fica pendurada) em vez de recusar a
conexão — igual ao deep sleep de verdade, que é o que faz o cartão cair em "sem
resposta" pelo timeout e não por um erro.

E a lógica de sono roda sozinha, sem nada instalado:

```bash
python tests_manual/sim_firmware.py
```

---

## Pontas soltas, se sobrar fôlego

- A latência de captura pode pedir uma resolução menor só durante a sessão ao
  vivo (`esp_camera_sensor_get()` mais `set_framesize()` ao entrar na sessão,
  voltando ao sair).
- O `/thumb/` do dashboard guarda miniaturas em memória do processo; com muitos
  avistamentos isso cresce sem teto.
- Nada disso foi testado com dois celulares abrindo a janela ao vivo ao mesmo
  tempo. O cache cobre o `/status`, mas dois pollings de `/capture` simultâneos
  no mesmo ESP é território desconhecido.
