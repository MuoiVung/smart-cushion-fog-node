/**
 * Smart Cushion - ESP32 Firmware Sample
 * 
 * Hardware: ESP32 (e.g., ESP-32S / DOIT DevKit V1)
 * Libraries required:
 *  - PubSubClient (by Nick O'Leary)
 *  - ArduinoJson (by Benoit Blanchon)
 * 
 * This sketch simulates FSR sensor data and connects to the Fog Node 
 * via MQTT. It also listens for 'vibrate' commands from the Fog Node.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ==========================================
// --- CONFIGURATION (Change these) ---
const char* ssid        = "YOUR_WIFI_SSID";
const char* password    = "YOUR_WIFI_PASSWORD";

// Use mDNS (e.g., "my-macbook.local") or fixed IP of your Fog Node machine
const char* mqtt_server = "fognode.local"; 

// Credentials from your Fog Node .env file
const char* mqtt_user   = "fognode";
const char* mqtt_pass   = "YOUR_MQTT_PASSWORD";
// ==========================================

const int mqtt_port = 1883;
const char* topic_raw = "cushion/raw";
const char* topic_control = "cushion/control";

WiFiClient espClient;
PubSubClient client(espClient);

unsigned long lastMsg = 0;

void setup() {
  // Built-in LED acts as a vibrator simulator
  pinMode(LED_BUILTIN, OUTPUT); 
  digitalWrite(LED_BUILTIN, LOW);
  
  Serial.begin(115200);
  
  // Use floating pin noise for better random numbers
  randomSeed(analogRead(0));

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

/**
 * Connect/Reconnect to Mosquitto Broker
 */
void reconnect() {
  while (!client.connected()) {
    Serial.printf("\nAttempting MQTT connection to '%s'...\n", mqtt_server);
    
    // Unique ID for each connection
    String clientId = "ESP32Cushion-";
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

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop(); 

  unsigned long now = millis();
  
  // Publish simulated raw data every 1 second
  if (now - lastMsg > 1000) {
    lastMsg = now;

    // Simulate 4 FSR sensors
    int t_l = random(500, 4000);
    int t_r = random(500, 4000);
    int b_l = random(500, 4000);
    int b_r = random(500, 4000);

    StaticJsonDocument<256> doc;
    doc["device_id"] = "esp32-real-hardware";
    doc["timestamp"] = now / 1000.0;

    JsonObject sensors = doc.createNestedObject("sensors");
    sensors["fsr_top_left"] = t_l;
    sensors["fsr_top_right"] = t_r;
    sensors["fsr_bottom_left"] = b_l;
    sensors["fsr_bottom_right"] = b_r;
    sensors["temperature"] = 36.5; 

    char buffer[256];
    serializeJson(doc, buffer);
    
    client.publish(topic_raw, buffer);
    
    Serial.print("Published to Fog: ");
    Serial.println(buffer);
  }
}
