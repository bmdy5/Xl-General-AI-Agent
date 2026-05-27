2026-05-26 13:17 亮哥指出：

【问题】browser_agent调用VisualAgent时卡住，因为默认视觉模型是glm-4v-flash（visual_agent.py第27行）。

【亮哥解释】mimo就是我的眼睛，识别图片用的就是mimo。不能用glm-4v-flash。

【修改】visual_agent.py第27行，DEFAULT_VISION_MODEL的fallback从"openai/glm-4v-flash"改为"openai/mimo-v2.5"。.env中已有VISUAL_AGENT_VISION_MODEL=openai/mimo-v2.5，改默认值是为了防止环境变量未加载时回退到错误的模型。

【教训】视觉模型不能依赖glm-4v-flash，要用mimo-v2.5。mimo是亮哥自建的视觉API服务（api.xiaomimimo.com/v1）。