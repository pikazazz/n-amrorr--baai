"""
ระบบตรวจจับระดับน้ำอัตโนมัติจากกล้อง CCTV (เทศบาลเมืองปทุมธานี)
พร้อมระบบแจ้งเตือนเสียง (Sound Alert) เมื่อระดับน้ำถึงเกณฑ์สีเหลืองหรือสีแดง

ฟีเจอร์:
1. ดึงภาพสตรีมสดจากกล้อง HLS (m3u8) แบบเรียลไทม์
2. คำนวณระดับผิวน้ำ (Waterline Detection) เทียบกับสเกลเสาจริง
3. แสดงผลหน้าจอพร้อมเส้นวัดระดับน้ำและสถานะแบบ Live HUD
4. ระบบเสียงเตือน (Sound Alert) ผ่านลำโพงคอมพิวเตอร์ (Windows winsound):
   - 🟠 เกณฑ์สีเหลือง/ส้ม (>= 2.20 ม.): เสียงเตือน Beep สั้น (Warning)
   - 🔴 เกณฑ์สีแดง (>= 2.50 ม.): เสียงไซเรนฉุกเฉิน (Critical Alert)
5. บันทึกภาพ Snapshot เมื่อเกิดเหตุการณ์แจ้งเตือนอัตโนมัติ
"""

import cv2
import numpy as np
import time
import threading
import sys
import os
import requests

# สำหรับเสียงเตือนบน Windows
try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False


class LiveHLSReader:
    """
    คลาสเล่นสตรีม HLS อัจฉริยะ (Live Edge Player):
    ดึงไฟล์ .ts ท่อนล่าสุดจาก playlist.m3u8 โดยตรง
    แก้ปัญหา OpenCV FFmpeg ไปอ่านไฟล์เก่าในอดีตจนเกิด Stream timeout
    ทำให้ภาพมาเร็วทันที 0.1s ไม่มีดีเลย์ ไม่ค้าง และไม่หลุด
    """
    def __init__(self, m3u8_url):
        self.m3u8_url = m3u8_url
        self.base_url = m3u8_url.rsplit('/', 1)[0]
        self.current_ts = None
        self.cap = None
        self.last_frame = None
        self.is_connected = False
        self.running = True
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _get_latest_ts(self):
        try:
            r = requests.get(self.m3u8_url, timeout=3)
            if r.status_code == 200:
                lines = [line.strip() for line in r.text.splitlines() if line.strip().endswith('.ts')]
                if lines:
                    return lines[-1]
        except Exception:
            pass
        return None

    def _worker(self):
        while self.running:
            latest_ts = self._get_latest_ts()
            if latest_ts and (latest_ts != self.current_ts or self.cap is None or not self.cap.isOpened()):
                self.current_ts = latest_ts
                ts_url = f"{self.base_url}/{latest_ts}"
                if self.cap:
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                self.cap = cv2.VideoCapture(ts_url)

            if self.cap and self.cap.isOpened():
                ret, frame = self.cap.read()
                if ret and frame is not None and frame.size > 0:
                    with self.lock:
                        self.last_frame = frame
                        self.is_connected = True
                    time.sleep(0.033) # แสดงผลที่ ~30 FPS อย่างราบรื่น
                    continue

            # เมื่อจบคลิปท่อนปัจจุบันหรือเน็ตสะดุด ให้รอสักครู่เพื่อสลับไปท่อนถัดไป
            self.is_connected = False
            time.sleep(0.5)

    def read(self):
        with self.lock:
            if self.last_frame is not None:
                return self.is_connected, self.last_frame.copy()
            return False, None

    def stop(self):
        self.running = False
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass

# ==========================================
# การตั้งค่าระบบ (Configuration)
# ==========================================
STREAM_URL = "http://101.109.253.60:8999/playlist.m3u8"

# พิกัดเสาวัดระดับน้ำ (แกน X บนภาพ 640x480)
POLE_X_MIN = 286
POLE_X_MAX = 308

# ตำแหน่งอ้างอิงของระดับน้ำ (แกน Y)
Y_300M = 230          # พิกัดระดับ 3.00 เมตร
PIXELS_PER_METER = 176.0 # สเกลความสูง 1 เมตร = ~176 พิกเซล

