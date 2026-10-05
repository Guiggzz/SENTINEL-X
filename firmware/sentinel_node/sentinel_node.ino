// SENTINEL-X Edge Node - ESP8266 NodeMCU v3
// DHT22 D5 | PIR D6 | MQ-2 A0 | OLED SDA D2 / SCL D1 | Buzzer D7 | LED rouge D0 | LED verte D8
// MQTT : sentinel/<id>/telemetry (2 s), sentinel/<id>/alerts (evenements),
//        sentinel/<id>/status (online/offline, retained), sentinel/<id>/cmd (commandes)
// Commandes JSON sur /cmd :
//   {"action":"buzzer_on","duration_ms":3000,"song":"paquetta"|"rickroll"|"gaz"|"intrus"} (duration_ms 0 = boucle) {"action":"buzzer_off"} {"action":"beep","duration_ms":2000} (bip d'armement)
//   {"action":"led","color":"red"|"green","state":true|false}  {"action":"led_auto"}
#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include "secrets.h"
#include "rickroll.h"
#include "paquetta.h"
#include "alarms.h"

#define DEVICE_ID "sentinel-node-01"
#define PIN_DHT D5
#define PIN_PIR D6
#define PIN_BUZ D7
#define PIN_RED D0
#define PIN_GREEN D8
#define PIN_GAS A0
const unsigned long TELEMETRY_MS = 500;   // envoi telemetrie 0,5 s
const unsigned long DHT_MS = 2000;        // le DHT22 ne mesure pas plus vite que 2 s
// PIR : une detection = impulsion de PIR_HOLD_MS, puis pause ; il faut que la sortie
// redescende (PIR_REARM_LOW_MS) avant qu'une nouvelle detection soit comptee.
const unsigned long PIR_CONFIRM_MS = 250;
const unsigned long PIR_HOLD_MS = 5000;
const unsigned long PIR_REARM_LOW_MS = 300;

const char* T_TELE   = "sentinel/" DEVICE_ID "/telemetry";
const char* T_ALERT  = "sentinel/" DEVICE_ID "/alerts";
const char* T_STATUS = "sentinel/" DEVICE_ID "/status";
const char* T_CMD    = "sentinel/" DEVICE_ID "/cmd";

DHT dht(PIN_DHT, DHT22);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
WiFiClientSecure net;
PubSubClient mqtt(net);

bool oledOk = false, buzzerOn = false, ledManual = false, redOn = false, greenOn = false;
int lastPir = -1;            // etat presence publie (0/1)
unsigned long pirHighSince = 0, pirLowSince = 0, pirHoldUntil = 0, lastDht = 0;
bool pirArmed = true;
unsigned long lastTele = 0, buzzerUntil = 0, lastMqttTry = 0;
float lastT = NAN, lastH = NAN; int lastGas = 0;

// Buzzer passif : il faut lui envoyer une frequence (tone), sinon il fait juste "clic".
// song = true : l'alarme joue le refrain de Never Gonna Give You Up en boucle (lecture non bloquante).
bool songMode = false; int songIdx = 0; unsigned long noteEnd = 0;
enum SongId : uint8_t { SONG_PAQ = 0, SONG_RICK = 1, SONG_GAZ = 2, SONG_INTRUS = 3 };
uint8_t songId = SONG_RICK;   // gaz = sirene IA gaz/fumee, intrus = alarme presence (voir alarms.h)
uint8_t songFromName(const char* n) {
  if (!strcmp(n, "paquetta")) return SONG_PAQ;
  if (!strcmp(n, "gaz")) return SONG_GAZ;
  if (!strcmp(n, "intrus")) return SONG_INTRUS;
  return SONG_RICK;
}
void setBuzzer(bool on, unsigned long ms = 0, bool song = false, uint8_t id = SONG_RICK) {
  buzzerOn = on; songId = id; songMode = on && song; songIdx = 0; noteEnd = 0;
  if (on && !song) tone(PIN_BUZ, 2700);
  if (!on) { noTone(PIN_BUZ); digitalWrite(PIN_BUZ, LOW); }
  buzzerUntil = (on && ms) ? millis() + ms : 0;
}
void updateSong() {
  if (!songMode || millis() < noteEnd) return;
  const int* mel = PAQ_MELODY; int count = PAQ_NOTES;
  if (songId == SONG_RICK) count = RICK_NOTES;
  else if (songId == SONG_GAZ) { mel = GAZ_PATTERN; count = GAZ_STEPS; }
  else if (songId == SONG_INTRUS) { mel = INTRUS_PATTERN; count = INTRUS_STEPS; }
  if (songIdx >= count) songIdx = 0;                    // on reboucle sur la melodie
  int freq; unsigned long dur;
  if (songId == SONG_RICK) {
    freq = RICK_MELODY[songIdx * 2]; int div = RICK_MELODY[songIdx * 2 + 1];
    unsigned long whole = (60000UL * 4) / RICK_TEMPO;
    dur = div > 0 ? whole / div : (whole / -div) * 3 / 2;
  } else { freq = mel[songIdx * 2]; dur = mel[songIdx * 2 + 1]; }
  bool siren = songId == SONG_GAZ || songId == SONG_INTRUS;   // sirenes : son continu, silences explicites
  if (freq > 0) { if (siren) tone(PIN_BUZ, freq); else tone(PIN_BUZ, freq, dur * 9 / 10); } else noTone(PIN_BUZ);
  noteEnd = millis() + dur; songIdx++;
}
void setLeds(bool red, bool green) { redOn = red; greenOn = green; digitalWrite(PIN_RED, red); digitalWrite(PIN_GREEN, green); }

