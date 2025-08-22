#include <WiFi.h>
#include <Preferences.h>
#include <SD.h>
#include <SPI.h>

#define WIFI_SSID "esp"
#define WIFI_PASSWORD "12345678"
#define WIFI_CONNECT_TIMEOUT 30000
#define SD_CS_PIN 5
#define SD_BUFFER_SIZE 860
#define RT_BUFFER_SIZE 256
#define CHUNK_SIZE 1024

// --- Встроенный АЦП ESP32 (ADC1) ---
#define ADC_PIN0 32          // ADC1_CH4
#define ADC_PIN1 33          // ADC1_CH5
#define ADC_PIN2 34          // ADC1_CH6 (input-only)
#define ADC_WIDTH_BITS 12
#define ADC_ATTEN      ADC_11db   // ~0..3.3–3.6 В

// --- Параметры фильтрации/выдачи ---
#define OUTPUT_HZ      100        // целевая частота выдачи
#define OVERSAMPLE     16         // сколько раз читаем каждый пин на кадр
#define EMA_ALPHA      0.25f      // коэффициент экспоненциального фильтра (0..1)

WiFiServer wifiServer(80);
Preferences preferences;
File dataFile;
String currentFileName = "";
bool isRecording = false;
bool isSDInitialized = false;

// ----- Формат точки данных -----
struct DataPoint {
  unsigned long timestamp;
  float adc0;
  float adc1;
  float adc2;
};

// ----- Рантайм-буфер -----
DataPoint rtBuffer[RT_BUFFER_SIZE];
volatile int rtHead = 0;
volatile bool rtHasData = false;
volatile bool samplingEnabled = false;
portMUX_TYPE rtMux = portMUX_INITIALIZER_UNLOCKED;

// ----- Буфер для SD -----
DataPoint sdBuffer[SD_BUFFER_SIZE];
int sdBufferIndex = 0;

// ---- Предзаявления ----
void flushBufferToSD();
void hostFile(WiFiClient client, String fileName);
String processRequest(String command);

// ======================= АЦП хелперы ========================
static inline int mv_oversampled(int pin, int n = OVERSAMPLE) {
  long s = 0;
  for (int i = 0; i < n; ++i) s += analogReadMilliVolts(pin);
  return (int)(s / n);
}

void readADC(float* result) {
  // Пересъём + EMA для сглаживания
  static bool ema_init = false;
  static float e0 = 0, e1 = 0, e2 = 0;

  float x0 = (float)mv_oversampled(ADC_PIN0);
  float x1 = (float)mv_oversampled(ADC_PIN1);
  float x2 = (float)mv_oversampled(ADC_PIN2);

  if (!ema_init) { e0 = x0; e1 = x1; e2 = x2; ema_init = true; }
  else {
    e0 += EMA_ALPHA * (x0 - e0);
    e1 += EMA_ALPHA * (x1 - e1);
    e2 += EMA_ALPHA * (x2 - e2);
  }

  result[0] = e0;
  result[1] = e1;
  result[2] = e2;
}

// ======================= Рантайм-буфер =======================
inline void startSampling() { samplingEnabled = true; }
inline void stopSampling()  { samplingEnabled = false; }

inline void rtPushSample(const DataPoint& dp) {
  portENTER_CRITICAL(&rtMux);
  rtBuffer[rtHead] = dp;
  rtHead = (rtHead + 1) % RT_BUFFER_SIZE;
  rtHasData = true;
  portEXIT_CRITICAL(&rtMux);
}

bool rtGetLatest(DataPoint& out) {
  if (!rtHasData) return false;
  portENTER_CRITICAL(&rtMux);
  int last = (rtHead - 1 + RT_BUFFER_SIZE) % RT_BUFFER_SIZE;
  out = rtBuffer[last];
  portEXIT_CRITICAL(&rtMux);
  return true;
}