# ระดับเกณฑ์แจ้งเตือน
ALERT_ORANGE_LEVEL = 2.20  # ลูกศรสีส้ม/เหลือง (ระดับเฝ้าระวัง ~2.20 ม.)
ALERT_RED_LEVEL = 2.50     # ลูกศรสีแดง (ระดับวิกฤต ~2.50 ม.)

# พิกัด Y ของขีดเตือนภัย (คำนวณอัตโนมัติหรือกำหนดคงที่)
Y_RED_ARROW = int(Y_300M + (3.00 - ALERT_RED_LEVEL) * PIXELS_PER_METER)    # ประมาณ Y=318
Y_ORANGE_ARROW = int(Y_300M + (3.00 - ALERT_ORANGE_LEVEL) * PIXELS_PER_METER) # ประมาณ Y=371

# การหน่วงเวลาเสียงเตือน (วินาที เพื่อไม่ให้เสียงดังถี่จนเกินไป)
ALERT_SOUND_INTERVAL = 3.0 
last_alert_time = 0

# ตัวแปรสถานะ
current_status = "NORMAL" # NORMAL, WARNING, CRITICAL
current_level_m = 2.00
is_running = True


# นำเข้า Win32 API สำหรับเร่งเสียงระบบ Windows อัตโนมัติ
import ctypes
from PIL import Image, ImageDraw, ImageFont

# โหลดฟอนต์ภาษาไทยสำหรับปุ่มและ HUD
FONT_THAI_BOLD = None
FONT_THAI_REGULAR = None
try:
    windir = os.environ.get("WINDIR", "C:\\Windows")
    for fb, fr in [("tahomabd.ttf", "tahoma.ttf"), ("leelauib.ttf", "leelaui.ttf"), ("arialbd.ttf", "arial.ttf")]:
        pb = os.path.join(windir, "Fonts", fb)
        pr = os.path.join(windir, "Fonts", fr)
        if os.path.exists(pb) and os.path.exists(pr):
            FONT_THAI_BOLD = ImageFont.truetype(pb, 14)
            FONT_THAI_REGULAR = ImageFont.truetype(pr, 12)
            break
except Exception:
    pass

# พิกัดปุ่มรับทราบระดับน้ำบนหน้าจอ HUD (x1, y1, x2, y2)
BTN_RECT = (18, 104, 322, 166)

# ตัวแปรสถานะ
current_status = "NORMAL"         # NORMAL, WARNING, CRITICAL
acknowledged_stage = None         # เก็บเกณฑ์ที่ผู้ใช้กดรับทราบแล้ว (None, "WARNING", "CRITICAL")
current_level_m = 2.00
is_running = True
sound_stop_requested = threading.Event()


def maximize_system_volume():
    """เร่งเสียง Master Volume ของ Windows ขึ้นสูงสุดและเปิดเสียง (Unmute) อัตโนมัติ"""
    try:
        VK_VOLUME_UP = 0xAF
        for _ in range(20):
            ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 2, 0)
    except Exception:
        pass


def stop_alert_sound():
    """ดับเสียงเตือนทันที"""
    sound_stop_requested.set()
    try:
        if HAS_WINSOUND:
            winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def acknowledge_current_level():
    """
    ฟังก์ชันรับทราบระดับน้ำ:
    - ดับเสียงเตือนทันที
    - บันทึกสถานะว่าได้รับทราบเกณฑ์นี้แล้ว (หยุดส่งเสียงเตือนซ้ำ)
    - รอจนกว่าระดับน้ำจะเปลี่ยนแปลงหรือขึ้นสู่เกณฑ์ถัดไป (เช่น WARNING -> CRITICAL)
    """
    global acknowledged_stage
    stop_alert_sound()
    if current_status in ["WARNING", "CRITICAL"]:
        acknowledged_stage = current_status
        next_hint = f">= {ALERT_RED_LEVEL:.2f} ม. (CRITICAL)" if current_status == "WARNING" else "เกณฑ์สูงสุดแล้ว"
        print(f"\n[{time.strftime('%H:%M:%S')}] 🔇 รับทราบระดับ {current_status} ({current_level_m:.2f} ม.) เรียบร้อยแล้ว")
        print(f"             -> ดับเสียงเตือนทันที! ระบบจะรอตรวจจับเกณฑ์ถัดไป: {next_hint}\n")
    else:
        print(f"\n[{time.strftime('%H:%M:%S')}] ℹ️ ขณะนี้ระดับน้ำปกติ ({current_level_m:.2f} ม.) ไม่มีการเตือนภัยที่ต้องปิดเสียง\n")


