import os
import re
import base64
import wave
import io
import asyncio
import aiohttp
import logging
import operator

logger = logging.getLogger("voice.tts")

# 1. 情感精调锁定配置表（参考音轨、Few-shot 日文 Prompt 与采样精调参数）
EMOTION_LOCKED_CONFIG = {
    # 撒娇：黄金 01 参考
    "撒娇": {
        "subdir": "coquettish",
        "wav_file": "slice_01.wav",
        "prompt_text": "お兄ちゃん、大好き！",
        "prompt_lang": "ja",
        "params": {
            "temperature": 0.65,
            "top_k": 10,
            "top_p": 0.90,
            "speed_factor": 1.03,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    # 元气：黄金 07 参考 (11:28 终极特调参数)
    "元气": {
        "subdir": "happy",
        "wav_file": "slice_07.wav",
        "prompt_text": "お兄ちゃん、朝だよ！起きて！",
        "prompt_lang": "ja",
        "params": {
            "temperature": 0.70,
            "top_k": 12,
            "top_p": 0.85,
            "speed_factor": 1.05,
            "text_split_method": "cut3",
            "repetition_penalty": 1.35
        }
    },
    # 傲娇：采用 11:28 终极满意的 slice_15 傲娇灵魂特调！
    "傲娇": {
        "subdir": "aggrieved",
        "wav_file": "slice_15.wav",
        "prompt_text": "お兄ちゃんが意地悪するから...",
        "prompt_lang": "ja",
        "params": {
            "temperature": 0.75,
            "top_k": 10,
            "top_p": 0.90,
            "speed_factor": 1.03,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    # 委屈
    "委屈": {
        "subdir": "aggrieved",
        "wav_file": "slice_15.wav",
        "prompt_text": "お兄ちゃんが意地悪するから...",
        "prompt_lang": "ja",
        "params": {
            "temperature": 0.70,
            "top_k": 10,
            "top_p": 0.90,
            "speed_factor": 0.95,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    # 正常/知性
    "正常": {
        "subdir": "normal",
        "wav_file": "slice_10.wav",
        "prompt_text": "お兄ちゃん、何？",
        "prompt_lang": "ja",
        "params": {
            "temperature": 0.70,
            "top_k": 10,
            "top_p": 0.90,
            "speed_factor": 1.00,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    },
    "知性": {
        "subdir": "normal",
        "wav_file": "slice_10.wav",
        "prompt_text": "お兄ちゃん、何？",
        "prompt_lang": "ja",
        "params": {
            "temperature": 0.70,
            "top_k": 10,
            "top_p": 0.90,
            "speed_factor": 1.00,
            "text_split_method": "cut2",
            "repetition_penalty": 1.35
        }
    }
}

def _pad_wav(wav_bytes: bytes, start_silence_sec: float = 0.3, min_duration_sec: float = 1.8) -> bytes:
    """为 WAV 音频首尾填充静音缓冲，确保总时长在 min_duration_sec 以上，防止 QQ 编解码截断无声"""
    try:
        with wave.open(io.BytesIO(wav_bytes), 'rb') as w_in:
            params = w_in.getparams()
            nchannels, sampwidth, framerate, nframes = params[:4]
            data = w_in.readframes(nframes)
            
            frame_bytes_size = operator.mul(nchannels, sampwidth)
            
            # 头部静音
            start_silence_frames = int(operator.mul(framerate, start_silence_sec))
            start_silence_data = bytes(operator.mul(start_silence_frames, frame_bytes_size))
            
            # 原始音频时长
            orig_duration = nframes / framerate
            
            # 尾部静音，确保总时长不低于 min_duration_sec
            current_total_duration = orig_duration + start_silence_sec
            if current_total_duration < min_duration_sec:
                end_silence_sec = min_duration_sec - current_total_duration
            else:
                end_silence_sec = 0.2  # 默认尾部补 0.2 秒以防播放提前截断
                
            end_silence_frames = int(operator.mul(framerate, end_silence_sec))
            end_silence_data = bytes(operator.mul(end_silence_frames, frame_bytes_size))
            
            # 重新打包
            out_buf = io.BytesIO()
            with wave.open(out_buf, 'wb') as w_out:
                w_out.setparams(params)
                w_out.writeframes(start_silence_data + data + end_silence_data)
            return out_buf.getvalue()
    except Exception as e:
        logger.warning(f"Failed to pad WAV: {e}")
        return wav_bytes

async def generate_voice(text: str, style: str = "知性") -> tuple[bytes, str, str]:
    """
    通用平台无关的 GPT-SoVITS 动漫语音合成函数。
    返回一个三元组: (voice_bytes, clean_voice_text, remaining_text)
    其中:
      - voice_bytes: 合成后的经过静音填充的 WAV 音频流字节
      - clean_voice_text: 送去合成的清洗后的纯文本
      - remaining_text: 超过截断字数后留存的剩余文本（用于追加发送）
    """
    if not text.strip():
        return b"", "", ""
        
    voice_text = text.strip()
    remaining_text = ""
    
    # 35字宽限策略：如果总字数不超过 35 字，则完全不截断
    if len(voice_text) > 35:
        # 智能在 20 到 28 字之间标点切分，避免截断吞字
        split_idx = 25
        for i in range(28, 18, -1):
            if i < len(voice_text) and voice_text[i] in ("，", "。", "！", "？", ",", ".", "!", "?", "；", ";"):
                split_idx = i + 1
                break
        voice_text = text[:split_idx].strip()
        remaining_text = text[split_idx:].strip()

    # 2. 文本清洗，过滤特殊的 Markdown 标记，以及旁白动作括号
    clean = voice_text
    clean = re.sub(r'（[^）]+）', '', clean)  # 过滤中文括号
    clean = re.sub(r'\([^)]+\)', '', clean)  # 过滤英文括号
    clean = re.sub(r'\[[^\]]+\]', '', clean)  # 过滤情感标签
    clean = re.sub(re.escape(chr(42)) + "+", "", clean) # 用 chr(42) 代替直接书写星号
    clean = re.sub(r'`+', '', clean)
    clean = re.sub(r'#+', '', clean)
    clean = clean.replace("&", "和").replace("<", " ").replace(">", " ")
    clean = clean.replace("……", "").replace("...", "")
    clean = clean.strip()
    
    if not clean:
        return b"", "", text

    # 获取具体情感的黄金锁定配置
    config = EMOTION_LOCKED_CONFIG.get(style)
    if not config:
        config = EMOTION_LOCKED_CONFIG["正常"]
    
    resources_dir = "/Users/xiaofeng/bot-我的自搭建agent/新的agent/Xl-General-AI-Agent/agent/resources/sagiri_emotions"
    ref_wav_path = os.path.join(resources_dir, config["subdir"], config["wav_file"])
    
    if not os.path.exists(ref_wav_path):
        raise FileNotFoundError(f"Locked reference audio not found: {ref_wav_path}")
        
    logger.info(f"🎙️ [TTS] 开始使用 GPT-SoVITS 合成. 情绪: [{style}] | 文本: '{clean}'")

    api_url = os.getenv("GPT_SOVITS_API_URL", "http://127.0.0.1:9880")
    
    payload = {
        "text": clean,
        "text_lang": "zh",
        "ref_audio_path": ref_wav_path,
        "prompt_text": config["prompt_text"],
        "prompt_lang": config["prompt_lang"],
        "top_k": config["params"]["top_k"],
        "top_p": config["params"]["top_p"],
        "temperature": config["params"]["temperature"],
        "speed_factor": config["params"]["speed_factor"],
        "text_split_method": config["params"].get("text_split_method", "cut2"),
        "repetition_penalty": config["params"]["repetition_penalty"],
        "media_type": "wav"
    }

    timeout = aiohttp.ClientTimeout(total=6.0)
    voice_bytes = b""
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{api_url}/tts", json=payload) as resp:
            if resp.status == 200:
                voice_bytes = await resp.read()
            else:
                err_text = await resp.text()
                logger.warning(f"POST /tts failed with {resp.status}, trying GET fallback... {err_text}")
                async with session.get(f"{api_url}/tts", params=payload) as resp_get:
                    if resp_get.status == 200:
                        voice_bytes = await resp_get.read()
                    else:
                        raise ValueError(f"GPT-SoVITS API both failed (status: {resp_get.status})")

    if len(voice_bytes) > 0:
        voice_bytes = _pad_wav(voice_bytes)
        return voice_bytes, clean, remaining_text
    else:
        raise ValueError("Generated audio byte stream is empty")

async def send_voice_qq(context, msg_type: str, user_id: str, group_id: str, text: str, style: str = "知性", is_test: bool = False):
    """QQ 平台特化的发送适配包装函数。若失败则执行高可用纯文本降级。"""
    try:
        voice_bytes, clean_voice_text, remaining_text = await generate_voice(text, style)
        if len(voice_bytes) > 0:
            b64_data = base64.b64encode(voice_bytes).decode("utf-8")
            cq_record = f"[CQ:record,file=base64://{b64_data}]"
            await context.send_msg(msg_type, user_id, group_id, cq_record, skip_delay=is_test)
            logger.info(f"✅ [TTS] QQ 动漫语音合成并发送成功！大小: {len(voice_bytes)} 字节")
            
            if remaining_text:
                asyncio.create_task(context.send_msg(msg_type, user_id, group_id, remaining_text, skip_delay=is_test))
        else:
            await context.send_msg(msg_type, user_id, group_id, text, skip_delay=is_test)
    except Exception as e:
        logger.error(f"❌ [TTS] QQ 语音发送降级为文本: {e}")
        await context.send_msg(msg_type, user_id, group_id, text, skip_delay=is_test)

def parse_voice_test_command(raw_text: str):
    """解析小萤专属语音测试指令"""
    raw_strip = raw_text.strip()
    if not raw_strip.startswith(("小萤语音测试：", "小萤语音测试:")):
        return None, None
        
    test_cmd = raw_strip[7:].strip()
    test_style = "撒娇"
    test_text = test_cmd
    
    m = re.match(r'^\[([^\]]+)\](.*)', test_cmd, re.DOTALL)
    if m:
        test_style = m.group(1).strip()
        test_text = m.group(2).strip()
    else:
        m_sep = re.match(r'^([^\s：，:,\s]{1,4})(?:\s+|[：，:,\s]+)(.*)', test_cmd, re.DOTALL)
        if m_sep:
            potential_style = m_sep.group(1).strip()
            known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "小脾气", "元气", "温柔", "知性", "正常"}
            if potential_style in known_styles or len(potential_style) <= 2:
                test_style = potential_style
                test_text = m_sep.group(2).strip()
        else:
            known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "小脾气", "元气", "温柔", "知性", "正常"}
            for style in sorted(known_styles, key=len, reverse=True):
                if len(style) >= 2 and test_cmd.startswith(style):
                    test_style = style
                    test_text = test_cmd[len(style):].strip()
                    break
    return test_style, test_text
