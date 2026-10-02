#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include "esp_camera.h"
#include "driver/rtc_io.h"
#include "wifi_credentials.h"

// ===== Mapeamento de pinos - ESP32-CAM AI-Thinker =====
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ===== Sensor PIR (HC-SR501) =====
// GPIO13 é um dos poucos pinos livres no ESP32-CAM AI-Thinker (os outros vão
// quase todos pra câmera) e também um dos pinos RTC capazes de acordar o chip
// do deep sleep. A saída do HC-SR501 já é em lógica 3.3V (regulada internamente
// pelo próprio módulo, mesmo alimentado em 5V), então liga direto sem
// conversor de nível: VCC -> 5V, OUT -> GPIO13, GND -> GND.
#define PIR_GPIO_NUM GPIO_NUM_13

// Quanto tempo esperar sem WiFi antes de desistir e voltar a dormir (evita
// ficar acordado (e gastando bateria) indefinidamente se o roteador cair).
#define WIFI_CONNECT_TIMEOUT_MS 15000

// Depois que o PIR volta a ficar baixo (bicho foi embora), continua
// acordado por mais esse tempo — dá margem pro pipeline Python pegar mais
// algumas capturas antes de dormir de novo.
#define HANGOVER_MS 10000

// Teto de segurança: dorme depois desse tempo acordado não importa o quê
// (ex: PIR com defeito travado em HIGH). Evita esvaziar a bateria.
#define MAX_AWAKE_MS 120000

WebServer server(80);
unsigned long lastMotionMs = 0;
unsigned long bootMs = 0;

void handleCapture() {
  uint32_t t0 = millis();
  camera_fb_t *fb = esp_camera_fb_get();
  uint32_t t1 = millis();
  if (!fb) {
    server.send(503, "text/plain", "Falha ao capturar frame");
    return;
  }
  server.sendHeader("Connection", "close");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  WiFiClient client = server.client();
  size_t written = client.write(fb->buf, fb->len);
  client.stop();
  uint32_t t2 = millis();
  size_t fb_len = fb->len;
  esp_camera_fb_return(fb);
  Serial.printf(
    "[capture] grab=%lums send=%lums bytes=%u/%u heap=%u rssi=%d\n",
    (unsigned long)(t1 - t0), (unsigned long)(t2 - t1),
    (unsigned)written, (unsigned)fb_len,
    (unsigned)ESP.getFreeHeap(), WiFi.RSSI()
  );
}

void handlePir() {
  server.send(200, "text/plain", digitalRead(PIR_GPIO_NUM) == HIGH ? "HIGH" : "LOW");
}

void handleRoot() {
  server.send(200, "text/plain", "passaros-cam-teste ok. GET /capture para um JPEG.");
}

void setupCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  // OV5640 é conhecido por esquentar e gerar um véu arroxeado que piora com
  // o tempo quando rodado nos 20MHz "padrão" (pensados pro OV2640). Baixar o
  // clock reduz o aquecimento e resolve isso, ao custo de menos fps.
  config.xclk_freq_hz = 6000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 12;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return;
  }
  Serial.println("Camera init OK!");
}

void goToSleep() {
  Serial.println("[sleep] indo dormir até o PIR detectar movimento de novo...");
  Serial.flush();

  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  esp_camera_deinit();

  // Se o PIR ainda estiver em HIGH (bicho parado bem na frente, ou hold time
  // do módulo ainda não acabou), espera ele baixar — senão o chip acorda de
  // novo instantaneamente ao entrar em deep sleep com ext0 armado em nível
  // alto. Com teto de segurança pra não travar aqui pra sempre.
  unsigned long waitStart = millis();
  while (digitalRead(PIR_GPIO_NUM) == HIGH && (millis() - waitStart) < MAX_AWAKE_MS) {
    delay(100);
  }

  rtc_gpio_pulldown_en(PIR_GPIO_NUM);
  rtc_gpio_pullup_dis(PIR_GPIO_NUM);
  esp_sleep_enable_ext0_wakeup(PIR_GPIO_NUM, 1); // acorda quando o PIR for HIGH
  esp_deep_sleep_start();
}

void logWakeupReason() {
  esp_sleep_wakeup_cause_t reason = esp_sleep_get_wakeup_cause();
  switch (reason) {
    case ESP_SLEEP_WAKEUP_EXT0:
      Serial.println("[boot] acordei por causa do PIR (movimento detectado)");
      break;
    default:
      Serial.println("[boot] boot normal (ligado na tomada/USB, não foi wake de deep sleep)");
      break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  // DIAGNÓSTICO: pulldown interno pra descobrir se o pino está flutuando
  // (sem ligação de verdade) ou se algo está puxando ele pra HIGH de propósito.
  pinMode(PIR_GPIO_NUM, INPUT_PULLDOWN);
  logWakeupReason();

  bootMs = millis();
  lastMotionMs = bootMs;

  setupCamera();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Conectando no Wi-Fi \"%s\"", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - bootMs > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.println("\nWiFi não conectou a tempo, voltando a dormir pra economizar bateria.");
      goToSleep();
    }
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Conectado! IP: ");
  Serial.println(WiFi.localIP());

  // Sem isso, o Wi-Fi entra em modo de economia de energia e cada captura
  // HTTP fica progressivamente mais lenta até travar.
  WiFi.setSleep(false);

  server.on("/", handleRoot);
  server.on("/capture", handleCapture);
  server.on("/pir", handlePir);
  server.begin();
  Serial.println("Servidor HTTP no ar. Capture em /capture");
}

void loop() {
  server.handleClient();

  if (digitalRead(PIR_GPIO_NUM) == HIGH) {
    lastMotionMs = millis();
  }

  unsigned long now = millis();
  bool semMovimentoHaTempoDemais = (now - lastMotionMs) > HANGOVER_MS;
  bool acordadoHaTempoDemais = (now - bootMs) > MAX_AWAKE_MS;
  if (semMovimentoHaTempoDemais || acordadoHaTempoDemais) {
    goToSleep();
  }
}
