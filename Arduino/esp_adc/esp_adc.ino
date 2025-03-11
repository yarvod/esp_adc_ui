#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <WiFi.h>
#include <Preferences.h>
#include <SD.h>
#include <SPI.h>

#define WIFI_SSID "esp"
#define WIFI_PASSWORD "12345678"
#define WIFI_CONNECT_TIMEOUT 30000  // 30 секунд
#define SD_CS_PIN 5  // Пин для SD-карты

Adafruit_ADS1115 ads;
WiFiServer wifiServer(80);
Preferences preferences;
File dataFile;
String currentFileName = "";
bool isRecording = false;
bool isSDInitialized = false;

adsGain_t currentGain = GAIN_TWOTHIRDS;

adsGain_t setGain(int gainValue) {
  adsGain_t gain = static_cast<adsGain_t>(gainValue);
  ads.setGain(gain);
  currentGain = gain;
  return gain;
}

adsGain_t getGain() {
  return ads.getGain();
}

float getMultiplier() {
  switch (currentGain) {
    case GAIN_TWOTHIRDS: return 0.1875F;
    case GAIN_ONE: return 0.125F;
    case GAIN_TWO: return 0.0625F;
    case GAIN_FOUR: return 0.03125F;
    case GAIN_EIGHT: return 0.015625F;
    case GAIN_SIXTEEN: return 0.0078125F;
    default: return 0.1875F;
  }
}

float* readADC() {
  int16_t adc0 = ads.readADC_SingleEnded(0);
  int16_t adc1 = ads.readADC_SingleEnded(1);
  int16_t adc2 = ads.readADC_SingleEnded(2);
  float multiplier = getMultiplier();
  float volt_0 = multiplier * adc0;
  float volt_1 = multiplier * adc1;
  float volt_2 = multiplier * adc2;
  static float result[3];
  result[0] = volt_0;
  result[1] = volt_1;
  result[2] = volt_2;
  return result;
}

String readADCPretty() {
  float* adcValues = readADC();
  char buffer[64];
  snprintf(buffer, sizeof(buffer), "ADC0: %.2f mV; ADC1: %.2f mV; ADC2: %.2f mV;", adcValues[0], adcValues[1], adcValues[2]);
  return String(buffer);
}

void configureWifi(const char* wifi, const char* ssid, const char* pwd) {
  preferences.putString("wifi", wifi);
  preferences.putString("ssid", ssid);
  preferences.putString("pwd", pwd);

  Serial.println("WiFi settings saved. Restarting...");
  ESP.restart();
}

String getIp() {
  String wifiType = preferences.getString("wifi", "own");
  if (wifiType == "own") {
    return "192.168.4.1";
  } else {
    return WiFi.localIP().toString();
  }
}

String checkRecordingStatus() {
  if (isRecording) {
    return "Recording to " + currentFileName;
  } else {
    return "Not recording";
  }
}

String processRequest(String command) {
  if (command == "adc") {
    return readADCPretty();
  } else if (command == "ip") {
    return getIp();
  } else if (command.startsWith("wifi=")) {
    int sep1 = command.indexOf(';');
    int sep2 = command.lastIndexOf(';');
    String wifi = command.substring(5, sep1);
    String ssid = command.substring(sep1 + 6, sep2);
    String pwd = command.substring(sep2 + 5);
    configureWifi(wifi.c_str(), ssid.c_str(), pwd.c_str());
  } else if (command.startsWith("setGain=")) {
    int gainValue = command.substring(8).toInt();
    setGain(gainValue);
    return String(gainValue);
  } else if (command.startsWith("gain")) {
    return String(getGain());
  } else if (command.startsWith("start=")) {
    if (isRecording) {
      return "Error: Unable to start new recording due to " + currentFileName;
    }
    currentFileName = command.substring(6);
    isRecording = true;
    return "Recording started in " + currentFileName;
  } else if (command == "stop") {
    isRecording = false;
    if (dataFile) {
      dataFile.close();
    }
    String response = "Recording stopped in " + currentFileName;
    currentFileName = "";
    return response;
  } else if (command.startsWith("delete=")) {
    String fileName = command.substring(7);
    if (SD.exists(fileName)) {
      SD.remove(fileName);
      return "File " + fileName + " deleted";
    } else {
      return "Error: File " + fileName + " not found";
    }
  } else if (command == "files") {
    String fileList = "";
    File root = SD.open("/");
    if (root) {
      File file = root.openNextFile();
      while (file) {
        fileList += String(file.name()) + ";";
        file = root.openNextFile();
      }
      file.close();
    } else {
      return "Error: Failed to open directory";
    }
    return fileList;
  } else if (command == "checkRecording") {
    return checkRecordingStatus();
  } else if (command == "deinitSD") {
    if (isSDInitialized) {
      SD.end();
      isSDInitialized = false;
      return "SD card deinitialized. Safe to remove.";
    } else {
      return "SD card is already deinitialized.";
    }
  } else if (command == "initSD") {
    if (!isSDInitialized) {
      if (SD.begin(SD_CS_PIN)) {
        isSDInitialized = true;
        return "SD card initialized.";
      } else {
        return "Failed to initialize SD card.";
      }
    } else {
      return "SD card is already initialized.";
    }
  }
  return "command not found";
}

