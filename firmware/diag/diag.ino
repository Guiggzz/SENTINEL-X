// SENTINEL-X : diagnostic du câblage
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#define PIN_DHT D5
#define PIN_PIR D6
#define PIN_BUZ D7
#define PIN_GAS A0
DHT dht(PIN_DHT, DHT22);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
bool oledOk = false;
void setup() {
  Serial.begin(115200); delay(300);
  Serial.println("\n=== DIAG START ===");
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_BUZ, OUTPUT);
  Wire.begin(D2, D1);
  int found = 0;
  for (byte a = 1; a < 127; a++) { Wire.beginTransmission(a); if (Wire.endTransmission() == 0) { Serial.printf("I2C device at 0x%02X\n", a); found++; } }
  if (!found) Serial.println("I2C: aucun appareil");
  byte addr = 0x3C; Wire.beginTransmission(0x3D); if (Wire.endTransmission() == 0) addr = 0x3D;
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, addr);
  Serial.printf("OLED: %s\n", oledOk ? "OK" : "ECHEC");
  dht.begin();
  digitalWrite(PIN_BUZ, HIGH); delay(150); digitalWrite(PIN_BUZ, LOW);
}
void loop() {
  float t = dht.readTemperature(), h = dht.readHumidity();
  int gas = analogRead(PIN_GAS); int pir = digitalRead(PIN_PIR);
  Serial.printf("DATA temp=%s hum=%s gas=%d pir=%d\n", isnan(t) ? "ERR" : String(t,1).c_str(), isnan(h) ? "ERR" : String(h,1).c_str(), gas, pir);
  if (oledOk) { oled.clearDisplay(); oled.setTextSize(1); oled.setTextColor(SSD1306_WHITE); oled.setCursor(0,0);
    oled.println("SENTINEL-X DIAG"); oled.printf("Temp: %s C\n", isnan(t)?"ERR":String(t,1).c_str()); oled.printf("Hum : %s %%\n", isnan(h)?"ERR":String(h,1).c_str());
    oled.printf("Gaz : %d\n", gas); oled.printf("PIR : %s\n", pir?"PRESENCE":"rien"); oled.display(); }
  delay(2000);
}
