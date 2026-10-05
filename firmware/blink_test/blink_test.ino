// Test de communication ESP8266 : LED bleue qui clignote + message série
void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}
void loop() {
  digitalWrite(LED_BUILTIN, LOW);   // LED allumée (logique inversée sur ESP8266)
  Serial.println("SENTINEL-X: ESP8266 OK");
  delay(500);
  digitalWrite(LED_BUILTIN, HIGH);
  delay(500);
}
