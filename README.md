# Pássaros App — protótipo (Fase 1, 2 e 3 em andamento)

Protótipo do sistema de identificação automática de pássaros pra varanda da casa
de praia. Dá pra testar com a webcam do computador (Fase 1) ou com o ESP32-CAM de
campo, que já está montado e funcionando (Fase 2 — veja a seção
[Hardware de campo](#hardware-de-campo-esp32-cam) mais abaixo).

## Arquitetura

```
webcam/ESP32-CAM -> detecção de ser vivo (YOLO) -> identificação de espécie -> log (SQLite) -> notificação
   capture.py           detector.py                  identify.py            storage.py         notify.py
```

Cada peça é trocável independente das outras (por isso as classes abstratas em
cada módulo). Isso significa: você pode trocar a webcam pelo ESP32-CAM, ou o
Telegram pelo WhatsApp, depois, sem reescrever o resto.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # no Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Rodar em modo 100% de teste (sem nenhuma credencial, só a webcam):

```bash
python -m src.main
```

Isso já valida o pipeline inteiro: câmera liga, detecta quando algo se move na
frente dela, "identifica" (modo mock, sem IA de verdade ainda) e imprime no
terminal + salva no banco (`data/sightings.db`) e a foto (`data/captures/`).

Pra usar o ESP32-CAM de campo em vez da webcam, no `.env`:

```
CAMERA_SOURCE=esp32
ESP32_CAM_URL=http://<ip-da-camera>/capture
```

O IP aparece no Serial Monitor do firmware quando ele conecta no Wi-Fi (veja a
seção [Hardware de campo](#hardware-de-campo-esp32-cam)).

## Ligando a identificação de verdade (iNaturalist)

1. Crie uma conta em [inaturalist.org](https://www.inaturalist.org) (gratuita).
2. Gere um token em https://www.inaturalist.org/users/api_token (válido por
   ~24h).
3. No `.env`: `IDENTIFY_BACKEND=inaturalist` e `INATURALIST_TOKEN=<seu token>`.

O token expira a cada ~24h. Criar uma OAuth Application no iNaturalist pra
automatizar a renovação exige ser "App Owner" aprovado, o que só é liberado
depois de ~2 meses de conta — então em vez disso, a renovação é manual via
Telegram: quando o token expira, o app manda um aviso no grupo, você gera um
token novo (mesmo link do passo 2) e responde no grupo com
`/token SEU_TOKEN_AQUI`. O app salva esse valor em
`data/inaturalist_token.txt` e volta a identificar — sem precisar editar o
`.env` nem reiniciar nada. Precisa do Telegram configurado (próxima seção).

## Ligando a notificação de verdade (Telegram)

1. No Telegram, fale com **@BotFather**, mande `/newbot` e siga o passo a passo.
   Guarde o token que ele te der.
2. Adicione o bot a um grupo com a família (ou converse direto com ele).
3. Mande qualquer mensagem no grupo/chat, depois acesse
   `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` no navegador pra achar o
   `chat_id`.
4. No `.env`: `NOTIFY_BACKEND=telegram`, `TELEGRAM_BOT_TOKEN=...`,
   `TELEGRAM_CHAT_ID=...`.

## Sobre WhatsApp

Dá pra integrar, mas com um trade-off que vale considerar (ver comentário no
topo de `src/notify.py` para o detalhe completo): a via oficial (Meta Cloud API)
exige verificação de conta Business e aprovação de templates; a via não-oficial
(ex: whatsapp-web.js, em Node.js) é mais rápida de configurar mas roda por fora
do suporte oficial do WhatsApp. Recomendo validar tudo com Telegram primeiro, e
se depois de usar vocês preferirem mesmo WhatsApp, a gente troca só essa peça.

## Hardware de campo (ESP32-CAM)

O ESP32-CAM já está montado e o firmware (pasta `passaros-cam-teste/`, projeto
PlatformIO separado) está funcionando: ele conecta no Wi-Fi de casa e serve uma
foto JPEG a cada chamada em `GET /capture` — é isso que o `ESP32CamSource` em
`src/capture.py` consome.

Por que ESP32-CAM e não um Raspberry Pi: não tem tomada perto da estante (só
Wi-Fi), e o ESP32-CAM consome bem menos energia, então uma bateria + painel
solar pequeno dá conta. Também é programado pela mesma Arduino IDE/PlatformIO
que você já usa.

### Gravando o firmware

```bash
cd passaros-cam-teste
cp include/wifi_credentials.h.example include/wifi_credentials.h
# edite include/wifi_credentials.h com o SSID/senha da sua rede
# (esse arquivo é gitignored, nunca é commitado)

pip install -U platformio   # se ainda não tiver o CLI
pio run -t upload --upload-port /dev/cu.usbserial-XXX   # ajuste a porta
```

Depois de gravar, abra o Serial Monitor (115200 baud) pra pegar o IP que a
câmera recebeu — é esse IP que vai no `ESP32_CAM_URL` do `.env` (ver seção
acima).

### Pegadinhas de hardware já resolvidas

- **Sensor é um OV5640, não o OV2640 padrão.** Nos 20MHz de clock "de fábrica"
  (pensados pro OV2640), o OV5640 esquenta e a imagem fica com um véu
  arroxeado que piora com o tempo. Resolvido baixando `xclk_freq_hz` pra 6MHz
  em `passaros-cam-teste/src/main.cpp` — como bônus, as capturas também
  ficaram mais rápidas e consistentes.
- **Conexões TCP mal fechadas travavam a câmera** depois de algumas capturas
  seguidas. Resolvido fechando a conexão explicitamente
  (`Connection: close` + `client.stop()`) e desligando o modo de economia de
  energia do Wi-Fi (`WiFi.setSleep(false)`).
- Mesmo com isso, a latência de cada `/capture` varia bastante (tipicamente
  0.3–8s) — normal pra antena do AI-Thinker. O `ESP32CamSource` já tem
  timeout de 15s e `main.py` já tolera e re-tenta uma captura que falhe.

### Sensor PIR (deep sleep) — em andamento

Pra economizar bateria de verdade (WiFi+câmera ligados o tempo todo custa
muito mais energia que ficar em sono profundo entre detecções), o firmware já
tem a lógica de deep sleep pronta: acorda quando o PIR (HC-SR501) detecta
movimento, serve capturas por um tempo, e volta a dormir sozinho.

Fiação (direto nos pads do módulo ESP32-CAM, sem precisar de resistor):

| HC-SR501 | ESP32-CAM |
|---|---|
| VCC | 5V |
| GND | GND |
| OUT | GPIO 13 |

**Status: validado e funcionando.** Com os fios soldados direto nos pads do
módulo (em vez de jumpers soltos em protoboard, que tinham o GND mal
compartilhado) e o potenciômetro de sensibilidade do PIR ajustado (estava
sensível demais), o ciclo completo funciona: detecta movimento real, acorda do
deep sleep sozinho e volta a dormir sozinho sem movimento. O endpoint
`GET /pir` (estado bruto do pino) ficou no firmware como ferramenta de
diagnóstico remoto, caso precise depurar de novo no futuro sem acesso físico à
placa.

## Próximos passos (Fase 3 em diante)

- Dimensionar e testar a bateria + painel solar com o consumo real do
  ESP32-CAM em deep sleep.
- Proteção contra intempérie (case) pra deixar o conjunto na varanda.
- Montagem definitiva no local de observação + teste prolongado.
- Dashboard web lendo direto de `data/sightings.db`: linha do tempo de
  avistamentos, contagem de espécies diferentes, filtros por data.
- Deploy do pipeline rodando continuamente perto da câmera (ou num servidor,
  puxando imagens do ESP32-CAM pela rede).
