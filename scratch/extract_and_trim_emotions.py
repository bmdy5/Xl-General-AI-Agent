import os
import shutil
import wave

slices_dir = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/scratch/sagiri_slices"
dest_root = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/resources/sagiri_emotions"

# 亮哥精确挑选的情感映射
emotion_mapping = {
    "coquettish": [1, 2, 3],       # 撒娇
    "happy": [7, 13],              # 元气
    "tsundere": [12, 14, 16, 20],  # 傲娇 (20号只需要前3秒)
    "aggrieved": [3, 4, 5, 15, 17, 18], # 委屈
    "normal": [6, 10]              # 正常
}

print(f"Starting extraction and trimming of golden emotional reference slices...")
print(f"Source: {slices_dir}")
print(f"Destination Root: {dest_root}\n")

# 确保目标目录存在，先清空已有内容，确保干净
if os.path.exists(dest_root):
    shutil.rmtree(dest_root)
os.makedirs(dest_root)

# 精准剪切 slice_20 前 3.0 秒的辅助函数
def trim_slice_20(src_path, dest_path):
    print(f"Trimming slice_20 (first 3.0s)...")
    with wave.open(src_path, 'rb') as w_in:
        params = w_in.getparams()
        nchannels, sampwidth, framerate, nframes = params[:4]
        
        # 3.0秒对应的帧数
        keep_frames = int(3.0 * framerate)
        if nframes < keep_frames:
            keep_frames = nframes
            
        frames_data = w_in.readframes(keep_frames)
        
    with wave.open(dest_path, 'wb') as w_out:
        w_out.setparams(params)
        w_out.writeframes(frames_data)
        
    duration = keep_frames / framerate
    print(f"Successfully trimmed and written to {dest_path} | Duration: {duration:.2f}s")

# 开始归档
for emotion, indices in emotion_mapping.items():
    emotion_dir = os.path.join(dest_root, emotion)
    os.makedirs(emotion_dir, exist_ok=True)
    print(f"\nProcessing emotion: [{emotion}] -> saving to {emotion_dir}")
    
    for idx in indices:
        slice_name = f"slice_{idx:02d}.wav"
        src_path = os.path.join(slices_dir, slice_name)
        
        if not os.path.exists(src_path):
            print(f"Warning: {slice_name} not found! Skipping...")
            continue
            
        dest_path = os.path.join(emotion_dir, slice_name)
        
        if idx == 20:
            # 20 号切片特殊处理：只裁剪前 3.0 秒
            trim_slice_20(src_path, dest_path)
        else:
            # 其余的直接无损复制
            shutil.copy2(src_path, dest_path)
            # 计算时长用于日志播报
            with wave.open(dest_path, 'rb') as w:
                params = w.getparams()
                dur = w.getnframes() / w.getframerate()
            print(f"Copied: {slice_name} | Duration: {dur:.2f}s -> {dest_path}")

print(f"\nAll golden emotional references have been organized successfully!")
print(f"Assets stored at: {dest_root}")
