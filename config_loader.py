"""
설정 파일 로더
config.yaml을 읽어 전역에서 사용할 수 있게 해줍니다.
"""
import yaml
import logging
import logging.handlers
from pathlib import Path
from typing import Any

_config: dict = {}


def load_config(path: str = "config.yaml") -> dict:
    """config.yaml을 로드합니다."""
    global _config
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")
    with open(config_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
    return _config


def get_config() -> dict:
    """로드된 설정을 반환합니다. 없으면 자동 로드합니다."""
    if not _config:
        load_config()
    return _config


def get(key_path: str, default: Any = None) -> Any:
    """
    점(.) 구분자로 중첩 키에 접근합니다.
    예: get("trading.risk.daily_loss_limit") → 0.03
    """
    cfg = get_config()
    keys = key_path.split(".")
    val = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val


_config_path: str = "config.yaml"


def set(key_path: str, value: Any) -> None:
    """
    런타임에 설정값을 변경합니다 (메모리만 변경, 파일 저장 안 함).
    파일에도 저장하려면 save_config()를 별도로 호출하세요.

    예: set("trading.risk.daily_loss_limit", 0.05)
    """
    cfg = get_config()
    keys = key_path.split(".")
    target = cfg
    for k in keys[:-1]:
        if k not in target or not isinstance(target[k], dict):
            target[k] = {}
        target = target[k]
    target[keys[-1]] = value


def save_config(path: str = None) -> None:
    """현재 메모리의 설정을 YAML 파일로 저장합니다."""
    global _config_path
    save_path = path or _config_path
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            yaml.dump(_config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logging.getLogger(__name__).info(f"설정 파일 저장 완료: {save_path}")
    except Exception as e:
        logging.getLogger(__name__).error(f"설정 파일 저장 실패: {e}")


def setup_logging() -> None:
    """config.yaml 설정으로 로거를 초기화합니다."""
    cfg = get_config()
    log_cfg = cfg.get("logging", {})

    level = getattr(logging, log_cfg.get("level", "INFO"))
    log_file = log_cfg.get("file", "logs/trading.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 콘솔 핸들러
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    # 파일 핸들러 (로테이팅)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=log_cfg.get("max_bytes", 10_485_760),
        backupCount=log_cfg.get("backup_count", 5),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)
