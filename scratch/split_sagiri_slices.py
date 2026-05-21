import os
import wave
import array

input_wav = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/scratch/sagiri_extracted.wav"
output_dir = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/scratch/sagiri_slices"

print(f"Analyzing WAV for silence splitting: {input_wav}")

if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# 清空已有切片
for f in os.listdir(output_dir):
    if f.endswith(".wav"):
        os.remove(os.path.join(output_dir, f))

# 读取 WAV 物理参数
with wave.open(input_wav, 'rb') as w:
    params = w.getparams()
    nchannels, sampwidth, framerate, nframes = params[:4]
    print(f"Channels: {nchannels}, Sample Width: {sampwidth} bytes, Frame Rate: {framerate}Hz, Frames: {nframes}")
    
    if sampwidth != 2 or nchannels != 1:
        print("Error: Script only supports 16-bit Mono WAV!")
        sys.exit(1)
        
    # 一次性读入所有数据点
    raw_data = w.readframes(nframes)
    samples = array.array('h', raw_data)

# 静音分割参数
# 16000Hz 采样率下，每 1600 个 sample 为 100ms
window_size = int(framerate * 0.1) # 100ms 窗口
threshold = 350 # 振幅绝对值阈值 (16-bit 范围在 0~32767)
min_silence_len = 5 # 连续 500ms 静音才判定为停顿分隔点
min_keep_len = int(framerate * 1.0) # 过滤掉小于 1 秒的超短杂音

slices = []
in_voice = False
start_idx = 0
silence_counter = 0

print("Scanning audio signal...")

# 步长为 100ms 窗口
for idx in range(0, len(samples), window_size):
    window = samples[idx: idx + window_size]
    if not window:
        break
        
    # 计算绝对值平均能量
    energy = sum(abs(x) for x in window) / len(window)
    
    if energy > threshold:
        # 处于声音区
        if not in_voice:
            in_voice = True
            start_idx = idx
        silence_counter = 0
    else:
        # 处于静音区
        if in_voice:
            silence_counter += 1
            if silence_counter >= min_silence_len:
                # 判定本句结束
                end_idx = idx - (min_silence_len - 1) * window_size
                in_voice = False
                silence_counter = 0
                if (end_idx - start_idx) >= min_keep_len:
                    slices.append((start_idx, end_idx))

# 处理最后一段
if in_voice and (len(samples) - start_idx) >= min_keep_len:
    slices.append((start_idx, len(samples)))

print(f"Scan complete. Found {len(slices)} sentence candidates.")

# 输出切片
for i, (start, end) in enumerate(slices):
    slice_samples = samples[start:end]
    duration_s = len(slice_samples) / framerate
    
    slice_name = f"slice_{i+1:02d}.wav"
    slice_path = os.path.join(output_dir, slice_name)
    
    with wave.open(slice_path, 'wb') as out_w:
        out_w.setparams(params)
        out_w.writeframes(slice_samples.tobytes())
        
    print(f"Generated: {slice_name} | Duration: {duration_s:.2f}s | Start: {start/framerate:.2f}s | End: {end/framerate:.2f}s")

print(f"All slices exported to: {output_dir}")
