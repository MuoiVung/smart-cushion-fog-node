# Cloud Integration Requirements

To complete the end-to-end integration between the Smart Cushion Fog Node and the Cloud (AWS IoT Core), the Cloud development team must provide the following configuration details and security certificates to the Fog Node deployment team.

## 1. Environment Configuration

The following parameters must be configured in the `.env` file of the Fog Node. The Cloud team needs to provide the exact values for the production environment:

| Environment Variable | Description | Example / Note |
| :--- | :--- | :--- |
| `AWS_ENDPOINT` | The AWS IoT Core device data endpoint. | `xxxxxxxxxxxxxx-ats.iot.ap-southeast-1.amazonaws.com` |
| `AWS_CLIENT_ID` | A unique identifier for the Fog Node MQTT client. | `smart-cushion-fog-01` (Must be unique per Fog Node) |
| `AWS_TOPIC_EVENT` | MQTT topic for Event records. | `cushion/{device_id}/event` |
| `AWS_TOPIC_TELEMETRY` | MQTT topic for Telemetry records. | `cushion/{device_id}/telemetry` |
| `AWS_TOPIC_SUMMARY` | MQTT topic for Summary records. | `cushion/{device_id}/summary` |

*Note: `{device_id}` in the topics will be dynamically replaced by the Fog Node based on its `DEVICE_ID` environment variable (e.g., `cushion-01`).*

## 2. Security Certificates (mTLS)

AWS IoT Core requires Mutual TLS (mTLS) for authentication. The Cloud team must generate a device certificate in AWS IoT Core, attach a policy allowing it to publish to the defined topics, and provide the following 3 files:

1.  **Device Certificate:** (e.g., `xxxxxxxxxx-certificate.pem.crt`)
2.  **Private Key:** (e.g., `xxxxxxxxxx-private.pem.key`)
3.  **Root CA Certificate:** Amazon Root CA 1 (e.g., `AmazonRootCA1.pem`)

**Deployment Instructions for these files:**
*   Create a `certs/` directory in the root of the `smart-cushion-fog` project.
*   Place the 3 files inside the `certs/` directory.
*   Ensure the `.env` file points to these exact file paths:
    *   `AWS_CERT_PATH=certs/your-certificate.pem.crt`
    *   `AWS_KEY_PATH=certs/your-private.pem.key`
    *   `AWS_CA_PATH=certs/AmazonRootCA1.pem`

## 3. Data Flow Checklist

Once configured, the Fog Node will automatically push JSON data to the defined topics. The Cloud team should verify they are receiving:

*   [ ] **Event Records:** `session_started`, `session_ended`, and `alert_triggered`.
*   [ ] **Telemetry Records:** Periodic updates every `CLOUD_SYNC_INTERVAL` seconds (default 60s) while a user is sitting.
*   [ ] **Summary Records:** An aggregated session summary containing the 9-posture duration breakdown, sent immediately after a session ends.

---

## Appendix: AWS IoT Core Setup Guide (For Cloud Team)

Since this project consists of a single demonstration cushion, the Fog Node is pre-configured with fixed IDs. Please follow these exact steps in the AWS Console to ensure compatibility:

1.  **Create a Thing:**
    *   Navigate to **AWS IoT Core -> Manage -> All devices -> Things**.
    *   Click **Create things**.
    *   Name the thing exactly: `smart-cushion-fog-01` (This matches our `AWS_CLIENT_ID`).
2.  **Generate Certificates:**
    *   Select "Auto-generate a new certificate".
    *   Download the **Device Certificate**, **Private Key**, and **Amazon Root CA 1**. Send these 3 files to the Fog Node deployment team.
3.  **Create IoT Policy:**
    *   Create a new policy named `SmartCushionFogPolicy`.
    *   **Action 1 (`iot:Connect`):** Allow connection where the client ID matches `smart-cushion-fog-01`.
    *   **Action 2 (`iot:Publish`):** Allow publishing to the topic `cushion/cushion-01/*` (The fixed `DEVICE_ID` is `cushion-01`).
4.  **Attach Policy:**
    *   Attach the `SmartCushionFogPolicy` to the certificate generated in Step 2.
5.  **Provide Endpoint:**
    *   Go to **AWS IoT Core -> Settings**.
    *   Copy the "Device data endpoint" and send it to the Fog Node deployment team.
