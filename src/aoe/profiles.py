"""业务画像的读写。画像就是本引擎的结构化输入契约(docs/04 §1)。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import BusinessProfile


def load_profile(path: str | Path) -> BusinessProfile:
    """读取并校验画像。校验失败会抛出 pydantic 的 ValidationError,信息可直接定位到字段。"""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"画像文件不存在: {target}")
    payload = json.loads(target.read_text(encoding="utf-8-sig"))
    return BusinessProfile.model_validate(payload)


def dump_profile(profile: BusinessProfile, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(profile.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target
