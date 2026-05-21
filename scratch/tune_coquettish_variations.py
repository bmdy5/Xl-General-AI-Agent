import base64
import requests
import time
import os

# 接口地址定义
TTS_API_URL = "http://127.0.0.1:9880/tts"
QQ_GATEWAY_URL = "http://127.0.0.1:3020/send_private_msg"
USER_ID = 1705919142

# 4款针对撒娇情绪极细微参数特调的候选版本
coquettish_tasks = [
    {
        "desc": "🌸 【撒娇候选-A】(极甜微嗔 · 偏慢更甜)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_01_coquettish.wav",
        "prompt_text": "お兄ちゃん、大好き！",
        "prompt_lang": "ja",
        "synthesize_text": "亮哥，最喜欢你了！要一直陪着小萤哦，好不好嘛~",
        "params": {
            "temperature": 0.60,
            "top_k": 8,
            "top_p": 0.90,
            "speed_factor": 0.92,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    {
        "desc": "🌸 【撒娇候选-B】(欢快黏人 · 语速弹性)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_01_coquettish.wav",
        "prompt_text": "お兄ちゃん、大好き！",
        "prompt_lang": "ja",
        "synthesize_text": "亮哥亮哥，小萤今天表现得这么棒，可以奖励我吃好吃的吗？好不好嘛~",
        "params": {
            "temperature": 0.68,
            "top_k": 12,
            "top_p": 0.85,
            "speed_factor": 0.96,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    {
        "desc": "🌸 【撒娇候选-C】(缠人娇滴 · 感情起伏大)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_01_coquettish.wav",
        "prompt_text": "お兄ちゃん、大好き！",
        "prompt_lang": "ja",
        "synthesize_text": "亮哥~ 别工作了，陪小萤聊天嘛，好不好？求求你了~",
        "params": {
            "temperature": 0.72,
            "top_k": 10,
            "top_p": 0.95,
            "speed_factor": 0.94,
            "text_split_method": "cut2",
            "repetition_penalty": 1.30
        }
    },
    {
        "desc": "🌸 【撒娇候选-D】(软萌委屈 · 情绪拉满)",
        "ref_audio": "/Users/xiaofeng/.gemini/antigravity-ide/brain/af37c692-c73d-450d-a3d0-fb6bbede7f39/slice_01_coquettish.wav",
        "prompt_text": "お兄ちゃん、大好き！",
        "prompt_lang": "ja",
        "synthesize_text": "哼，亮哥要是再不理小萤，小萤就要哭给你看啦！快哄哄我嘛~",
        "params": {
            "temperature": 0.75,
            "top_k": 15,
            "top_p": 0.90,
            "speed_factor": 1.00,
            "text_split_method": "cut2",
            "repetition_penalty": 1.40
        }
    }
]

def synthesize_and_push_variations():
    print("🚀 [推理启动] 正在连接本地 GPT-SoVITS 推理 API ＆ QQ 网关进行撒娇特调...")
    
    # 发送导语
    intro_msg = "亮哥，小萤为您定制了4款细微参数特调的【撒娇-候选版本】！参数与情感细节各不相同，请您点听后告诉我哪一个听起来最完美最自然，我们将用作最终版撒娇情绪的黄金模板："
    try:
        requests.post(QQ_GATEWAY_URL, json={"user_id": USER_ID, "message": intro_msg})
        print("✅ 导语发送成功。")
        time.sleep(1.5)
    except Exception as e:
        print(f"❌ 导语发送失败: {e}")
        return

    for item in coquettish_tasks:
        desc = item["desc"]
        ref_path = item["ref_audio"]
        prompt_txt = item["prompt_text"]
        prompt_lng = item["prompt_lang"]
        synth_txt = item["synthesize_text"]
        params = item["params"]
        
        print(f"\n🎧 [正在合成] {desc} -> 文本: '{synth_txt}'")
        
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
            t0 = time.time()
            res = requests.get(TTS_API_URL, params=payload, timeout=40)
            elapsed = time.time() - t0
            
            if res.status_code != 200:
                print(f"❌ 语音合成失败 (HTTP {res.status_code}): {res.text}")
                continue
                
            audio_bytes = res.content
            print(f"✅ 语音合成成功！耗时: {elapsed:.2f}秒，音频大小: {len(audio_bytes)}字节")
            
            b64_data = base64.b64encode(audio_bytes).decode("utf-8")
            cq_record = f"[CQ:record,file=base64://{b64_data}]"
            
            # 发送当前候选的描述与文本
            text_desc = f"{desc}\n文本内容：\"{synth_txt}\"\n参数配置：Temp={params['temperature']}, TopK={params['top_k']}, Speed={params['speed_factor']}"
            requests.post(QQ_GATEWAY_URL, json={"user_id": USER_ID, "message": text_desc})
            time.sleep(0.8)
            
            # 发送音频
            requests.post(QQ_GATEWAY_URL, json={"user_id": USER_ID, "message": cq_record})
            print(f"✅ 音频消息推送成功！")
            
            # 间隔，避免并包
            time.sleep(3.0)
            
        except Exception as e:
            print(f"❌ 处理过程中抛出异常: {e}")

    print("\n🎉 [发送完毕] 4款精心调校的撒娇特调候选语音已全部推送到亮哥的 QQ 上！")

if __name__ == "__main__":
    synthesize_and_push_variations()
