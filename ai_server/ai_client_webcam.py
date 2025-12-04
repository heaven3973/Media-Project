import ultralytics
ultralytics.checks()
from ultralytics import YOLO

import cv2
import requests
import time
import serial
print("[DEBUG] Using serial module from:", serial.__file__)
print("[DEBUG] Serial has attribute Serial?", hasattr(serial, "Serial"))


# -----------------------------
# 1) 설정값
# -----------------------------
MODEL_PATH = '/home/vit/dev_ws/project/Media-Project/ai_server/12_model.pt'
DATA_SERVER_URL = "http://192.168.0.101:5002/process_trash"  # 데이터 서버 주소

CAM_INDEX = 2          # 웹캠 번호
CONF_THRES = 0.25      # 최소 신뢰도

# 아두이노(초음파 센서) 시리얼 설정
SERIAL_PORT = "/dev/ttyUSB0"  # 우분투 기준. 필요하면 /dev/ttyUSB0 등으로 변경
BAUD_RATE = 9600
TRIGGER_DIST_CM = 7.0       # 7cm 이하일 때 트리거
TRIGGER_COOLDOWN_SEC = 2.0  # 초음파 트리거 쿨다운
DETECTION_COOLDOWN_SEC = 10.0  # YOLO 분류 후 5초간 재감지 금지

# YOLO 클래스 인덱스 -> 실제 라벨 이름 (학습 시 사용한 순서)
CLASS_NAMES = {
    0: "종이",
    1: "종이팩",
    2: "종이컵",
    3: "캔류",
    4: "유리병",
    5: "페트",
    6: "플라스틱",
    7: "비닐",
    8: "유리+다중포장재",
    9: "페트+다중포장재",
    10: "스티로폼",
    11: "건전지",
}

# YOLO 클래스 idx -> type_id 매핑
YOLO_TO_TYPE_ID = {
    0: 1, 1: 1, 2: 1,          # 종이류 = 일반
    3: 3,                      # 캔류
    4: 1,                      # 유리병 = 일반
    5: 2, 6: 2, 9: 2,          # 플라스틱류
    7: 1, 8: 1, 10: 1, 11: 1   # 기타 = 일반
}

# -----------------------------
# 2) YOLO 결과에서 클래스 하나 뽑기
# -----------------------------
def get_pred_label(results):
    r = results[0]

    # -------------------------------
    # 1) 분류 모델(Classification)
    # -------------------------------
    if hasattr(r, "probs") and r.probs is not None:
        probs = r.probs.data.clone()  # tensor 복사
        probs[0] = 0.0  # ★ 0번 종이 제거

        idx = int(probs.argmax().item())
        conf = float(probs[idx].item())
        return idx, conf

    # -------------------------------
    # 2) 객체 탐지 모델(Detection)
    # -------------------------------
    if hasattr(r, "boxes") and r.boxes is not None and len(r.boxes) > 0:
        boxes = r.boxes

        # 모든 박스 중 0번 클래스는 제거
        keep_indices = []
        for i, cls in enumerate(boxes.cls):
            if int(cls.item()) != 0:  # ★ cls==0이면 제거
                keep_indices.append(i)

        # 유효 박스가 없으면 리턴
        if len(keep_indices) == 0:
            return None, None

        # 남아있는 박스 중 confidence 가장 높은 것 선택
        confs = boxes.conf[keep_indices]
        best_i = keep_indices[int(confs.argmax().item())]

        idx = int(boxes.cls[best_i].item())
        conf = float(boxes.conf[best_i].item())
        return idx, conf

    return None, None


# -----------------------------
# 3) 데이터 서버 호출
# -----------------------------
def send_to_data_server(type_id: int):
    payload = {"type_id": type_id}
    try:
        print(f"[INFO] 데이터서버로 전송: {payload}")
        resp = requests.post(DATA_SERVER_URL, json=payload, timeout=5)
        print(f"[INFO] 응답 코드: {resp.status_code}, 응답 내용: {resp.text}")
    except Exception as e:
        print(f"[ERROR] 데이터서버 통신 실패: {e}")

