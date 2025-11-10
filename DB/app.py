# [0] 임포트 (Imports)
import serial
import sys
import time
import threading
import mysql.connector
from flask import Flask, request, jsonify
import logging
import os
from dotenv import load_dotenv
import json # !! 아두이노의 JSON 응답을 파싱하기 위해 추가 !!

# --- 1. 환경 변수 로드 (Load Environment Variables) ---
load_dotenv() 

# --- 2. Flask 앱 및 기본 설정 (Flask App & Basic Config) ---
app = Flask(__name__)

# --- 3. 시스템 설정값 (System Settings) ---
# .env 파일에서 COM 포트를 읽어옵니다.
SERIAL_PORT = os.getenv('SERIAL_PORT', 'COM3') 
BAUD_RATE = 9600
# 아두이노의 모터 작동 + 딜레이(3초)보다 길게 타임아웃 설정
ARDUINO_TIMEOUT_SEC = 10.0 

# --- 4. .env 파일에서 DB 설정 읽어오기 (Read DB Config from .env)
DB_CONFIG = {
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'host': os.getenv('DB_HOST'),
    'database': os.getenv('DB_NAME')
}

# --- 5. 로깅(Logging) 설정 (Logging Config) ---
logging.basicConfig(
    filename='trash_sorter.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)
app.logger.addHandler(logging.StreamHandler()) 
app.logger.setLevel(logging.INFO)

# (DB_CONFIG 유효성 검사)
missing_configs = [key for key, value in DB_CONFIG.items() if not value]
if missing_configs:
    app.logger.critical(f" [오류] .env 환경 변수 누락: {missing_configs}")
    sys.exit(1)

app.logger.info("DB 설정(.env)을 성공적으로 불러왔습니다.")

# --- 6. ID 변환 맵 (!!핵심 통역 로직!!) ---
# AI(PC 1)가 보낸 DB의 'TypeID'를
# 아두이노가 이해하는 'commandNum'으로 변환하는 규칙입니다.
# (DB 스크린샷과 아두이노 코드를 기반으로 작성)
#
# DB TypeID 1 (일반) -> Arduino Command 0 -> (Arduino가 101번 통 제어)
# DB TypeID 2 (플라스틱) -> Arduino Command 1 -> (Arduino가 102번 통 제어)
# DB TypeID 3 (캔) -> Arduino Command 2 -> (Arduino가 103번 통 제어)
TYPE_ID_TO_ARDUINO_CMD = {
    1: 0,
    2: 1,
    3: 2
}

# --- 7. 백그라운드 작업 함수 (JSON 통역 버전) ---
def background_task_worker(type_id):
    """
    이 함수는 별도의 스레드에서 실행됩니다.
    (1) DB TypeID -> Arduino Command 변환 (예: 2 -> 1)
    (2) 아두이노에 Command 전송 (예: "1")
    (3) 아두이노로부터 JSON 응답 수신 (예: {"bin_id": 102})
    (4) DB에 로그 기록 (예: TypeID=2, BinID=102)
    """
    app.logger.info(f"[BG-Task] 시작: DB TypeID={type_id} 수신")
    
    # 1단계: DB TypeID를 아두이노 Command로 변환
    command_num = TYPE_ID_TO_ARDUINO_CMD.get(type_id)
    
    if command_num is None:
        app.logger.error(
            f"[BG-Task] ABORTED: TypeID={type_id}에 해당하는 아두이노 명령(Command)을 "
            f"TYPE_ID_TO_ARDUINO_CMD 맵에서 찾을 수 없습니다."
        )
        return # 작업 종료

    app.logger.info(f"[BG-Task] ID 변환: DB TypeID={type_id} -> Arduino Command={command_num}")

    # 2단계: 아두이노 제어 및 JSON 응답 수신
    # (이 함수는 3번째 파트에서 정의할 것입니다)
    arduino_response_json = send_to_arduino(command_num)
    
    if arduino_response_json:
        app.logger.info(f"[BG-Task] 아두이노 작동 성공. 응답: {arduino_response_json}")
        
        # 3단계: 응답에서 DB에 기록할 BinID 추출
        # (아두이노가 {"bin_id": 102} 처럼 응답)
        bin_id_from_arduino = arduino_response_json.get('bin_id')
        
        if bin_id_from_arduino is None:
            app.logger.error(f"[BG-Task] ABORTED: 아두이노 JSON 응답에 'bin_id'가 없습니다.")
            return

        # 4단계: DB에 로그 기록 (원본 TypeID와 아두이노가 알려준 BinID)
        # (이 함수는 4번째 파트에서 정의할 것입니다)
        # (예: type_id=2, bin_id_from_arduino=102)
        db_success = insert_log_to_db(type_id, bin_id_from_arduino)
        
        if not db_success:
            app.logger.critical(
                f"[BG-Task] CRITICAL: 모터는 (BinID: {bin_id_from_arduino})로 이동했으나, "
                f"DB 기록 실패! (TypeID: {type_id})"
            )
    else:
        app.logger.error(
            f"[BG-Task] ABORTED: 아두이노 제어 실패 (Command: {command_num}). "
            "DB 기록을 시도하지 않습니다."
        )
    
    app.logger.info(f"[BG-Task] 완료: TypeID={type_id}")

# --- 8. 헬퍼 함수 1: 아두이노 제어 (JSON 응답 처리 수정본) ---
def send_to_arduino(command_num):
    """
    아두이노에 command_num(숫자, 0/1/2)을 보내고
    'JSON' 피드백을 기다린 뒤, 파싱하여 딕셔너리로 반환합니다.
    (수정: "Arduino Ready" 부팅 메시지를 먼저 읽고 버립니다)
    """
    try:
        # 'with' 구문을 사용하여 리소스를 안전하게 관리
        with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=ARDUINO_TIMEOUT_SEC) as ser:
            
            # 1. "Arduino Ready" 부팅 메시지 대기 및 버리기
            # (time.sleep(2) 대신, 라인을 실제로 읽어서 부팅 완료를 확인)
            app.logger.info("Arduino 부팅 대기 중 ('Arduino Ready' 수신 대기)...")
            boot_message = ser.readline().decode('utf-8').strip()
            
            # "Arduino Ready"가 아닌 다른 것이 읽힐 수도 있지만,
            # 중요한 것은 첫 번째 라인을 읽어서 '버퍼'를 비우는 것입니다.
            app.logger.info(f"Arduino 부팅 메시지 수신: {boot_message} (이 메시지는 무시됨)")
            
            app.logger.info("Arduino 부팅 완료 확인. Command 전송 시작.")
            
            # 2. 아두이노의 'loop()'가 읽을 명령 전송
            command = f"{command_num}\n".encode('utf-8')
            ser.write(command)
            app.logger.info(f"Arduino <- {command.decode().strip()} (Command 전송)")
            
            # 3. 아두이노로부터 '진짜' JSON 응답 읽기
            response_line = ser.readline().decode('utf-8').strip()
            
            if not response_line:
                app.logger.error(f"Arduino JSON 응답 없음 (Timeout: {ARDUINO_TIMEOUT_SEC}초)")
                return None

            app.logger.info(f"Arduino -> {response_line} (JSON 응답 수신)")
            
            # 4. 수신한 문자열을 JSON 딕셔너리로 변환 시도
            try:
                response_json = json.loads(response_line)
                return response_json # 예: {"type_id": 1, "bin_id": 102}
            except json.JSONDecodeError:
                app.logger.error(f"Arduino가 유효한 JSON이 아닌 응답: {response_line}")
                return None # JSON 파싱 실패

    except serial.SerialTimeoutException:
        app.logger.error(f"Arduino 응답 시간 초과 ({ARDUINO_TIMEOUT_SEC}초). 포트({SERIAL_PORT}) 확인 필요.")
        return None
    except Exception as e:
        app.logger.error(f"아두이노 시리얼 통신 오류: {e}")
        return None
    
