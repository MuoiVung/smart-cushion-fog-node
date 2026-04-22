/**
 * Smart Cushion - ESP32 #2 Firmware
 * 
 * Hardware: ESP32 (e.g., ESP-32S / DOIT DevKit V1)
 * Libraries required:
 *  - PubSubClient (by Nick O'Leary)
 *  - ArduinoJson (by Benoit Blanchon)
 * 
 * Function: Reads the remaining 5 FSR sensors (the inner cross of the 3x3 array),
 * then publishes to the Fog Node MQTT broker.
 * NOTE: Uses the exact same pins as ESP1!
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "esp32_secrets.h"

// ==========================================
// --- HARWARE PINS ---
// Both ESP32s use the exact same pins for reading
#define PIN_SENSOR_1 32
#define PIN_SENSOR_2 33
#define PIN_SENSOR_3 34
#define PIN_SENSOR_4 35
#define PIN_SENSOR_5 36 
// ==========================================

const int mqtt_port = 1883;
const char* topic_raw = "cushion/raw";

WiFiClient espClient;
PubSubClient client(espClient);

unsigned long lastMsg = 0;

void setup() {
  Serial.begin(115200);

  // Setup pins
  pinMode(PIN_SENSOR_1, INPUT);
  pinMode(PIN_SENSOR_2, INPUT);
  pinMode(PIN_SENSOR_3, INPUT);
  pinMode(PIN_SENSOR_4, INPUT);
  pinMode(PIN_SENSOR_5, INPUT);

  setup_wifi();
  
  client.setServer(mqtt_server, mqtt_port);
}

void setup_wifi() {
  delay(10);
  Serial.print("\nConnecting to WiFi: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi connected!");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());
}

void reconnect() {
  while (!client.connected()) {
    Serial.printf("\nAttempting MQTT connection to '%s'...\n", mqtt_server);
    
    String clientId = "ESP32-2-Cushion-";
    clientId += String(random(0xffff), HEX);
    
    if (client.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
      Serial.println(">>> FOG NODE CONNECTED! <<<");
      // Node 2 only sends data, the vibration motor is presumed to be on Node 1 (if any)
    } else {
      Serial.print("Failed, rc=");
      Serial.print(client.state());
      Serial.println(" -> Retrying in 5 seconds");
      delay(5000);
    }
  }
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop(); 

  unsigned long now = millis();
  
  // Publish to MQTT every 500ms for smoother real-time data
  if (now - lastMsg > 500) {
    lastMsg = now;

    // Read real FSR pins from inner cross
    // Pin mapping: 32(FM), 33(MM/Center), 34(BM), 35(ML), 36(MR)
    int f_m = analogRead(PIN_SENSOR_1);
    int m_m = analogRead(PIN_SENSOR_2);
    int b_m = analogRead(PIN_SENSOR_3);
    int m_l = analogRead(PIN_SENSOR_4);
    int m_r = analogRead(PIN_SENSOR_5);

    StaticJsonDocument<256> doc;
    doc["device_id"] = "esp32-2";
    doc["timestamp"] = now / 1000.0;

    JsonObject sensors = doc.createNestedObject("sensors");
    sensors["fsr_front_mid"] = f_m;
    sensors["fsr_mid_mid"]   = m_m;
    sensors["fsr_back_mid"]  = b_m;
    sensors["fsr_mid_left"]  = m_l;
    sensors["fsr_mid_right"] = m_r;

    char buffer[256];
    serializeJson(doc, buffer);
    
    client.publish(topic_raw, buffer);
    
    Serial.print("Published ESP2: ");
    Serial.println(buffer);
  }
}