// Mode auto : vert = connecte et RAS ; rouge = presence ou alarme ; rouge clignotant = deconnecte
void updateLedsAuto() {
  if (ledManual) return;
  bool online = WiFi.status() == WL_CONNECTED && mqtt.connected();
  if (!online) setLeds((millis() / 500) % 2, false);
  else if (buzzerOn || lastPir == 1) setLeds(true, false);
  else setLeds(false, true);
}

void publishAlert(const char* type, const char* state) {
  JsonDocument doc;
  doc["device_id"] = DEVICE_ID; doc["type"] = type; doc["state"] = state; doc["uptime_ms"] = millis();
  char buf[192]; serializeJson(doc, buf);
  if (mqtt.connected()) mqtt.publish(T_ALERT, buf);
  Serial.printf("ALERT %s\n", buf);
}

void onCommand(char* topic, byte* payload, unsigned int len) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) { Serial.println("CMD: JSON invalide"); return; }
  const char* action = doc["action"] | "";
  Serial.printf("CMD %s\n", action);
  if (!strcmp(action, "buzzer_on")) { const char* song = doc["song"] | "rickroll"; setBuzzer(true, doc["duration_ms"] | 0, true, songFromName(song)); publishAlert("actuator", "buzzer_on"); }
  else if (!strcmp(action, "buzzer_off")) { setBuzzer(false); publishAlert("actuator", "buzzer_off"); }
  else if (!strcmp(action, "beep")) { unsigned long d = doc["duration_ms"] | 600; setBuzzer(true, d > 5000 ? 5000 : d); publishAlert("actuator", "beep"); }
  else if (!strcmp(action, "led")) {
    ledManual = true; const char* c = doc["color"] | ""; bool s = doc["state"] | false;
    if (!strcmp(c, "red")) setLeds(s, greenOn); else if (!strcmp(c, "green")) setLeds(redOn, s);
    publishAlert("actuator", "led_manual");
  }
  else if (!strcmp(action, "led_auto")) { ledManual = false; publishAlert("actuator", "led_auto"); }
  else Serial.println("CMD inconnue");
}

void drawScreen() {
  if (!oledOk) return;
  oled.clearDisplay(); oled.setTextSize(1); oled.setTextColor(SSD1306_WHITE); oled.setCursor(0, 0);
  oled.println("SENTINEL-X  node-01");
  if (WiFi.status() == WL_CONNECTED) oled.printf("IP %s\n", WiFi.localIP().toString().c_str());
  else oled.println("WiFi: connexion...");
  oled.printf("MQTTS: %s\n", mqtt.connected() ? "OK" : "deconnecte");
  oled.printf("Temp %s C  Hum %s%%\n", isnan(lastT) ? "--" : String(lastT, 1).c_str(), isnan(lastH) ? "--" : String(lastH, 0).c_str());
  oled.printf("Gaz  %d\n", lastGas);
  oled.printf("PIR  %s\n", lastPir == 1 ? "PRESENCE" : "rien");
  if (buzzerOn) oled.println(songMode && songId == SONG_GAZ ? "!! ALERTE GAZ !!" : songMode && songId == SONG_INTRUS ? "!! INTRUSION !!" : "!! ALARME !!");
  oled.display();
}

void ensureMqtt() {
  if (mqtt.connected() || WiFi.status() != WL_CONNECTED || millis() - lastMqttTry < 3000) return;
  lastMqttTry = millis();
  Serial.printf("MQTTS -> %s:%d ... ", MQTT_HOST, MQTT_PORT);
  if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD, T_STATUS, 1, true, "offline")) {
    Serial.println("OK");
    mqtt.publish(T_STATUS, "online", true);
    mqtt.subscribe(T_CMD);
  } else Serial.printf("echec (%d)\n", mqtt.state());
}