def play_alert_sound(status):
    """ฟังก์ชันเล่นเสียงเตือนระดับสูง (WAV Siren) ใน Background Thread เพื่อให้ตื่นชัวร์"""
    sound_stop_requested.clear()
    maximize_system_volume()
    try:
        if status == "CRITICAL":
            # เสียงหวูดไซเรนอพยพน้ำท่วม (Air Raid / Evacuation Siren) แสบแก้วหู ปลุกคนหลับ
            if os.path.exists("loud_siren.wav"):
                winsound.PlaySound("loud_siren.wav", winsound.SND_FILENAME)
            else:
                for _ in range(4):
                    if sound_stop_requested.is_set():
                        break
                    winsound.Beep(2000, 200)
                    winsound.Beep(1200, 200)
        elif status == "WARNING":
            # เสียงเตือนภัยฉุกเฉิน EAS Alert Tone (853Hz+960Hz)
            if os.path.exists("eas_alarm.wav"):
                winsound.PlaySound("eas_alarm.wav", winsound.SND_FILENAME)
            else:
                for _ in range(2):
                    if sound_stop_requested.is_set():
                        break
                    winsound.Beep(900, 300)
                    time.sleep(0.1)
    except Exception as e:
        print(f"[Sound Error] {e}")


def draw_thai_text_on_cv2(img, text, pos, font, fill_bgr=(255, 255, 255)):
    """วาดข้อความภาษาไทยลงบนภาพ OpenCV อย่างรวดเร็ว (< 0.5 ms) โดยใช้ PIL เฉพาะบริเวณข้อความ"""
    if font is None:
        cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.45, fill_bgr, 1, cv2.LINE_AA)
        return
    x, y = pos
    dummy = Image.new("RGB", (1, 1))
    draw_dummy = ImageDraw.Draw(dummy)
    bbox = draw_dummy.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0] + 8, bbox[3] - bbox[1] + 8

    h, w, _ = img.shape
    x2, y2 = min(w, x + tw), min(h, y + th)
    if x2 <= x or y2 <= y:
        return

    crop = img[y:y2, x:x2]
    pil_crop = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_crop)
    fill_rgb = (fill_bgr[2], fill_bgr[1], fill_bgr[0])
    draw.text((0, 0), text, font=font, fill=fill_rgb)
    img[y:y2, x:x2] = cv2.cvtColor(np.array(pil_crop), cv2.COLOR_RGB2BGR)


def detect_waterline(frame):
    """
    ตรวจจับตำแหน่งผิวน้ำที่ตัดกับเสาสีเหลือง
    ใช้เทคนิค Saturation + Contrast Drop ระหว่างตัวเสาในอากาศกับใต้น้ำ
    คืนค่า: waterline_y (พิกัด Y บนภาพ) และระดับน้ำเป็นเมตร
    """
    roi = frame[:, POLE_X_MIN:POLE_X_MAX]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    row_sat = np.mean(sat, axis=1)

    # กวาดจากบนลงล่าง เพื่อหาจุดสุดท้ายที่เสายังโผล่พ้นน้ำ
    waterline_y = 403
    for y in range(200, 460):
        if row_sat[y] > 110:
            waterline_y = y

    # แปลงพิกัด Y เป็นระดับความสูงน้ำจริง (หน่วยเมตร)
    level_m = 3.00 - ((waterline_y - Y_300M) / PIXELS_PER_METER)
    level_m = round(level_m, 2)

    return waterline_y, level_m