# --- 9. 헬퍼 함수 2: DB 로그 기록 ---
def insert_log_to_db(type_id, bin_id):
    """
    ERD의 SortLog 테이블에 분류 로그를 INSERT합니다.
    (예: RecognizedTypeID=2, TargetBinID=102)
    """
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG) 
        cursor = conn.cursor()
        
        # ERD의 SortLog 스키마에 맞게 INSERT
        # (AI가 보낸 원본 type_id와 아두이노가 알려준 bin_id를 기록)
        query = "INSERT INTO SortLog (RecognizedTypeID, TargetBinID) VALUES (%s, %s)"
        cursor.execute(query, (type_id, bin_id))
        conn.commit() # DB에 변경사항 확정
        return True
        
    except mysql.connector.Error as err:
        app.logger.error(f"DB Insert (SortLog) 오류: {err}")
        if conn:
            conn.rollback() # 오류 발생 시 롤백
        return False
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# --- 10. 메인 API 엔드포인트 (AI PC 1이 호출) ---
@app.route('/process_trash', methods=['POST'])
def process_trash_endpoint():
    """
    AI(PC 1)가 식별한 type_id를 JSON으로 받아
    백그라운드 스레드를 시작시키는 API입니다.
    
    [요청 예시 (Body)]
    {
        "type_id": 2  // 1: 일반, 2: 플라스틱, 3: 캔
    }
    """
    
    # 1. 요청 검증: JSON 형식인지 확인
    if not request.is_json:
        app.logger.warn(f"[Main] API 비정상 요청: JSON 형식이 아님")
        return jsonify({"status": "error", "message": "JSON 형식의 요청이 아닙니다."}), 400
        
    data = request.get_json()
    
    # 2. 요청 검증: DB 스키마에 맞는 'type_id'가 왔는지 확인
    type_id = data.get('type_id') 

    if type_id is None:
        app.logger.warn(f"[Main] API 비정상 요청: 'type_id'가 누락되었습니다. (Data: {data})")
        return jsonify({"status": "error", "message": "type_id가 누락되었습니다."}), 400

    # 3. 요청 검증: 유효한 type_id인지 확인 (1, 2, 3)
    if type_id not in TYPE_ID_TO_ARDUINO_CMD:
        app.logger.warn(f"[Main] API 비정상 요청: 유효하지 않은 type_id입니다. (ID: {type_id})")
        return jsonify({"status": "error", "message": f"유효하지 않은 type_id: {type_id}"}), 400

    app.logger.info(f"[Main] API 요청 수신: DB TypeID={type_id}")

    # 4. 백그라운드 스레드 생성 및 시작
    # (주의: args에 튜플로 전달하기 위해 (type_id,) 와 같이 쉼표를 찍어야 함)
    thread = threading.Thread(
        target=background_task_worker, 
        args=(type_id,) 
    )
    thread.daemon = True # 메인 프로그램 종료 시 스레드도 함께 종료
    thread.start()

    # 5. AI(PC 1)에게 작업이 시작되었음을 즉시 응답 (HTTP 202 Accepted)
    return jsonify({
        "status": "accepted",
        "message": f"작업(Type: {type_id})을 백그라운드에서 시작합니다."
    }), 202

# --- 11. Flask 서버 실행 ---
if __name__ == '__main__':
    # AI PC(PC 1)가 접속할 수 있도록 '0.0.0.0' (모든 IP)로 설정합니다.
    # debug=False로 설정해야 스레드가 두 번 실행되지 않고 안정적입니다.
    app.logger.info("Flask 서버를 0.0.0.0:5002에서 시작합니다.")
    app.run(host='0.0.0.0', port=5002, debug=False)