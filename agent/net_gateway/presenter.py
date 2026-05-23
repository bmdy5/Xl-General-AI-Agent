import re
import time
import logging
from .tts import send_voice

logger = logging.getLogger("net_gateway.presenter")

class StreamPresenter:
    """流式文本展示与语音发音协调器，负责将大模型推理流进行拟真分句、[SPLIT] 切分及动漫情绪语音合成发送。"""
    
    def __init__(self, executor):
        self.executor = executor
        self.context = executor.context
        
        # 移出的渲染状态（针对每一个 session 会话进行局部管理）
        self.sent_transition = False
        self.buf = ""
        self.is_voice_reply = False
        self.voice_style = "知性"
        self.total_sent_tokens = 0

    async def handle_delta(self, content: str, msg_type: str, user_id: str, group_id: str):
        """流式处理 text_delta 内容"""
        if not self.sent_transition:
            self.sent_transition = True
        self.buf += content

        if not self.is_voice_reply:
            # 识别是否是语音发声前缀（比如 [语音:乐] 或者 [乐]）
            style_match = re.match(r'^\[([^\s\]]+)\]', self.buf.strip())
            if style_match:
                candidate_style = style_match.group(1).strip()
                if candidate_style.startswith("语音:") or candidate_style.startswith("语音："):
                    candidate_style = candidate_style.split(":", 1)[-1].split("：", 1)[-1].strip()
                    
                known_styles = {"喜", "怒", "哀", "乐", "撒娇", "傲娇", "委屈", "小脾气", "元气", "温柔", "知性", "正常"}
                if candidate_style in reversed(sorted(known_styles, key=len)):
                    self.is_voice_reply = True
                    self.voice_style = candidate_style
                    self.context._last_voice_time = time.monotonic()
                    logger.info(f"✅ AI自主触发语音合成，情绪: {self.voice_style}")

        if self.is_voice_reply:
            # 语音回复时不进行流式分句发送，全量缓存在 buf 中以保持语音连贯性
            pass
        else:
            # 文本流式分句分段发送，保证极速拟真微交互
            if "[SPLIT]" in self.buf:
                parts = self.buf.split("[SPLIT]")
                for part in parts[:-1]:
                    if part.strip():
                        self.executor._log_activity_dispatcher("AI 计划/答复", part.strip(), user_id=user_id)
                        await self.context.send_chunk(msg_type, user_id, group_id, part.strip())
                        self.total_sent_tokens += self.executor._count_tokens(part.strip())
                self.buf = parts[-1]
            elif "\n\n" in self.buf and len(self.buf) > 40:
                idx = self.buf.rfind("\n\n")
                to_send = self.buf[:idx]
                if to_send.strip():
                    self.executor._log_activity_dispatcher("AI 计划/答复", to_send.strip(), user_id=user_id)
                    await self.context.send_chunk(msg_type, user_id, group_id, to_send.strip())
                    self.total_sent_tokens += self.executor._count_tokens(to_send.strip())
                self.buf = self.buf[idx+2:]
            elif len(self.buf) > 100 and any(p in self.buf for p in ("。", "！", "？")):
                idx = -1
                for p in ("。", "！", "？"):
                    p_idx = self.buf.rfind(p)
                    if p_idx > idx:
                        idx = p_idx
                if idx != -1:
                    to_send = self.buf[:idx+1]
                    if to_send.strip():
                        self.executor._log_activity_dispatcher("AI 计划/答复", to_send.strip(), user_id=user_id)
                        await self.context.send_chunk(msg_type, user_id, group_id, to_send.strip())
                        self.total_sent_tokens += self.executor._count_tokens(to_send.strip())
                    self.buf = self.buf[idx+1:]

    async def flush_buffer(self, msg_type: str, user_id: str, group_id: str):
        """强行冲刷缓冲区，并进行语音合成的最终投递"""
        if self.buf.strip():
            self.executor._log_activity_dispatcher("AI 计划/答复", self.buf.strip(), user_id=user_id)
            if self.is_voice_reply:
                style_match = re.match(r'^\[([^\s\]]+)\](.*)', self.buf.strip(), re.DOTALL)
                pure_text = style_match.group(2).strip() if style_match else self.buf.strip()
                if pure_text:
                    await send_voice(self.context, msg_type, user_id, group_id, pure_text, self.voice_style)
                    self.total_sent_tokens += self.executor._count_tokens(pure_text)
            else:
                await self.context.send_chunk(msg_type, user_id, group_id, self.buf.strip())
                self.total_sent_tokens += self.executor._count_tokens(self.buf.strip())
            self.buf = ""
