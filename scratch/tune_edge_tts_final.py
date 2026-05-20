import asyncio
import sys
import os
import base64
import aiohttp
import edge_tts

# 将项目根目录加入 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

USER_ID = 1705919142
NC_HTTP_URL = "http://127.0.0.1:3020"

TEST_TEXT = "亮哥，我是小萤。我已经为你准备好了这几款不同的音色，你觉得哪一个声音最适合作为我的极客女声人设呢？"

# 精选4款非二次元普通话/国语日常女声的8组对比配置
TEST_VOICES = {
    "方案 E-1 (台湾腔晓臻 - 原版直出)": {
        "voice": "zh-TW-HsiaoChenNeural",
        "pitch": "+0Hz",
        "rate": "+0%",
        "desc": "🎙️ 音轨 1: 台湾腔 · 晓臻 (原版直出)\n特点: 原始台湾日常女声，甜美自然，极具微信语音聊天温度。"
    },
    "方案 E-2 (台湾腔晓臻 - 极客松弛精调)": {
        "voice": "zh-TW-HsiaoChenNeural",
        "pitch": "-5Hz",
        "rate": "-3%",
        "desc": "🎙️ 音轨 2: 台湾腔 · 晓臻 (极客松弛精调)\n特点: 降低音高 5Hz、语速减缓 3%，音色更加沉稳舒缓，极其耐听。"
    },
    "方案 E-3 (台湾腔晓雨 - 原版直出)": {
        "voice": "zh-TW-HsiaoYuNeural",
        "pitch": "+0Hz",
        "rate": "+0%",
        "desc": "🎙️ 音轨 3: 台湾腔 · 晓雨 (原版直出)\n特点: 原始台湾知性女声，温润平静，知性十足。"
    },
    "方案 E-4 (台湾腔晓雨 - 极客松弛精调)": {
        "voice": "zh-TW-HsiaoYuNeural",
        "pitch": "-5Hz",
        "rate": "-3%",
        "desc": "🎙️ 音轨 4: 台湾腔 · 晓雨 (极客松弛精调)\n特点: 降低音高 5Hz、语速减缓 3%，耳边轻语感更强，知性治愈。"
    },
    "方案 E-5 (普通话晓晓 - 原版直出)": {
        "voice": "zh-CN-XiaoxiaoNeural",
        "pitch": "+0Hz",
        "rate": "+0%",
        "desc": "🎙️ 音轨 5: 普通话 · 晓晓 (原版直出)\n特点: 原始标准普通话，温柔日常，清晰度极高。"
    },
    "方案 E-6 (普通话晓晓 - 极客日常精调)": {
        "voice": "zh-CN-XiaoxiaoNeural",
        "pitch": "-10Hz",
        "rate": "-5%",
        "desc": "🎙️ 音轨 6: 普通话 · 晓晓 (极客日常精调)\n特点: 降低音高 10Hz、语速减缓 5%，强力洗去商业客服感，转化为非常真诚温润的聊天声音。"
    },
    "方案 E-7 (普通话晓伊 - 原版直出)": {
        "voice": "zh-CN-XiaoyiNeural",
        "pitch": "+0Hz",
        "rate": "+0%",
        "desc": "🎙️ 音轨 7: 普通话 · 晓伊 (原版直出)\n特点: 原始活泼朝气，稍带点灵动可爱感。"
    },
    "方案 E-8 (普通话晓伊 - 极客朝气精调)": {
        "voice": "zh-CN-XiaoyiNeural",
        "pitch": "-8Hz",
        "rate": "-3%",
        "desc": "🎙️ 音轨 8: 普通话 · 晓伊 (极客朝气精调)\n特点: 降低音高 8Hz、语速减缓 3%，压低高频刺耳感，转为极具亲和力的邻家聊天音。"
    }
}

async def send_msg(session, text):
    url = f"{NC_HTTP_URL}/send_private_msg"
    payload = {"user_id": USER_ID, "message": text}
    headers = {"Content-Type": "application/json"}
    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"发送消息出错: {e}")
        return False

async def generate_and_send(session, name, config_dict):
    print(f"\n⚙️ 正在使用 Edge-TTS 合成 [{name}]...")
    
    scratch_dir = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/scratch"
    os.makedirs(scratch_dir, exist_ok=True)
    temp_mp3_path = os.path.join(scratch_dir, f"temp_{config_dict['voice']}_{config_dict['pitch']}.mp3")
    
    try:
        communicate = edge_tts.Communicate(
            text=TEST_TEXT,
            voice=config_dict['voice'],
            pitch=config_dict['pitch'],
            rate=config_dict['rate']
        )
        await communicate.save(temp_mp3_path)
        
        # 读取 MP3 字节码
        with open(temp_mp3_path, "rb") as f:
            mp3_bytes = f.read()
            
        b64_data = base64.b64encode(mp3_bytes).decode("utf-8")
        cq_record = f"[CQ:record,file=base64://{b64_data}]"
        
        # 先发送描述性说明
        await send_msg(session, config_dict['desc'])
        await asyncio.sleep(0.5)
        
        # 再发送语音 CQ 码
        await send_msg(session, cq_record)
        print(f"✅ {name} 发送成功！")
        
        # 清理临时文件
        if os.path.exists(temp_mp3_path):
            os.remove(temp_mp3_path)
            
    except Exception as e:
        print(f"❌ {name} 合成或发送失败: {e}")
        
    await asyncio.sleep(2.5)  # 冷却防高频与错乱播放

async def main():
    async with aiohttp.ClientSession() as session:
        intro_str = (
            "亮哥，根据您的最新指示，我已经彻底排除了粤语和东北等地方方言，"
            "为您锁定了 4 款非二次元的最美普通话/国语日常女生音色！\n\n"
            "下面我将批量为您投递这 4 款音色在【原版直出】与【极客松弛精调】状态下的 8 组对比音频，"
            "方便您在手机上直接盲听选拔！"
        )
        await send_msg(session, intro_str)
        await asyncio.sleep(1.0)
        
        for name, preset in TEST_VOICES.items():
            await generate_and_send(session, name, preset)
            
        final_str = (
            "🎉 8 组经典日常普通话对比音频已全部投递完成！\n\n"
            "您可以点开认真听一下：\n"
            "- 音轨 1、2（台湾晓臻）极富生活聊天温度，听起来像不像微信里真人在说话？\n"
            "- 音轨 5、6（晓晓精密降调版）洗去客服腔后，是不是极其真诚、知性？\n\n"
            "您看中了哪一款？咱们就锁定它作为小萤的新人设音色！"
        )
        await send_msg(session, final_str)
        print("\n🎉 所有的对比声线已成功发往亮哥的 QQ！")

if __name__ == "__main__":
    asyncio.run(main())
