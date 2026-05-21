import base64
import requests
import time

api_url = "http://127.0.0.1:3020/send_private_msg"
user_id = 1705919142

# 我们要发的文件和对应的中文说明
files_to_send = [
    ("🌸 【撒娇情绪】(slice_01_coquettish.wav)", "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_01_coquettish.wav"),
    ("⚡ 【元气情绪】(slice_07_happy.wav)", "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_07_happy.wav"),
    ("💢 【傲娇情绪 (3秒去噪裁剪版 ✂️)】(slice_20_tsundere.wav)", "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_20_tsundere.wav"),
    ("💧 【委屈情绪】(slice_15_aggrieved.wav)", "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_15_aggrieved.wav"),
    ("🍵 【正常情绪】(slice_10_normal.wav)", "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_10_normal.wav")
]

print("Initializing QQ Voice Push for Liange...")

# 先发一条前导说明消息
try:
    requests.post(api_url, json={"user_id": user_id, "message": "亮哥好！这是小萤为您整理并精准裁剪好的 5 类黄金情绪原声，您可以直接在手机 QQ 上点击试听收听效果："})
    time.sleep(1.0)
    
    for desc, path in files_to_send:
        print(f"Sending: {desc} from {path}")
        with open(path, "rb") as f:
            audio_bytes = f.read()
            
        b64_data = base64.b64encode(audio_bytes).decode("utf-8")
        cq_record = f"[CQ:record,file=base64://{b64_data}]"
        
        # 1. 先发送文本描述
        requests.post(api_url, json={"user_id": user_id, "message": desc})
        time.sleep(0.5)
        # 2. 发送音频语音
        requests.post(api_url, json={"user_id": user_id, "message": cq_record})
        time.sleep(1.5)
        
    print("All audio previews pushed successfully to QQ!")
except Exception as e:
    print(f"Error during push: {e}")
