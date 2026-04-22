/**
 * Smart Cushion - ESP32 #1 Firmware
 * 
 * Hardware: ESP32 (e.g., ESP-32S / DOIT DevKit V1)
 * Libraries required:
 *  - PubSubClient (by Nick O'Leary)
 *  - ArduinoJson (by Benoit Blanchon)
 * 
 * Function: Reads the 4 corner FSR sensors and the NTC temperature sensor,
 * then publishes to the Fog Node MQTT broker.
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
#define PIN_SENSOR_5 36 // NTC on ESP1, FSR on ESP2
// ==========================================

const int mqtt_port = 1883;
const char* topic_raw = "cushion/raw";
const char* topic_control = "cushion/control";

WiFiClient espClient;
PubSubClient client(espClient);

unsigned long lastMsg = 0;

void setup() {
  pinMode(LED_BUILTIN, OUTPUT); 
  digitalWrite(LED_BUILTIN, LOW);
  
  Serial.begin(115200);

  // Setup pins
  pinMode(PIN_SENSOR_1, INPUT);
  pinMode(PIN_SENSOR_2, INPUT);
  pinMode(PIN_SENSOR_3, INPUT);
  pinMode(PIN_SENSOR_4, INPUT);
  pinMode(PIN_SENSOR_5, INPUT);

  setup_wifi();
  
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
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

/**
 * Handle incoming MQTT commands (Fog -> ESP32)
 */
void callback(char* topic, byte* payload, unsigned int length) {
  Serial.print("\n[MQTT RECEIVED] Topic: "); 
  Serial.println(topic);
  
  StaticJsonDocument<200> doc;
  DeserializationError error = deserializeJson(doc, payload, length);
  
  if (error) {
    Serial.print("JSON Parse failed: ");
    Serial.println(error.f_str());
    return;
  }

  const char* command = doc["command"]; 
  int duration = doc["duration_ms"];    
  
  if (strcmp(command, "vibrate") == 0) {
    Serial.printf(">>> ALERT: Vibrating for %d ms! <<<\n", duration);
    digitalWrite(LED_BUILTIN, HIGH);
    delay(duration);
    digitalWrite(LED_BUILTIN, LOW);
  }
}

void reconnect() {
  while (!client.connected()) {
    Serial.printf("\nAttempting MQTT connection to '%s'...\n", mqtt_server);
    
    String clientId = "ESP32-1-Cushion-";
    clientId += String(random(0xffff), HEX);
    
    if (client.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
      Serial.println(">>> FOG NODE CONNECTED! <<<");
      client.subscribe(topic_control);
    } else {
      Serial.print("Failed, rc=");
      Serial.print(client.state());
      Serial.println(" -> Retrying in 5 seconds");
      delay(5000);
    }
  }
}

float convertNTCToTemperature(int analogValue) {
  // Dummy conversion for NTC
  if(analogValue == 0) return 25.0;
  float resistance = (4095.0 / analogValue) - 1.0;
  resistance = 10000.0 / resistance;
  float steinhart;
  steinhart = resistance / 10000.0;     
  steinhart = log(steinhart);                  
  steinhart /= 3950.0;                  
  steinhart += 1.0 / (25.0 + 273.15); 
  steinhart = 1.0 / steinhart;                 
  steinhart -= 273.15;                         
  return steinhart;
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

    // Read real FSR pins
    int fl = analogRead(PIN_SENSOR_1);
    int fr = analogRead(PIN_SENSOR_2);
    int bl = analogRead(PIN_SENSOR_3);
    int br = analogRead(PIN_SENSOR_4);
    
    // Read NTC pin and convert
    int ntcRaw = analogRead(PIN_SENSOR_5); 
    float temp = convertNTCToTemperature(ntcRaw);

    StaticJsonDocument<256> doc;
    doc["device_id"] = "esp32-1";
    doc["timestamp"] = now / 1000.0;

    JsonObject sensors = doc.createNestedObject("sensors");
    sensors["fsr_front_left"]  = fl;
    sensors["fsr_front_right"] = fr;
    sensors["fsr_back_left"]   = bl;
    sensors["fsr_back_right"]  = br;
    sensors["temperature"]     = temp; 

    char buffer[256];
    serializeJson(doc, buffer);
    
    client.publish(topic_raw, buffer);
    
    Serial.print("Published ESP1: ");
    Serial.println(buffer);
  }
}