void setup() {
  Serial.begin(115200); delay(200);
  pinMode(PIN_PIR, INPUT); pinMode(PIN_BUZ, OUTPUT); pinMode(PIN_RED, OUTPUT); pinMode(PIN_GREEN, OUTPUT);
  setBuzzer(false); setLeds(true, true); delay(300); setLeds(false, false);   // test LEDs au demarrage
  Wire.begin(D2, D1);
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  dht.begin();
  WiFi.mode(WIFI_STA); WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  // Pin SHA1 du certificat serveur (hors-ligne, sans NTP). Buffers TLS élargis pour handshake ECDSA.
  net.setBufferSizes(2048, 512);
  net.setFingerprint(MQTT_CERT_FINGERPRINT);
  mqtt.setServer(MQTT_HOST, MQTT_PORT); mqtt.setCallback(onCommand); mqtt.setBufferSize(1024);
  mqtt.setSocketTimeout(15);
  Serial.printf("\nSENTINEL-X %s, WiFi %s\n", DEVICE_ID, WIFI_SSID);
}

void loop() {
  static wl_status_t lastWifi = WL_IDLE_STATUS;
  if (WiFi.status() != lastWifi) { lastWifi = WiFi.status(); if (lastWifi == WL_CONNECTED) Serial.printf("WiFi OK, IP %s\n", WiFi.localIP().toString().c_str()); }
  ensureMqtt(); mqtt.loop();
  if (buzzerUntil && millis() > buzzerUntil) { setBuzzer(false); publishAlert("actuator", "buzzer_off"); }

  // --- PIR anti-rebond + pause 5 s ---
  int raw = digitalRead(PIN_PIR);
  unsigned long nowMs = millis();
  if (raw) { if (!pirHighSince) pirHighSince = nowMs; pirLowSince = 0; }
  else     { if (!pirLowSince) pirLowSince = nowMs; pirHighSince = 0; }
  if (!pirArmed && pirLowSince && nowMs - pirLowSince >= PIR_REARM_LOW_MS && !pirHoldUntil) pirArmed = true;
  int pir = lastPir < 0 ? 0 : lastPir;
  if (pirHoldUntil) {
    if (nowMs >= pirHoldUntil) {
      if (raw) pirHoldUntil = nowMs + PIR_HOLD_MS;                 // toujours du mouvement : on prolonge 5 s
      else { pirHoldUntil = 0; pir = 0; pirArmed = true; }         // zone libre : on relache et on rearme
    }
  } else if (pirArmed && pirHighSince && nowMs - pirHighSince >= PIR_CONFIRM_MS) {
    pir = 1; pirHoldUntil = nowMs + PIR_HOLD_MS; pirArmed = false; // detection confirmee
  }
  if (pir != lastPir) { if (lastPir != -1) publishAlert("presence", pir ? "detected" : "cleared"); lastPir = pir; drawScreen(); }
  updateSong();
  updateLedsAuto();

  if (millis() - lastTele >= TELEMETRY_MS) {
    lastTele = millis();
    float t = NAN, h = NAN;
    if (millis() - lastDht >= DHT_MS || lastDht == 0) {
      lastDht = millis();
      t = dht.readTemperature(); h = dht.readHumidity();
      if (!isnan(t)) lastT = t;
      if (!isnan(h)) lastH = h;
    }
    if (isnan(t)) t = lastT;   // entre deux mesures DHT : derniere valeur
    if (isnan(h)) h = lastH;
    lastGas = analogRead(PIN_GAS);
    JsonDocument doc;
    doc["device_id"] = DEVICE_ID; doc["uptime_ms"] = millis();
    if (isnan(t)) doc["temperature"] = nullptr; else doc["temperature"] = round(t * 10) / 10.0;
    if (isnan(h)) doc["humidity"] = nullptr; else doc["humidity"] = round(h * 10) / 10.0;
    doc["gas"] = lastGas; doc["presence"] = pir == 1; doc["buzzer"] = buzzerOn;
    doc["led_red"] = redOn; doc["led_green"] = greenOn; doc["rssi"] = WiFi.RSSI();
    char buf[320]; serializeJson(doc, buf);
    if (mqtt.connected()) mqtt.publish(T_TELE, buf);
    static unsigned long lastDraw = 0;
    if (millis() - lastDraw >= 1000) { lastDraw = millis(); drawScreen(); }
  }
}
