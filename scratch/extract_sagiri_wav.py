import os
import subprocess
import sys

mp4_path = "/Users/xiaofeng/Desktop/素材库/音频/和泉纱雾的手机提示音！拿去用！.mp4"
output_wav = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/scratch/sagiri_extracted.wav"

print(f"Extracting audio from: {mp4_path}")
print(f"Target format: 16000Hz, Single Channel, 16-bit PCM WAV")

cmd = [
    "ffmpeg", "-y",
    "-i", mp4_path,
    "-vn",
    "-acodec", "pcm_s16le",
    "-ar", "16000",
    "-ac", "1",
    output_wav
]

try:
    print(f"Running ffmpeg command: {' '.join(cmd)}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    print("Extraction successful!")
    
    # 检查输出文件大小
    if os.path.exists(output_wav):
        wav_size = os.path.getsize(output_wav)
        print(f"Extracted WAV file size: {wav_size} bytes ({wav_size / 1024 / 1024:.2f} MB)")
    else:
        print("Error: WAV file was not created!")
except subprocess.CalledProcessError as e:
    print(f"Error during extraction: {e}")
    print("Stderr output:")
    print(e.stderr.decode())
except Exception as e:
    print(f"Unexpected error: {e}")
