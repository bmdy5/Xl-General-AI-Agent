import os
import subprocess
import sys

mp4_path = "/Users/xiaofeng/Desktop/素材库/音频/和泉纱雾的手机提示音！拿去用！.mp4"
output_wav = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/scratch/sagiri_extracted.wav"

print("Cropping audio: Removing the first 10 seconds...")
print(f"Extraction range: 10.0s to the end")

# -ss 10 放置在 -i 之前可以极速跳过前面的帧并精准定位
cmd = [
    "ffmpeg", "-y",
    "-ss", "10",
    "-i", mp4_path,
    "-vn",
    "-acodec", "pcm_s16le",
    "-ar", "16000",
    "-ac", "1",
    output_wav
]

try:
    print(f"Running ffmpeg crop: {' '.join(cmd)}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    print("Crop and extraction successful!")
    
    # 检查输出文件并使用 ffprobe 获取精准的新时长
    if os.path.exists(output_wav):
        wav_size = os.path.getsize(output_wav)
        
        # 获取精确的新时长
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", output_wav]
        duration = subprocess.check_output(probe_cmd).decode().strip()
        
        print(f"New Cropped WAV file size: {wav_size} bytes ({wav_size / 1024 / 1024:.2f} MB)")
        print(f"New Precise Duration: {duration} seconds (Successfully removed the first 10s)")
    else:
        print("Error: WAV file was not created!")
except subprocess.CalledProcessError as e:
    print(f"Error during crop: {e}")
    print("Stderr output:")
    print(e.stderr.decode())
except Exception as e:
    print(f"Unexpected error: {e}")
