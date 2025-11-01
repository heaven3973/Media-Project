import serial
import sys
import time
import threading
import mysql.connector
from flask import Flask, request, jsonify
import logging
import os  # 1. os 라이브러리 임포트
from dotenv import load_dotenv  # 2. dotenv 라이브러리 임포트

# --- 1. 환경 변수 로드 ---
load_dotenv() 

# --- 2. Flask 앱 및 기본 설정 ---
app = Flask(__name__)

# --- 3. 시스템 설정값 ---
SERIAL_PORT = os.getenv('SERIAL_PORT', 'COM3') 
BAUD_RATE = 9600
ARDUINO_TIMEOUT_SEC = 5.0

# 4. DB 설정을 하드코딩 대신 .env 파일에서 읽어오기
DB_CONFIG = {
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'host': os.getenv('DB_HOST'),
    'database': os.getenv('DB_NAME')
}

# --- 5. 로깅(Logging) 설정 ---
logging.basicConfig(
    filename='trash_sorter.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)
app.logger.addHandler(logging.StreamHandler()) # 콘솔에도 로그 출력
app.logger.setLevel(logging.INFO)

# (DB_CONFIG 유효성 검사 - .env 파일에 값이 비어있는지 확인)
missing_configs = [key for key, value in DB_CONFIG.items() if not value]
if missing_configs:
    print("="*50)
    print(f" [오류] .env 파일에 다음 환경 변수가 설정되지 않았습니다: {missing_configs}")
    print(" .env 파일을 확인하고 서버를 다시 시작하세요.")
    print("="*50)
    sys.exit(1) # 프로그램 강제 종료

app.logger.info("DB 설정(.env)을 성공적으로 불러왔습니다.")

# --- 6. 백그라운드 작업 함수 ---
def background_task_worker(type_id, bin_id):
    """
    이 함수는 별도의 스레드에서 실행됩니다.
    AI(PC 1)는 이 작업이 끝나기를 기다리지 않습니다.
    """
    app.logger.info(f"[BG-Task] 시작: Type={type_id}, Bin={bin_id}")
    
    # 1단계: 아두이노 제어 및 피드백 수신
    arduino_success = send_to_arduino(bin_id)
    
    if arduino_success:
        app.logger.info(f"[BG-Task] 아두이노 작동 성공. DB 기록 시작.")
        
        # 2단계: DB에 로그 기록
        db_success = insert_log_to_db(type_id, bin_id)
        
        if not db_success:
            app.logger.critical(
                f"[BG-Task] CRITICAL: 모터는 (BinID: {bin_id})로 이동했으나, "
                f"DB 기록 실패! (TypeID: {type_id})"
            )
    else:
        app.logger.error(
            f"[BG-Task] ABORTED: 아두이노 제어 실패 (BinID: {bin_id}). "
            "DB 기록을 시도하지 않습니다."
        )
    
    app.logger.info(f"[BG-Task] 완료: Type={type_id}, Bin={bin_id}")

# --- 7. 헬퍼 함수 1: 아두이노 제어 ---
def send_to_arduino(bin_id):
    """아두이노에 명령을 보내고 'OK' 피드백을 기다립니다."""
    try:
        with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=ARDUINO_TIMEOUT_SEC) as ser:
            time.sleep(2) 
            
            command = f"{bin_id}\n".encode('utf-8')
            ser.write(command)
            app.logger.info(f"Arduino <- {command.decode().strip()}")
            
            response = ser.readline().decode('utf-8').strip()
            app.logger.info(f"Arduino -> {response}")
            
            if response == "OK":
                return True
            else:
                app.logger.error(f"Arduino가 'OK'가 아닌 응답: {response}")
                return False

    except serial.SerialTimeoutException:
        app.logger.error(f"Arduino 응답 시간 초과 ({ARDUINO_TIMEOUT_SEC}초)")
        return False
    except Exception as e:
        app.logger.error(f"아두이노 시리얼 통신 오류: {e}")
        return False

# --- 8. 헬퍼 함수 2: DB 기록 ---
def insert_log_to_db(type_id, bin_id):
    """DB에 쓰레기 분류 로그를 INSERT합니다."""
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG) 
        cursor = conn.cursor()
        
        query = "INSERT INTO SortLog (RecognizedTypeID, TargetBinID) VALUES (%s, %s)"
        cursor.execute(query, (type_id, bin_id))
        conn.commit()
        return True
        
    except mysql.connector.Error as err:
        app.logger.error(f"DB Insert 오류: {err}")
        if conn:
            conn.rollback() 
        return False
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

# --- 9. 메인 API 엔드포인트 ---
@app.route('/process_trash', methods=['POST'])
def process_trash_endpoint():
    """AI(PC 1)가 호출하는 API입니다."""
    
    # 1. 요청 검증
    if not request.is_json:
        return jsonify({"status": "error", "message": "JSON 형식의 요청이 아닙니다."}), 400
        
    data = request.get_json()
    type_id = data.get('type_id')
    bin_id = data.get('bin_id')

    if type_id is None or bin_id is None:
        return jsonify({"status": "error", "message": "type_id 또는 bin_id가 누락되었습니다."}), 400

    app.logger.info(f"[Main] API 요청 수신: Type={type_id}, Bin={bin_id}")

    # 2. 백그라운드 스레드 생성 및 시작
    thread = threading.Thread(
        target=background_task_worker, 
        args=(type_id, bin_id)
    )
    thread.daemon = True
    thread.start()

    # 3. AI(PC 1)에게 즉시 응답 반환
    return jsonify({
        "status": "accepted",
        "message": f"작업(Type: {type_id})을 백그라운드에서 시작합니다."
    }), 202

# --- 10. Flask 서버 실행 ---
if __name__ == '__main__':
    # AI PC(PC 1)가 접속할 수 있도록 '0.0.0.0'으로 설정합니다.
    # 포트는 5002번을 그대로 사용합니다.
    app.run(host='0.0.0.0', port=5002, debug=False)