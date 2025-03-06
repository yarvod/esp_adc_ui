#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <WiFi.h>
#include <Preferences.h>

#define WIFI_SSID "esp"
#define WIFI_PASSWORD "12345678"
#define WIFI_CONNECT_TIMEOUT 30000  // 30 секунд

const float multiplier = 0.1875F;
Adafruit_ADS1115 ads;

WiFiServer wifiServer(80);
Preferences preferences;

// Функция для обновления чтения adc
String readADC() {
  int16_t adc0_1 = ads.readADC_Differential_0_1();
  int16_t adc2_3 = ads.readADC_Differential_2_3();
  float volt_01 = multiplier * adc0_1;
  float volt_23 = multiplier * adc2_3;
  String values = "ADC01: " + String(volt_01) + " mV;";
  values += "ADC23: " + String(volt_23) + " mV;";

  return values;
}

void configureWifi(String command) {
  int sep1 = command.indexOf(';');
  int sep2 = command.lastIndexOf(';');
  String wifi = command.substring(5, sep1);
  String ssid = command.substring(sep1 + 6, sep2);
  String pwd = command.substring(sep2 + 5);

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
  } else return WiFi.localIP().toString();
}

String processRequest(String command) {
  if (command == "adc") {
    return readADC();
  } else if (command == "ip") {
    return getIp();
  } else if (command.startsWith("wifi")) {
    configureWifi(command);
  }
  return "command not found";
}

void setup() {
  // Инициализация последовательного соединения для отладки
  Serial.begin(9600);

  ads.setGain(GAIN_TWOTHIRDS);
  ads.begin();

  // Инициализация Preferences
  preferences.begin("wifi-settings", false);

  // Загрузка настроек Wi-Fi из Preferences
  String wifiType = preferences.getString("wifi", "own");
  String ssid = preferences.getString("ssid", WIFI_SSID);
  String password = preferences.getString("pwd", WIFI_PASSWORD);

  // Подключение к Wi-Fi
  if (wifiType == "other") {
    Serial.print("Connecting to existing WiFi SSID ");
    Serial.println(ssid);
    WiFi.begin(ssid.c_str(), password.c_str());
  } else if (wifiType == "own") {
    Serial.print("Creating own WiFi SSID ");
    Serial.println(ssid);
    WiFi.softAP(ssid.c_str(), password.c_str());
    // Назначение статического IP-адреса для точки доступа
    WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1), IPAddress(255, 255, 255, 0));
  }

  unsigned long startAttemptTime = millis();

  while (WiFi.status() != WL_CONNECTED && wifiType == "other") {
    Serial.print(".");
    delay(500);

    // Проверка тайм-аута
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

  // Создание задач для обработки команд с Serial и WiFi
  xTaskCreatePinnedToCore(
      handleSerialCommands,   // Функция задачи
      "HandleSerial",         // Имя задачи
      4096,                  // Размер стека
      NULL,                   // Параметры задачи
      1,                     // Приоритет задачи
      NULL,                  // Дескриптор задачи
      0);                    // Ядро (0 или 1)

  xTaskCreatePinnedToCore(
      handleWiFiCommands,     // Функция задачи
      "HandleWiFi",           // Имя задачи
      4096,                  // Размер стека
      NULL,                   // Параметры задачи
      1,                     // Приоритет задачи
      NULL,                  // Дескриптор задачи
      1);                    // Ядро (0 или 1)
}

void loop() {
  // Основной цикл остается пустым, так как задачи обрабатываются в отдельных потоках
}

void handleSerialCommands(void * parameter) {
  while (true) {
    if (Serial.available() > 0) {
      String serialCommand = Serial.readStringUntil('\n');
      String response = processRequest(serialCommand);
      Serial.print("Response: ");
      Serial.println(response);
    }
    delay(10);
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
            String response = processRequest(request);
            client.println(response);
            Serial.print("Response: ");
            Serial.println(response);
          }
          delay(10);
        }

        client.stop();
        Serial.println("Client disconnected");
      }
    }
    delay(10);
  }
}
