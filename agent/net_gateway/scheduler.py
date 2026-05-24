import asyncio
import json
import logging
import os
import re
import shutil
import urllib.request
from datetime import datetime
import aiohttp

logger = logging.getLogger("net_gateway.scheduler")

NC_HTTP_URL = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3020")
NC_TOKEN = os.getenv("NAPCAT_TOKEN", "")

class GatewayScheduler:
    """定时任务与假死自愈管理器，负责早晚间播客电台轮询与 GPT-SoVITS 守护自愈。"""
    
    def __init__(self, bot):
        self.bot = bot
        self.admin_id = bot.admin_id
        self._is_generating = False
        self._daemon_task = None
        self._tts_fail_count = 0

    async def start(self):
        """拉起守护后台任务循环"""
        self._daemon_task = asyncio.create_task(self._daemon_loop())

        # 网关启动即刻物理自愈去重
        try:
            from agent.skills.cleanup import run_incremental_cleanup
            asyncio.create_task(run_incremental_cleanup(self.bot))
            logger.info("🚀 [自愈系统] 网关成功启动并连接，已异步拉起物理技能增量去重任务")
        except Exception as e:
            logger.error(f"❌ [自愈系统] 启动期物理技能去重拉起失败: {e}")

    async def stop(self):
        """停止定时调度并优雅取消所有活动中的协程任务"""
        logger.info("正在停止 GatewayScheduler...")
        if self._daemon_task and not self._daemon_task.done():
            self._daemon_task.cancel()
            try:
                await self._daemon_task
            except asyncio.CancelledError:
                pass
            logger.info("GatewayScheduler 守护后台任务已成功停止。")

    async def _daemon_loop(self):
        """后台高可用健康守护轮询进程，负责 NapCat 断线自愈重启、GPT-SoVITS 挂载自愈及定时技术早报播客推送。"""
        logger.info("QQ Gateway Background Daemon Loop started.")
        while True:
            await asyncio.sleep(15)  # 每 15s 轮询检测一次健康度
            
            # 1. 自动对 GPT-SoVITS 语音服务进行保活探测、超时强杀与自愈管理
            try:
                from pathlib import Path
                import time
                import shutil
                
                root_dir = Path(__file__).resolve().parents[2]
                state_file = root_dir / "logs" / ".tts_state"
                
                active = False
                last_time = 0.0
                if state_file.exists():
                    try:
                        state_data = json.loads(state_file.read_text(encoding="utf-8"))
                        active = state_data.get("active", False)
                        last_time = state_data.get("last_time", 0.0)
                    except Exception:
                        pass
                
                if active:
                    now_ts = time.time()
                    if now_ts - last_time > 7200:  # 2小时 = 7200秒
                        logger.info("🎙️ [守护进程] 语音服务已闲置满 2 小时，执行物理强杀释放 3GB 内存，并进行磁盘垃圾清理...")
                        
                        cmd_kill = 'pkill -f "api_v2.py" || true'
                        import os
                        os.system(cmd_kill)
                        
                        tts_dir = root_dir.parent / "GPT-SoVITS"
                        output_dir = tts_dir / "output"
                        if output_dir.exists():
                            try:
                                shutil.rmtree(output_dir)
                                logger.info("🎙️ [守护进程] 物理清理 output/ 临时生成目录成功")
                            except Exception as clean_err:
                                logger.warning(f"🎙️ [守护进程] 物理清理 output/ 失败: {clean_err}")
                        
                        tts_log = tts_dir / "tts.log"
                        if tts_log.exists():
                            try:
                                tts_log.unlink()
                            except Exception:
                                pass
                                
                        state_data = {"active": False, "last_time": 0.0}
                        state_file.write_text(json.dumps(state_data), encoding="utf-8")
                        logger.info("🎙️ [守护进程] 语音服务物理内存与磁盘缓存彻底复归清爽 (IDLE)")
                    else:
                        try:
                            timeout_tts = aiohttp.ClientTimeout(total=6.0)
                            if self.bot._http and not self.bot._http.closed:
                                async with self.bot._http.get("http://127.0.0.1:9880/", timeout=timeout_tts) as resp:
                                    if resp.status not in (200, 404):
                                        raise ValueError(f"Status {resp.status}")
                            else:
                                async with aiohttp.ClientSession(timeout=timeout_tts) as session:
                                    async with session.get("http://127.0.0.1:9880/") as resp:
                                        if resp.status not in (200, 404):
                                            raise ValueError(f"Status {resp.status}")
                            self._tts_fail_count = 0  # 探测成功，重置计数
                        except Exception:
                            self._tts_fail_count += 1
                            if self._tts_fail_count >= 2:
                                logger.warning(f"🎙️ [守护进程] 语音服务连续 {self._tts_fail_count} 次探测失败，判定为假死/挂起，执行自愈重启...")
                                tts_dir = str(root_dir.parent / "GPT-SoVITS")
                                cmd_kill = 'pkill -f "api_v2.py" || true'
                                cmd_start = f'cd {tts_dir} && nohup ./venv/bin/python3 api_v2.py -a 127.0.0.1 -p 9880 > tts.log 2>&1 &'
                                import os
                                os.system(cmd_kill)
                                os.system(cmd_start)
                                self._tts_fail_count = 0  # 重启后重置
                            else:
                                logger.info(f"🎙️ [守护进程] 语音服务探测失败 (第 {self._tts_fail_count} 次)，继续防抖观察中...")
                else:
                    pass
            except Exception as tts_manage_err:
                logger.error(f"🎙️ [守护进程] 语音调度及超时检测异常: {tts_manage_err}")

            # 获取当前时间
            now_dt = datetime.now()
            
            # 定时任务：每日 21:00 自动拉起夜间极客播客选题（仅限管理员私聊）
            if now_dt.hour == 21 and now_dt.minute == 0 and 0 <= now_dt.second < 20:
                p_key = f"private_{self.admin_id}"
                waiting_topic = self.bot.get_waiting_podcast_topic()
                if not waiting_topic.get(p_key, False):
                    logger.info("⏰ Time hit 21:00. Triggering night podcast topic selection...")
                    asyncio.create_task(self._trigger_night_podcast_selection(p_key, self.admin_id))
                    await asyncio.sleep(20)  # 防重入冷却
            
            # 定时任务：每日 04:00 自动触发物理技能增量去重自演进
            if now_dt.hour == 4 and now_dt.minute == 0 and 0 <= now_dt.second < 20:
                logger.info("⏰ Time hit 04:00. Triggering daily skills incremental deduplication...")
                try:
                    from agent.skills.cleanup import run_incremental_cleanup
                    asyncio.create_task(run_incremental_cleanup(self.bot))
                except Exception as scheduler_dedup_err:
                    logger.error(f"❌ [守护进程] 凌晨 4 点拉起物理技能去重失败: {scheduler_dedup_err}")
                await asyncio.sleep(20)  # 防重入冷却

            # 定时任务：每日 06:00 自动拉取云端 NotebookLM 播客并推送
            if now_dt.hour == 6 and now_dt.minute == 0 and 0 <= now_dt.second < 20:
                logger.info("⏰ Time hit 06:00. Triggering morning technical podcast push...")
                asyncio.create_task(self._trigger_morning_podcast_download(self.admin_id))
                await asyncio.sleep(20)  # 防重入冷却

    async def _trigger_night_podcast_selection(self, session_key: str, admin_id: str):
        """夜间播客自动选题器"""
        try:
            from agent.tools.mcp_agent_learning_server import list_agent_topics
            res_topics = await list_agent_topics()
            data = json.loads(res_topics)
            if data.get("status") != "success":
                raise ValueError(f"获取选题失败: {data.get('message')}")
                
            topics = data.get("topics", [])
            self.bot.get_podcast_choices()[session_key] = topics
            self.bot.get_waiting_podcast_topic()[session_key] = True
            
            t_str = "\n".join([f"{t}" for t in topics])
            msg = (
                f"💡 亮哥，我是小萤。今晚我们来为明早的极客播客定个专题吧！\n"
                f"您可以直接选择以下任一主题（回复 1、2 或 3），或者直接回复您想听的任意技术方向：\n\n"
                f"{t_str}\n\n"
                f"请在回复中选择。"
            )
            await self.bot._send("private", admin_id, "", msg)
        except Exception as e:
            logger.error(f"获取选题或推送失败: {e}", exc_info=True)
            await self.bot._send("private", admin_id, "", f"❌ 抱歉亮哥，智能提炼明早播客选题时发生异常: {e}")

    async def _trigger_morning_podcast_download(self, admin_id: str):
        """晨间播客音频自动拉取与文件主动推送"""
        from agent.tools.mcp_agent_learning_server import check_and_push_podcast
        try:
            res = await check_and_push_podcast()
            data = json.loads(res)
            status = data.get("status")
            if status == "success":
                local_path = data.get("local_path")
                topic = data.get("topic")
                if os.path.exists(local_path):
                    from pathlib import Path
                    share_dir = str(Path.home() / "napcat-data-tmp")
                    os.makedirs(share_dir, exist_ok=True)
                    safe_topic = re.sub(r'[\/:*?"<>|]', '_', topic)
                    dest_filename = f"亮哥专属完整播客音频-{safe_topic}.wav"
                    host_dest_path = os.path.join(share_dir, dest_filename)
                    container_dest_path = f"/app/.config/QQ/{dest_filename}"
                    
                    logger.info(f"➡️ 正在拷贝音频到共享目录: {host_dest_path}...")
                    shutil.copy(local_path, host_dest_path)
                    
                    file_payload = {
                        "user_id": int(admin_id),
                        "file": container_dest_path,
                        "name": dest_filename
                    }
                    
                    url = f"{NC_HTTP_URL}/upload_private_file"
                    headers = {"Content-Type": "application/json"}
                    if NC_TOKEN:
                        headers["Authorization"] = f"Bearer {NC_TOKEN}"
                        
                    logger.info(f"📤 正在向亮哥 QQ 主动推送完整版播客文件: {dest_filename}")
                    try:
                        if self.bot._http and not self.bot._http.closed:
                            timeout_upload = aiohttp.ClientTimeout(total=30.0)
                            async with self.bot._http.post(url, json=file_payload, headers=headers, timeout=timeout_upload) as resp:
                                if resp.status != 200:
                                    body = await resp.text()
                                    logger.warning(f"File upload failed ({resp.status}): {body[:100]}")
                        else:
                            req = urllib.request.Request(url, data=json.dumps(file_payload).encode(), headers=headers, method="POST")
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=30))
                    except Exception as upload_err:
                        logger.error(f"Failed to upload file to QQ: {upload_err}")
                    
                    success_msg = f"🎉 亮哥专属每日学习早报播客获取成功！\n今日主题：【{topic}】\n音频已通过 QQ 文件传输发送到您的手机。\n本地保存路径：{local_path}"
                    await self.bot._send("private", admin_id, "", success_msg)
            elif status == "pending":
                logger.info("晨间播客尚在生成中，将由守护进程轮询捕获。")
        except Exception as e:
            logger.error(f"晨间主动下载播客失败: {e}", exc_info=True)

    async def _process_podcast_generation_async(self, session_key: str, topic: str, admin_id: str):
        """夜间播客笔记异步合成与 NotebookLM 自动投喂流程"""
        if self._is_generating:
            await self.bot._send("private", admin_id, "", "🌅 亮哥，夜间双人播客生成正在进行中，请耐心等待，请勿重复触发。")
            return
            
        self._is_generating = True
        try:
            from agent.tools.mcp_agent_learning_server import synthesize_agent_notes, launch_podcast_generation
            res_synth = await synthesize_agent_notes(topic, use_web_search=True)
            synth_data = json.loads(res_synth)
            if synth_data.get("status") != "success":
                raise ValueError(f"笔记合成失败: {synth_data.get('message')}")
                
            note_path = synth_data.get("note_path")
            
            res_launch = await launch_podcast_generation(note_path, topic, debug_mode=False)
            launch_data = json.loads(res_launch)
            if launch_data.get("status") != "success":
                raise ValueError(f"云端投喂失败: {launch_data.get('message')}")
                
            await self.bot._send("private", admin_id, "", f"🌅 云端双人中文技术播客生成已成功拉起！\n我已将 2000 字深度研究笔记保存在了 scratch。\n明早 06:00 我将自动使用本地 Chrome 活跃实例静默捕获并为您推送！")
        except Exception as e:
            logger.error(f"夜间播客交互生成失败: {e}", exc_info=True)
            await self.bot._send("private", admin_id, "", f"❌ 抱歉亮哥，在为您生成播客笔记或投喂 NotebookLM 时发生异常：{e}")
        finally:
            self._is_generating = False

