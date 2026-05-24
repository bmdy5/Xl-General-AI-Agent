import os
import logging
from pathlib import Path

logger = logging.getLogger("agent.config")

class Settings:
    def __init__(self):
        self._data = {}
        self.load()

    def load(self):
        """自适应从 config/settings.yaml 加载配置并注入环境变量"""
        try:
            # 找到项目根目录下的 config/settings.yaml
            root = Path(__file__).resolve().parents[2]
            yaml_path = root / "config" / "settings.yaml"
            
            if not yaml_path.exists():
                yaml_path = root / "config.yaml"
            
            if yaml_path.exists():
                import yaml
                content = yaml_path.read_text(encoding="utf-8")
                parsed = yaml.safe_load(content)
                if isinstance(parsed, dict):
                    self._data = parsed
                    # 自动注入到 os.environ，实现 100 percent 向后兼容
                    for key, val in parsed.items():
                        if val is not None and not isinstance(val, (dict, list)):
                            os.environ[str(key)] = str(val)
                logger.info(f"Successfully loaded config from {yaml_path}")
            else:
                logger.warning("Centralized config file settings.yaml not found, falling back to env.")
        except Exception as e:
            logger.error(f"Failed to load centralized YAML config: {e}")

    def __getattr__(self, name: str):
        if name in self._data:
            return self._data[name]
        return os.environ.get(name, "")

    def get(self, name: str, default=None):
        if name in self._data:
            return self._data[name]
        return os.environ.get(name, default)

# 单例模式暴露
settings = Settings()