# -----------------------------
# 4) 메인 루프
# -----------------------------
def main():
    print("[INFO] YOLO 모델 로딩 중...")
    model = YOLO(MODEL_PATH)
    print("[INFO] 모델 로딩 완료!")

    # 웹캠
    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        print("[ERROR] 웹캠을 열 수 없습니다.")
        return

    # 시리얼
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.05)
        print(f"[INFO] 시리얼 포트 열림: {SERIAL_PORT} @ {BAUD_RATE}")
    except Exception as e:
        print(f"[ERROR] 시리얼 포트를 열 수 없습니다: {e}")
        cap.release()
        return

    last_trigger_time = 0.0
    last_detection_time = 0.0

    print("[INFO] 웹캠 + 초음파 트리거 시작!")
    print(" - 초음파 센서가 7cm 이하를 감지하면 자동으로 캡처 & 분류")
    print(" - YOLO 분류 후 5초 동안 재감지 금지")
    print(" - 'q' 를 누르면 종료")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] 프레임을 읽을 수 없습니다.")
            break

        # ---------- 초음파 센서 입력 ----------
        line = ser.readline().decode(errors="ignore").strip()
        triggered = False
        dist_cm = None

        if line:
            try:
                dist_cm = float(line)
                if 0 < dist_cm < 200 and dist_cm <= TRIGGER_DIST_CM:
                    triggered = True
            except ValueError:
                if line.upper() == "DETECT":
                    triggered = True

        # 화면에 거리 표시
        text = "Ultrasonic: waiting..."
        if dist_cm is not None:
            text = f"Distance: {dist_cm:.1f} cm"

        # ---------- YOLO 박스 없이 기본 화면 표시 ----------
        cv2.putText(frame, text, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, "Press 'q' to quit", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        cv2.imshow("DeepCycle AI Webcam (Ultrasonic Trigger)", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            print("[INFO] 종료합니다.")
            break

        # ---------- 초음파 / YOLO 쿨다운 ----------
        now = time.time()

        # 초음파 연속발사 방지
        if triggered and (now - last_trigger_time) > TRIGGER_COOLDOWN_SEC:
            # YOLO 5초 쿨다운 적용
            if now - last_detection_time < DETECTION_COOLDOWN_SEC:
                continue

            last_trigger_time = now
            print("\n[INFO] ----- 초음파 트리거 감지! 캡처 & 분류 시작 -----")
            if dist_cm is not None:
                print(f"[INFO] 감지 거리: {dist_cm:.1f} cm")

            # ---------- YOLO 추론 ----------
            t0 = time.time()
            results = model.predict(source=frame, conf=CONF_THRES, verbose=False)
            infer_ms = (time.time() - t0) * 1000.0

            # ---------- YOLO Bounding Box + Label 시각화 ----------
            plot_img = results[0].plot()
            cv2.imshow("DeepCycle AI Webcam (Ultrasonic Trigger)", plot_img)
            cv2.waitKey(1)

            idx, conf = get_pred_label(results)
            if idx is None:
                print("[WARN] 객체를 인식하지 못했습니다.")
                continue

            label_kor = CLASS_NAMES.get(idx, f"cls_{idx}")
            type_id = YOLO_TO_TYPE_ID.get(idx, 1)

            print(
                f"[RESULT] YOLO idx={idx} ({label_kor}), "
                f"type_id={type_id}, conf={conf:.3f}, time={infer_ms:.1f}ms"
            )

            # ---------- YOLO 5초 쿨다운 시작 ----------
            last_detection_time = time.time()

            # ---------- 데이터 서버로 전송 ----------
            send_to_data_server(type_id)

    cap.release()
    ser.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
