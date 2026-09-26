"""
เครื่องมือสร้างและทดสอบเสียงไซเรนฉุกเฉิน (Emergency Alarm Sound Generator & Tester)
ออกแบบสำหรับกรณีเตือนภัยน้ำท่วมโดยเฉพาะ เสียงแสบหูดังกระหึ่ม ปลุกคนหลับได้ 100%

1. eas_alarm.wav: เสียงสัญญาณ EAS (Emergency Alert System) 853Hz + 960Hz (เสียงเตือนภัยพิบัติระดับโลก)
2. loud_siren.wav: เสียงหวูดไซเรนอพยพ (Evacuation / Air Raid Siren) หอนลากยาว
"""

import os
import wave
import numpy as np
import winsound
import ctypes
import time

def maximize_system_volume():
    """เพิ่มระดับเสียงของ Windows ขึ้นอัตโนมัติ และปลด Mute ทันที"""
    VK_VOLUME_UP = 0xAF
    for _ in range(25):  # กดปุ่มเพิ่มเสียง 25 ครั้ง
        ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_VOLUME_UP, 0, 2, 0)

def generate_eas_sound(filename="eas_alarm.wav", duration=4.0):
    """สร้างเสียง EAS (Emergency Alert System) ความถี่คู่ 853Hz + 960Hz กระตุกถี่"""
    sample_rate = 44100
    num_samples = int(sample_rate * duration)
    t = np.linspace(0, duration, num_samples, endpoint=False)

    tone1 = np.sin(2 * np.pi * 853 * t)
    tone2 = np.sin(2 * np.pi * 960 * t)
    eas = (tone1 + tone2) / 2.0

    # Pulse 8 ครั้งต่อวินาทีเพื่อให้สะดุ้งตื่น
    pulse = (np.sin(2 * np.pi * 8 * t) > 0).astype(float)
    eas_pulsed = eas * 0.6 + eas * pulse * 0.4
    eas_loud = np.clip(eas_pulsed * 2.0, -0.98, 0.98) # ปรับความแรงเต็มพิกัด
    audio = (eas_loud * 32767).astype(np.int16)

    with wave.open(filename, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(audio.tobytes())

def generate_siren_sound(filename="loud_siren.wav", duration=5.0):
    """สร้างเสียงหวูดไซเรนอพยพหนีภัยน้ำท่วม (Air Raid / Evacuation Siren) กวาดคลื่น 600-1400Hz"""
    sample_rate = 44100
    num_samples = int(sample_rate * duration)
    t = np.linspace(0, duration, num_samples, endpoint=False)

    # ความถี่แกว่งขึ้นลงเร็วและรุนแรง
    freq = 900 + 500 * np.sin(2 * np.pi * 1.8 * t)
    phase = np.cumsum(2 * np.pi * freq / sample_rate)

    signal = 0.55 * np.sin(phase) + 0.3 * np.sin(2 * phase) + 0.15 * np.sin(3 * phase)
    signal = np.clip(signal * 2.2, -0.98, 0.98) # Distortion เร่งความดังแสบแก้วหู
    audio = (signal * 32767).astype(np.int16)

    with wave.open(filename, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(audio.tobytes())

def play_sound_file(filename, loop=False):
    maximize_system_volume()
    flags = winsound.SND_FILENAME
    if loop:
        flags |= winsound.SND_ASYNC | winsound.SND_LOOP
    winsound.PlaySound(filename, flags)

if __name__ == "__main__":
    print("=" * 60)
    print("      กำลังสังเคราะห์ไฟล์เสียงเตือนภัยระดับสูงสุด...")
    print("=" * 60)
    
    generate_eas_sound("eas_alarm.wav")
    generate_siren_sound("loud_siren.wav")
    print("✅ สังเคราะห์ไฟล์เสียงสำเร็จ: eas_alarm.wav, loud_siren.wav")

    print("\n🔊 [1/2] กำลังทดสอบ: เสียงสัญญาณ EAS Alert (พายุ/ภัยพิบัติ)...")
    play_sound_file("eas_alarm.wav")
    
    time.sleep(1)
    
    print("\n🚨 [2/2] กำลังทดสอบ: เสียงหวูดไซเรนอพยพน้ำท่วม (Evacuation Siren)...")
    play_sound_file("loud_siren.wav")
    
    print("\n✅ ทดสอบเสียงเสร็จสมบูรณ์!")
