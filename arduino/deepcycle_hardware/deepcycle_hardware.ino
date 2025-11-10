/*
 * 스마트 쓰레기통 - 아두이노 보드 제어 코드 (최종본)
 * ---------------------------------------------------
 * - 로직: (servo1, servo2) 동시 각도 제어 -> servo0 입구 개폐
 * - 응답: "OK" 대신 JSON으로 (type_id, bin_id) 전송
 * - (수정) "Json과 ID 작성" 요청에 따라 bin_id 동적 매핑
 */

// 1. 라이브러리 (가장 위에 선언)
#include <Servo.h>
#include <ArduinoJson.h>

// 2. 핀 정의 및 전역 변수
#define SERVO0_PIN 9  // 입구
#define SERVO1_PIN 10 // 내부 1
#define SERVO2_PIN 11 // 내부 2

Servo servo0;
Servo servo1;
Servo servo2;

// 3. setup() 함수
void setup() {
  Serial.begin(9600); // 파이썬과 통신 속도 맞춤

  // 서보 모터 핀 연결
  servo0.attach(SERVO0_PIN);
  servo1.attach(SERVO1_PIN);
  servo2.attach(SERVO2_PIN);
  
  // 모터 초기 상태 설정
  servo0.write(0);  // 입구 닫힘
  servo1.write(90); // 내부는 90도 (이전 코드 기준)
  servo2.write(90); // 내부는 90도 (이전 코드 기준)
  
  Serial.println("Arduino Ready");
}

// 4. loop() 함수 (메인 로직)
void loop() {
  
  if (Serial.available() > 0) {
    // 1. 파이썬으로부터 명령 수신 (예: "0", "1", "2")
    String commandStr = Serial.readStringUntil('\n');
    int commandNum = commandStr.toInt();
    
    int angle = 0;
    bool validCommand = true; 

    // 2. 명령(commandNum)에 따라 각도(angle) 결정
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
        Serial.println("Error: Invalid CommandNum");
        break;
    }

    // 3. 유효한 명령일 경우 모터 작동
    if (validCommand) {
      
      // 3-1. 내부 모터(1, 2) 각도 조절
      servo1.write(angle);
      servo2.write(angle);
      delay(1000); // 각도 조절 대기

      // 3-2. 입구 모터(0) 열기
      servo0.write(90); // 입구 열림

      // 3-3. JSON 응답 생성 (!! "Json과 ID 작성" 요청 반영 !!)
      // (commandNum 0, 1, 2를 DB BinID 101, 102, 103에 매핑)
      int bin_id_to_report = 0;
      switch(commandNum) {
        case 0: bin_id_to_report = 101; break; // 0번 -> 101번 통
        case 1: bin_id_to_report = 102; break; // 1번 -> 102번 통
        case 2: bin_id_to_report = 103; break; // 2번 -> 103번 통
        default: bin_id_to_report = 0; break; // 예외 처리
      }

      StaticJsonDocument<100> doc; 
      doc["type_id"] = commandNum; // 0, 1, 또는 2
      doc["bin_id"] = bin_id_to_report; // 101, 102, 또는 103
      
      // 3-4. 파이썬으로 JSON 전송
      serializeJson(doc, Serial);
      Serial.println(); 

      // 3-5. 입구 원위치 (닫기)
      delay(3000); // 내용물 투하 대기
      servo0.write(0); // 입구 닫힘
    }
  }
}