String readADCPretty() {
  if (!samplingEnabled) startSampling();

  DataPoint dp;
  if (!rtGetLatest(dp)) {
    float v[3];
    readADC(v);
    DataPoint now{ millis(), v[0], v[1], v[2] };
    rtPushSample(now);
    dp = now;
  }

  char out[64];
  snprintf(out, sizeof(out),
           "ADC0: %.1f mV; ADC1: %.1f mV; ADC2: %.1f mV;",
           dp.adc0, dp.adc1, dp.adc2);
  return String(out);
}

// ======================= WiFi / Настройки ====================
void configureWifi(const char* wifi, const char* ssid, const char* pwd) {
  preferences.putString("wifi", wifi);
  preferences.putString("ssid", ssid);
  preferences.putString("pwd", pwd);
  Serial.println("WiFi settings saved. Restarting...");
  ESP.restart();
}

String getIp() {
  String wifiType = preferences.getString("wifi", "own");
  if (wifiType == "own") return "192.168.4.1";
  return WiFi.localIP().toString();
}

// ======================= SD запись ===========================
void flushBufferToSD() {
  if (sdBufferIndex > 0 && isSDInitialized) {
    dataFile = SD.open(currentFileName, FILE_APPEND);
    if (dataFile) {
      for (int i = 0; i < sdBufferIndex; i++) {
        char line[64];
        snprintf(line, sizeof(line), "%lu; %.1f; %.1f; %.1f",
                 sdBuffer[i].timestamp, sdBuffer[i].adc0, sdBuffer[i].adc1, sdBuffer[i].adc2);
        dataFile.println(line);
      }
      dataFile.close();
      sdBufferIndex = 0;
    } else {
      isRecording = false;
      Serial.println("Error: Failed to open file for writing");
    }
  }
}

// ======================= Команды =============================
String checkRecordingStatus() {
  return isRecording ? ("Recording to " + currentFileName) : "Not recording";
}

String processRequest(String command) {
  if (command == "adc") {
    return readADCPretty();

  } else if (command == "ip") {
    return getIp();

  } else if (command.startsWith("wifi=")) {
    if (isRecording) return "Error: Unable setup wifi during recording!";
    int sep1 = command.indexOf(';');
    int sep2 = command.lastIndexOf(';');
    String wifi = command.substring(5, sep1);
    String ssid = command.substring(sep1 + 6, sep2);
    String pwd  = command.substring(sep2 + 5);
    configureWifi(wifi.c_str(), ssid.c_str(), pwd.c_str());

  } else if (command.startsWith("start=")) {
    if (isRecording) return "Error: Unable to start new recording due to " + currentFileName;
    currentFileName = command.substring(6);
    isRecording = true;
    startSampling();
    return "Recording started in " + currentFileName;

  } else if (command == "stop") {
    isRecording = false;
    flushBufferToSD();
    String response = "Recording stopped in " + currentFileName;
    currentFileName = "";
    return response;

  } else if (command.startsWith("delete=")) {
    String fileName = command.substring(7);
    if (isRecording && currentFileName == fileName) return "Error: Unable delete current recording file!";
    if (SD.exists(fileName)) { SD.remove(fileName); return "File " + fileName + " deleted"; }
    else return "Error: File " + fileName + " not found";

  } else if (command == "files") {
    String fileList = "";
    File root = SD.open("/");
    if (root) {
      File file = root.openNextFile();
      while (file) { fileList += String(file.name()) + ";"; file = root.openNextFile(); }
      file.close();
    } else return "Error: Failed to open directory";
    return fileList;

  } else if (command == "checkRecording") {
    return checkRecordingStatus();

  } else if (command == "deinitSD") {
    if (isSDInitialized) {
      if (isRecording) { isRecording = false; flushBufferToSD(); }
      SD.end(); isSDInitialized = false;
      return "SD card deinitialized. Safe to remove.";
    } else return "SD card is already deinitialized.";

  } else if (command == "initSD") {
    if (!isSDInitialized) {
      if (SD.begin(SD_CS_PIN)) { isSDInitialized = true; return "SD card initialized."; }
      else return "Failed to initialize SD card.";
    } else return "SD card is already initialized.";
  }

  return "command not found";
}