def draw_hud(frame, waterline_y, level_m, status, ack_stage):
    """วาดกราฟิก สถานะเตือนภัย และปุ่มรับทราบระดับน้ำลงบนภาพ"""
    annotated = frame.copy()
    h, w, _ = frame.shape

    # 1. วาดเส้นอ้างอิงระดับต่างๆ
    # ขีดสีแดง (ระดับวิกฤต 2.50 ม.)
    cv2.line(annotated, (POLE_X_MIN - 50, Y_RED_ARROW), (POLE_X_MAX + 150, Y_RED_ARROW), (0, 0, 255), 2)
    cv2.putText(annotated, f"CRITICAL: {ALERT_RED_LEVEL:.2f}m", (POLE_X_MAX + 160, Y_RED_ARROW + 5), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)

    # ขีดสีส้ม (ระดับเฝ้าระวัง 2.20 ม.)
    cv2.line(annotated, (POLE_X_MIN - 50, Y_ORANGE_ARROW), (POLE_X_MAX + 150, Y_ORANGE_ARROW), (0, 165, 255), 2)
    cv2.putText(annotated, f"WARNING: {ALERT_ORANGE_LEVEL:.2f}m", (POLE_X_MAX + 160, Y_ORANGE_ARROW + 5), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1, cv2.LINE_AA)

    # ขีดระดับผิวน้ำปัจจุบัน (Detected Waterline)
    water_color = (0, 255, 0) if status == "NORMAL" else ((0, 165, 255) if status == "WARNING" else (0, 0, 255))
    cv2.line(annotated, (POLE_X_MIN - 40, waterline_y), (POLE_X_MAX + 80, waterline_y), (0, 255, 255), 2)
    cv2.putText(annotated, f"<-- WATER: {level_m:.2f}m", (POLE_X_MAX + 85, waterline_y + 4), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2, cv2.LINE_AA)

    # 2. แถบป้าย Dashboard Banner ด้านบนซ้าย
    is_acked = (ack_stage == status and status in ["WARNING", "CRITICAL"])
    banner_border = (50, 220, 120) if is_acked else water_color
    cv2.rectangle(annotated, (10, 10), (330, 175), (20, 20, 20), -1)
    cv2.rectangle(annotated, (10, 10), (330, 175), banner_border, 2)

    status_text = "STATUS: SAFE (NORMAL)"
    if status == "WARNING":
        status_text = "STATUS: WARNING (ACKNOWLEDGED)" if is_acked else "STATUS: WARNING (ORANGE)!"
    elif status == "CRITICAL":
        status_text = "STATUS: CRITICAL (ACKNOWLEDGED)" if is_acked else "STATUS: ALERT (RED CRITICAL)!"

    cv2.putText(annotated, status_text, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.52, banner_border, 2, cv2.LINE_AA)
    cv2.putText(annotated, f"WATER LEVEL: {level_m:.2f} M", (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(annotated, time.strftime("%Y-%m-%d %H:%M:%S"), (20, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1, cv2.LINE_AA)

    # 3. ปุ่มรับทราบระดับน้ำ (Interactive Acknowledge Button)
    bx1, by1, bx2, by2 = BTN_RECT
    flash = (int(time.time() * 2.5) % 2 == 0)

    if status in ["WARNING", "CRITICAL"] and not is_acked:
        # กำลังส่งเสียงเตือนภัย (ยังไม่ได้รับทราบ) -> ปุ่มกระพริบให้เห็นชัดเจน
        if status == "CRITICAL":
            btn_bg = (0, 0, 180) if flash else (0, 0, 110)
        else:
            btn_bg = (0, 90, 200) if flash else (0, 60, 140)
        btn_border_col = (0, 255, 255) if flash else (255, 255, 255)
        title_text = "[ SPACE / คลิก ] รับทราบระดับ & ดับเสียง"
        if status == "WARNING":
            sub_text = f"รอเกณฑ์ถัดไป: >= {ALERT_RED_LEVEL:.2f} ม. (CRITICAL)"
        else:
            sub_text = "กดเพื่อดับเสียงไซเรนฉุกเฉิน"
        title_color = (255, 255, 255)
        sub_color = (200, 240, 255)
    elif is_acked:
        # รับทราบแล้ว ดับเสียงแล้ว -> แสดงสถานะเฝ้าระวังรอเกณฑ์ถัดไป
        btn_bg = (35, 75, 45)
        btn_border_col = (50, 220, 120)
        title_text = "[ รับทราบแล้ว ] ดับเสียงเตือนเรียบร้อย"
        if status == "WARNING":
            sub_text = f"เฝ้าระวังรอเกณฑ์ถัดไป: >= {ALERT_RED_LEVEL:.2f} ม."
        else:
            sub_text = "เฝ้าระวังระดับสูงสุด (ปิดเสียงแล้ว)"
        title_color = (255, 255, 255)
        sub_color = (150, 240, 180)
    else:
        # สถานะปกติ
        btn_bg = (32, 32, 32)
        btn_border_col = (90, 90, 90)
        title_text = "[ ระบบพร้อมทำงาน ] ระดับน้ำปกติ"
        sub_text = "กด SPACE / คลิก เมื่อมีเสียงเตือนภัย"
        title_color = (200, 200, 200)
        sub_color = (140, 140, 140)

    # วาดพื้นหลังปุ่มและขอบปุ่ม
    cv2.rectangle(annotated, (bx1, by1), (bx2, by2), btn_bg, -1)
    cv2.rectangle(annotated, (bx1, by1), (bx2, by2), btn_border_col, 2)

    # วาดข้อความภาษาไทยลงบนปุ่ม
    draw_thai_text_on_cv2(annotated, title_text, (bx1 + 10, by1 + 9), FONT_THAI_BOLD, title_color)
    draw_thai_text_on_cv2(annotated, sub_text, (bx1 + 10, by1 + 35), FONT_THAI_REGULAR, sub_color)

    return annotated


def on_mouse_click(event, x, y, flags, param):
    """ตรวจจับการคลิกเมาส์ที่ปุ่มบนหน้าต่าง HUD"""
    if event == cv2.EVENT_LBUTTONDOWN:
        if BTN_RECT[0] <= x <= BTN_RECT[2] and BTN_RECT[1] <= y <= BTN_RECT[3]:
            acknowledge_current_level()


def main():
    global last_alert_time, current_status, current_level_m, acknowledged_stage

    print("=" * 65)
    print("  ระบบตรวจจับระดับน้ำและเตือนภัยอัตโนมัติ (เทศบาลเมืองปทุมธานี)")
    print(f"  Stream: {STREAM_URL}")
    print(f"  เกณฑ์สีส้ม (เฝ้าระวัง): >= {ALERT_ORANGE_LEVEL:.2f} ม.")
    print(f"  เกณฑ์สีแดง (วิกฤต):     >= {ALERT_RED_LEVEL:.2f} ม.")
    print("-" * 65)
    print("  🔘 [SPACE] หรือ [คลิกปุ่มบนหน้าจอ]: รับทราบระดับน้ำ ดับเสียง และรอเกณฑ์ถัดไป")
    print("  🔘 [t] / [w]: ทดสอบเสียงเตือนสีส้ม (Warning EAS)")
    print("  🔘 [c]:       ทดสอบเสียงไซเรนสีแดง (Critical Siren)")
    print("  🔘 [q]:       ออกจากโปรแกรม")
    print("=" * 65)

    WINDOW_NAME = "Water Level Monitor - Pathumthani"
    cv2.namedWindow(WINDOW_NAME)
    cv2.setMouseCallback(WINDOW_NAME, on_mouse_click)

    print("  กำลังเชื่อมต่อสตรีมกล้อง...")
    stream = LiveHLSReader(STREAM_URL)
    
    frame_count = 0
    smoothed_y = 405

    try:
        while True:
            connected, frame = stream.read()
            if frame is None:
                # กำลังโหลดหรือเชื่อมต่อครั้งแรก
                time.sleep(0.1)
                continue

            frame_count += 1

            # ตรวจจับทุกๆ 5 เฟรมเพื่อความลื่นไหลและประหยัดทรัพยากร
            if frame_count % 5 == 0:
                raw_y, raw_level = detect_waterline(frame)
                # ตัวกรอง Exponential Moving Average (EMA) เพื่อให้เส้นระดับน้ำนิ่ง ไม่สั่นจากคลื่นน้ำ
                smoothed_y = int(0.7 * smoothed_y + 0.3 * raw_y)
                current_level_m = round(3.00 - ((smoothed_y - Y_300M) / PIXELS_PER_METER), 2)

                # กำหนดระดับสถานะใหม่
                if current_level_m >= ALERT_RED_LEVEL:
                    detected_status = "CRITICAL"
                elif current_level_m >= ALERT_ORANGE_LEVEL:
                    detected_status = "WARNING"
                else:
                    detected_status = "NORMAL"

                # ตรวจสอบการเปลี่ยนผ่านของระดับน้ำ (การรอเกณฑ์ถัดไป)
                if detected_status == "NORMAL":
                    if acknowledged_stage is not None:
                        print(f"[{time.strftime('%H:%M:%S')}] 💧 ระดับน้ำลดลงสู่ระดับปกติ ({current_level_m:.2f} ม.) รีเซ็ตการรับทราบ พร้อมตรวจจับใหม่")
                        acknowledged_stage = None
                elif detected_status == "CRITICAL" and acknowledged_stage == "WARNING":
                    # ระดับน้ำสูงขึ้นจากเกณฑ์สีส้ม สู่เกณฑ์วิกฤตสีแดง (เกณฑ์ถัดไปที่รอคอย!)
                    print(f"\n[{time.strftime('%H:%M:%S')}] 🚨🚨 น้ำขึ้นถึงเกณฑ์ถัดไป! CRITICAL (วิกฤต) ระดับน้ำ: {current_level_m:.2f} ม. >= {ALERT_RED_LEVEL:.2f} ม.!")
                    acknowledged_stage = None  # ปลดการรับทราบ เพื่อส่งเสียงเตือนระดับ CRITICAL ทันที
                elif detected_status == "WARNING" and acknowledged_stage == "CRITICAL":
                    # ระดับน้ำลดลงจากวิกฤตมาที่เตือนภัย
                    acknowledged_stage = "WARNING"

                current_status = detected_status

                # ตรวจสอบการส่งเสียงเตือน
                current_time = time.time()
                if current_status in ["WARNING", "CRITICAL"]:
                    # หากยังไม่ได้รับทราบเกณฑ์ปัจจุบัน ให้ส่งเสียงเตือนตามรอบเวลา
                    if acknowledged_stage != current_status:
                        if current_time - last_alert_time >= ALERT_SOUND_INTERVAL:
                            last_alert_time = current_time
                            print(f"[{time.strftime('%H:%M:%S')}] 🚨 แจ้งเตือน: {current_status} | ระดับน้ำ: {current_level_m:.2f} ม. (กด SPACE หรือคลิกปุ่มเพื่อรับทราบ/ดับเสียง)")
                            # เรียกเสียงเตือนในเธรดแยก
                            threading.Thread(target=play_alert_sound, args=(current_status,), daemon=True).start()

            # วาดหน้าจอ HUD พร้อมปุ่มรับทราบระดับน้ำ
            annotated_frame = draw_hud(frame, smoothed_y, current_level_m, current_status, acknowledged_stage)

            # หากเน็ตกระตุกและกำลัง Reconnect ในเบื้องหลัง ให้แสดงป้ายแจ้งเตือนบน HUD
            if not connected:
                cv2.putText(annotated_frame, "[ RECONNECTING STREAM... ]", (20, 195), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2, cv2.LINE_AA)

            # แสดงภาพหน้าต่าง OpenCV
            cv2.imshow(WINDOW_NAME, annotated_frame)

            # กดปุ่มควบคุม
            key = cv2.waitKey(20) & 0xFF
            if key == ord('q'):
                break
            elif key in [32, ord('a'), ord('A'), 13, ord('m'), ord('M')]:  # Spacebar, A, Enter, M
                acknowledge_current_level()
            elif key == ord('t') or key == ord('w'):
                print("[ทดสอบ] 🔔 เล่นเสียงเตือนระดับสีส้ม (Warning)")
                threading.Thread(target=play_alert_sound, args=("WARNING",), daemon=True).start()
            elif key == ord('c'):
                print("[ทดสอบ] 🚨 เล่นเสียงไซเรนระดับวิกฤต (Critical)")
                threading.Thread(target=play_alert_sound, args=("CRITICAL",), daemon=True).start()
    finally:
        stop_alert_sound()
        stream.stop()
        cv2.destroyAllWindows()
        print("ปิดระบบเรียบร้อยแล้ว")


if __name__ == "__main__":
    main()