void setup() {
  Serial.begin(115200);

  ads.setGain(currentGain);
  ads.setDataRate(RATE_ADS1115_860SPS);
  ads.begin();

  preferences.begin("wifi-settings", false);

  String wifiType = preferences.getString("wifi", "own");
  String ssid = preferences.getString("ssid", WIFI_SSID);
  String password = preferences.getString("pwd", WIFI_PASSWORD);

  if (wifiType == "other") {
    Serial.print("Connecting to existing WiFi SSID ");
    Serial.println(ssid);
    WiFi.begin(ssid.c_str(), password.c_str());
  } else if (wifiType == "own") {
    Serial.print("Creating own WiFi SSID ");
    Serial.println(ssid);
    WiFi.softAP(ssid.c_str(), password.c_str());
    WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1), IPAddress(255, 255, 255, 0));
  }

  unsigned long startAttemptTime = millis();

  while (WiFi.status() != WL_CONNECTED && wifiType == "other") {
    Serial.print(".");
    delay(500);
    if (millis() - startAttemptTime > WIFI_CONNECT_TIMEOUT) {
      Serial.println("\nFailed to connect to WiFi. Continuing without WiFi.");
      break;
    }
  }

  if (WiFi.status() == WL_CONNECTED || wifiType == "own") {
    Serial.print("\nWiFi connected. IP address: ");
    Serial.println(getIp());
    wifiServer.begin();
  }

  if (SD.begin(SD_CS_PIN)) {
    isSDInitialized = true;
    Serial.println("SD card initialized.");
  } else {
    Serial.println("SD card initialization failed!");
  }

  xTaskCreatePinnedToCore(handleSerialCommands, "HandleSerial", 4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(handleWiFiCommands, "HandleWiFi", 4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(dataCollectionTask, "DataCollection", 4096, NULL, 1, NULL, 0);
}

void loop() {
  // Основной цикл остается пустым, так как задачи обрабатываются в отдельных потоках
}

void handleSerialCommands(void * parameter) {
  while (true) {
    if (Serial.available() > 0) {
      String serialCommand = Serial.readStringUntil('\n');
      String response = processRequest(serialCommand);
      Serial.println(response);
    }
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

void handleWiFiCommands(void * parameter) {
  while (true) {
    if (WiFi.status() == WL_CONNECTED || WiFi.getMode() == WIFI_AP) {
      WiFiClient client = wifiServer.available();

      if (client) {
        while (client.connected()) {
          while (client.available() > 0) {
            String request = client.readStringUntil('\n');

            request.replace("\r", "");
            request.replace("\n", "");
            Serial.println("Process request");
            String response = processRequest(request);
            client.println(response);
            Serial.print("Response: ");
            Serial.println(response);
          }
          vTaskDelay(10 / portTICK_PERIOD_MS);
        }

        client.stop();
        Serial.println("Client disconnected");
      }
    }
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

void dataCollectionTask(void * parameter) {
  while (true) {
    if (isRecording && isSDInitialized) {
      dataFile = SD.open(currentFileName, FILE_APPEND);
      if (dataFile) {
        float* adcData = readADC();
        char buffer[64];
        snprintf(buffer, sizeof(buffer), "%d; %.2f; %.2f; %.2f", millis(), adcData[0], adcData[1], adcData[2]);
        dataFile.println(String(buffer));
        dataFile.close();
      } else {
        Serial.println("Error: Failed to open file for writing");
      }
    }
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

void hostFile(WiFiClient client) {
  String request = client.readStringUntil('\r');
  int addr_start = request.indexOf('/') + 1;
  int addr_end = request.indexOf(' ', addr_start);
  String fileName = request.substring(addr_start, addr_end);

  if (SD.exists(fileName)) {
    File file = SD.open(fileName, FILE_READ);
    if (file) {
      Serial.println("File is exists; Send headers");
      client.println("HTTP/1.1 200 OK");
      client.println("Content-Type: application/octet-stream");
      client.println("Content-Disposition: attachment; filename=\"" + fileName + "\"");
      client.println("Connection: close");
      client.println();

      // Чтение и отправка файла по частям
      const size_t bufferSize = 128;
      char buffer[bufferSize];
      size_t bytesRead;

      while ((bytesRead = file.readBytes(buffer, bufferSize)) > 0) {
        Serial.println("Send 128 bytest");
        client.write(buffer, bytesRead);
      }
      Serial.println("All bytest sent; closing file");
      file.close();
      Serial.println("File is closed");
    } else {
      Serial.println("File not found "+ fileName);
      client.println("HTTP/1.1 404 Not Found");
      client.println("Connection: close");
      client.println();
    }
    client.stop();
  }
}
