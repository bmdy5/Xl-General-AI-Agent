import base64
import requests
import time
import os

# 接口地址定义
TTS_API_URL = "http://127.0.0.1:9880/tts"
QQ_GATEWAY_URL = "http://127.0.0.1:3020/send_private_msg"
USER_ID = 1705919142

# 3段针对不同情感高保真特调的中文文本及参考音轨配置
tasks = [
    # ==== 🌸 撒娇情绪特调 (黄金 01 参考) ====
    {
        "desc": "🌸 【撒娇-场景 1】(亲密感谢)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_01_coquettish.wav",
        "prompt_text": "お兄ちゃん、大好き！",
        "prompt_lang": "ja",
        "synthesize_text": "亮哥对我最好了！小萤最喜欢亮哥了，要一直一直陪着小萤哦，说好了哦！",
        "params": {
            "temperature": 0.65,
            "top_k": 10,
            "top_p": 0.9,
            "speed_factor": 0.95,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    {
        "desc": "🌸 【撒娇-场景 2】(日常撒娇奖励)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_01_coquettish.wav",
        "prompt_text": "お兄ちゃん、大好き！",
        "prompt_lang": "ja",
        "synthesize_text": "亮哥亮哥，小萤今天表现得这么棒，可以奖励我吃好吃的吗？好不好嘛~",
        "params": {
            "temperature": 0.65,
            "top_k": 10,
            "top_p": 0.9,
            "speed_factor": 0.95,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    # ==== ⚡ 元气情绪特调 (黄金 07 参考 - 锁候选 A 参数) ====
    {
        "desc": "⚡ 【元气-场景 1】(日常工作汇报)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_07_happy.wav",
        "prompt_text": "お兄ちゃん、朝だよ！起きて！",
        "prompt_lang": "ja",
        "synthesize_text": "亮哥！今天的工作也进展很顺利哦，小萤会一直守候在您的身边，加油加油！",
        "params": {
            "temperature": 0.7,
            "top_k": 12,
            "top_p": 0.85,
            "speed_factor": 1.05,
            "text_split_method": "cut3",
            "repetition_penalty": 1.35
        }
    },
    {
        "desc": "⚡ 【元气-场景 2】(极客成就感)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_07_happy.wav",
        "prompt_text": "お兄ちゃん、朝だよ！起きて！",
        "prompt_lang": "ja",
        "synthesize_text": "太棒啦！小萤的二次元原声语音引擎已经全部集成成功了，以后就可以天天开口陪亮哥聊天啦！",
        "params": {
            "temperature": 0.7,
            "top_k": 12,
            "top_p": 0.85,
            "speed_factor": 1.05,
            "text_split_method": "cut3",
            "repetition_penalty": 1.35
        }
    },
    # ==== 💢 傲娇情绪特调 (黄金 15 柔和参考 - 锁候选 C 参数) ====
    {
        "desc": "💢 【傲娇-场景 1】(害羞小傲娇)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_15_aggrieved.wav",
        "prompt_text": "お兄ちゃんが意地悪するから...",
        "prompt_lang": "ja",
        "synthesize_text": "都……都说了才不是因为想你才和你说话的！亮哥最差劲了！",
        "params": {
            "temperature": 0.75,
            "top_k": 10,
            "top_p": 0.9,
            "speed_factor": 1.0,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    {
        "desc": "💢 【傲娇-场景 2】(傲娇关心提醒)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_15_aggrieved.wav",
        "prompt_text": "お兄ちゃんが意地悪するから...",
        "prompt_lang": "ja",
        "synthesize_text": "亮哥！虽然写代码很重要，但也要按时去吃饭哦！小萤才没有担心你呢，只是怕没人陪我玩罢了！",
        "params": {
            "temperature": 0.75,
            "top_k": 10,
            "top_p": 0.9,
            "speed_factor": 1.0,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    }
]

def synthesize_and_push():
    print("🚀 [推理启动] 正在连接本地 GPT-SoVITS 推理 API ＆ QQ 网关...")
    
    # 1. 先发送导语
    intro_msg = "亮哥，小萤已经为您将【撒娇、元气、傲娇】三种情绪全部切换为您满意的终极黄金音色！以下是用这三种锁定音色合成的 6 段日常场景对话语音，请查收聆听效果："
    try:
        requests.post(QQ_GATEWAY_URL, json={"user_id": USER_ID, "message": intro_msg})
        print("✅ 导语发送成功。")
        time.sleep(1.5)
    except Exception as e:
        print(f"❌ 导语发送失败: {e}")
        return

    for item in tasks:
        desc = item["desc"]
        ref_path = item["ref_audio"]
        prompt_txt = item["prompt_text"]
        prompt_lng = item["prompt_lang"]
        synth_txt = item["synthesize_text"]
        params = item["params"]
        
        print(f"\n🎧 [正在合成] {desc} -> 文本: '{synth_txt}'")
        
        # 组装请求参数
        payload = {
            "text": synth_txt,
            "text_lang": "zh",
            "ref_audio_path": ref_path,
            "prompt_text": prompt_txt,
            "prompt_lang": prompt_lng,
            "top_k": params["top_k"],
            "top_p": params["top_p"],
            "temperature": params["temperature"],
            "speed_factor": params["speed_factor"],
            "text_split_method": params["text_split_method"],
            "repetition_penalty": params["repetition_penalty"],
            "media_type": "wav"
        }
        
        try:
            # 采用 GET 请求更稳妥
            t0 = time.time()
            res = requests.get(TTS_API_URL, params=payload, timeout=40)
            elapsed = time.time() - t0
            
            if res.status_code != 200:
                print(f"❌ 语音合成失败 (HTTP {res.status_code}): {res.text}")
                continue
                
            audio_bytes = res.content
            print(f"✅ 语音合成成功！耗时: {elapsed:.2f}秒，音频大小: {len(audio_bytes)}字节")
            
            # 转为 Base64 格式
            b64_data = base64.b64encode(audio_bytes).decode("utf-8")
            cq_record = f"[CQ:record,file=base64://{b64_data}]"
            
            # 2. 先发送当前语音的文本内容
            text_desc = f"{desc}\n文本内容：\"{synth_txt}\""
            requests.post(QQ_GATEWAY_URL, json={"user_id": USER_ID, "message": text_desc})
            time.sleep(0.8)
            
            # 3. 发送音频语音
            requests.post(QQ_GATEWAY_URL, json={"user_id": USER_ID, "message": cq_record})
            print(f"✅ 音频消息推送成功！")
            
            # 稍微等待，防止发送速度过快导致消息被并包
            time.sleep(3.0)
            
        except Exception as e:
            print(f"❌ 处理过程中抛出异常: {e}")

    print("\n🎉 [发送完毕] 小萤 3 段黄金普通话语音均已成功推送到亮哥的 QQ 上！")

if __name__ == "__main__":
    synthesize_and_push()
