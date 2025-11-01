#include <Servo.h>
#include <ArduinoJson.h>

// 핀 정의
#define SERVO0_PIN 9
#define SERVO1_PIN 10
#define SERVO2_PIN 11

Servo servo0;   // 입구 제어
Servo servo1;   // 내부 제어
Servo servo2;   // 내부 제어

void setup() {
  Serial.begin(9600);

  servo0.attach(SERVO0_PIN);
  servo1.attach(SERVO1_PIN);
  servo2.attach(SERVO2_PIN);
  
  servo0.write(0);
  servo1.write(90);
  servo2.write(90);
}

void loop() {
  if (Serial.available() > 0) {
    String commandStr = Serial.readStringUntil('\n');
    int commandNum = commandStr.toInt();
    
    int angle = 0;
    bool validCommand = true; 

    switch (commandNum) {
      case 0:
        angle = 30; 
        break;
      case 1:
        angle = 90; 
        break;
      case 2:
        angle = 150; 
        break;
      default:
        validCommand = false; 
        break;
    }

    if (validCommand) {
      servo1.write(angle);
      servo2.write(angle);
      delay(1000); 

      servo0.write(90);

      // JSON 수신 처리
      StaticJsonDocument<100> doc; 
      doc["type_id"] = commandNum; 
      doc["bin_id"] = 102;
      serializeJson(doc, Serial);
      Serial.println(); 

      // 입구 원위치
      delay(3000);
      servo0.write(0);
    }
  }
}