// ======================= setup/loop ==========================
void setup() {
  Serial.begin(115200);

  // --- ESP32 ADC1 init ---
  analogReadResolution(ADC_WIDTH_BITS);
  pinMode(ADC_PIN0, INPUT);
  pinMode(ADC_PIN1, INPUT);
  pinMode(ADC_PIN2, INPUT);
  analogSetPinAttenuation(ADC_PIN0, ADC_ATTEN);
  analogSetPinAttenuation(ADC_PIN1, ADC_ATTEN);
  analogSetPinAttenuation(ADC_PIN2, ADC_ATTEN);

  // --- WiFi ---
  preferences.begin("wifi-settings", false);
  String wifiType = preferences.getString("wifi", "own");
  String ssid = preferences.getString("ssid", WIFI_SSID);
  String password = preferences.getString("pwd", WIFI_PASSWORD);

  if (wifiType == "other") {
    Serial.print("Connecting to existing WiFi SSID "); Serial.println(ssid);
    WiFi.begin(ssid.c_str(), password.c_str());
  } else {
    Serial.print("Creating own WiFi SSID "); Serial.println(ssid);
    WiFi.softAP(ssid.c_str(), password.c_str());
    WiFi.softAPConfig(IPAddress(192,168,4,1), IPAddress(192,168,4,1), IPAddress(255,255,255,0));
  }

  unsigned long startAttemptTime = millis();
  while (WiFi.status() != WL_CONNECTED && wifiType == "other") {
    Serial.print("."); delay(500);
    if (millis() - startAttemptTime > WIFI_CONNECT_TIMEOUT) {
      Serial.println("\nFailed to connect to WiFi. Continuing without WiFi.");
      break;
    }
  }
  if (WiFi.status() == WL_CONNECTED || wifiType == "own") {
    Serial.print("\nWiFi connected. IP address: "); Serial.println(getIp());
    wifiServer.begin();
  }

  if (SD.begin(SD_CS_PIN)) { isSDInitialized = true; Serial.println("SD card initialized."); }
  else { Serial.println("SD card initialization failed!"); }

  // Запускаем задачи
  xTaskCreatePinnedToCore(handleSerialCommands, "HandleSerial", 4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(handleWiFiCommands,   "HandleWiFi",   4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(dataCollectionTask,   "DataCollection",4096, NULL, 1, NULL, 0);
}

void loop() {
  // задачи сами всё делают
}

// ======================= Задачи ==============================
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
            request.replace("\r", ""); request.replace("\n", "");
            if (request.startsWith("hostFile=")) {
              String fileName = request.substring(9);
              hostFile(client, fileName);
            } else {
              String response = processRequest(request);
              client.println(response);
            }
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
  const TickType_t period = pdMS_TO_TICKS(1000 / OUTPUT_HZ); // 10 мс при 100 Гц
  TickType_t lastWake = xTaskGetTickCount();

  // сразу включим сбор
  samplingEnabled = true;

  while (true) {
    if (samplingEnabled) {
      float v[3];
      readADC(v); // oversample + EMA
      DataPoint dp{ millis(), v[0], v[1], v[2] };

      // 1) в рантайм-буфер
      rtPushSample(dp);

      // 2) при записи — в SD-буфер
      if (isRecording && isSDInitialized) {
        sdBuffer[sdBufferIndex] = dp;
        if (++sdBufferIndex >= SD_BUFFER_SIZE) flushBufferToSD();
      }
    }
    vTaskDelayUntil(&lastWake, period); // держим ровно 100 Гц
  }
}

// ======================= Отдача файла ========================
void hostFile(WiFiClient client, String fileName) {
  if (SD.exists(fileName)) {
    File file = SD.open(fileName);
    if (file) {
      uint8_t txbuf[CHUNK_SIZE];
      size_t bytesRead;
      while ((bytesRead = file.read(txbuf, CHUNK_SIZE)) > 0) {
        Serial.println("Sent chunk");
        client.write(txbuf, bytesRead);
        vTaskDelay(pdMS_TO_TICKS(1));
      }
      file.close();
      client.stop();
      Serial.println("File sent successfully");
    } else client.println("Error: Failed to open file");
  } else client.println("Error: File not found");
}
