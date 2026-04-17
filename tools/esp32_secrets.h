#ifndef ESP32_SECRETS_H
#define ESP32_SECRETS_H

// ==========================================
// --- SECRETS & CREDENTIALS ---
// Do not commit this file to version control (*.gitignore)
// ==========================================

const char *ssid = "Aiot_Lab";
const char *password = "118ntustAiot";

// Use mDNS (e.g., "my-macbook.local") or fixed IP of your Fog Node machine
const char *mqtt_server = "192.168.50.161";

// Credentials from your Fog Node .env file
const char *mqtt_user = "fognode";
const char *mqtt_pass = "vnaiot";

#endif
