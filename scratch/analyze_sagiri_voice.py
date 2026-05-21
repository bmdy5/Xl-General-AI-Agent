import os
import sys

file_path = "/Users/xiaofeng/Desktop/素材库/音频/和泉纱雾的手机提示音！拿去用！.mp4"

print(f"Checking file: {file_path}")

if not os.path.exists(file_path):
    print("Error: File does not exist!")
    sys.exit(1)

size_bytes = os.path.getsize(file_path)
size_mb = size_bytes / (1024 * 1024)
print(f"File exists. Size: {size_bytes} bytes ({size_mb:.2f} MB)")

# 尝试探测是否有 ffmpeg 存在，以及用 ffprobe 抓取信息
import subprocess

try:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    duration = subprocess.check_output(cmd).decode().strip()
    print(f"Duration (ffprobe): {duration} seconds")
    
    cmd_audio = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name,sample_rate,channels", "-of", "json", file_path]
    audio_info = subprocess.check_output(cmd_audio).decode().strip()
    print("Audio info (JSON):")
    print(audio_info)
except Exception as e:
    print(f"Could not retrieve duration via ffprobe: {e}")
