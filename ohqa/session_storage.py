"""Session persistence for ``ohqa``."""
# ohqa 会话持久化模块 - 负责保存和加载会话状态

from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

# ========== OpenHarness 导入 ==========
from openharness.api.usage import UsageSnapshot  # Token 使用量快照
from openharness.engine.messages import ConversationMessage  # 对话消息类型
from openharness.services.session_backend import SessionBackend  # 会话后端接口

# ========== ohqa 导入 ==========
from ohqa.workspace import get_sessions_dir  # 获取会话目录路径


# ========== 路径辅助函数组 ==========

def get_session_dir(workspace: str | Path | None = None) -> Path:
    """获取 ohqa 会话目录路径（~/.ohqa/sessions/）"""
    session_dir = get_sessions_dir(workspace)
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _session_key_token(session_key: str) -> str:
    """将 session_key 哈希为 12 字符 token（用于文件名）

    例如：session_key = "telegram:123456:789"
          token = "a1b2c3d4e5f6"
    """
    return hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]


def _session_key_latest_path(workspace: str | Path | None, session_key: str) -> Path:
    """获取 session_key 对应的 latest 文件路径"""
    session_dir = get_session_dir(workspace)
    token = _session_key_token(session_key)
    return session_dir / f"latest-{token}.json"


# ========== 核心函数：保存会话快照 ⭐⭐⭐ ==========
def save_session_snapshot(
    *,
    cwd: str | Path,  # 当前工作目录（项目路径）
    workspace: str | Path | None = None,  # ohqa 工作区路径
    model: str,  # 使用的模型名称
    system_prompt: str,  # 完整的系统提示词
    messages: list[ConversationMessage],  # 对话历史
    usage: UsageSnapshot,  # Token 使用量统计
    session_id: str | None = None,  # 会话 ID（可选，自动生成）
    session_key: str | None = None,  # 会话路由键（格式：channel:chat_id:thread_id）
) -> Path:
    """持久化 ohqa 会话快照到磁盘

    此函数会写入 3 个文件：
    1. ~/.ohqa/sessions/latest.json - 全局最新会话
    2. ~/.ohqa/sessions/latest-{token}.json - 按 session_key 的最新会话（如果提供）
    3. ~/.ohqa/sessions/session-{id}.json - 具体会话快照
    """
    # ========== 步骤1：获取会话目录和生成会话 ID ==========
    session_dir = get_session_dir(workspace)
    sid = session_id or uuid4().hex[:12]  # 使用提供的 ID 或生成新的 12 字符 ID
    now = time.time()  # 当前时间戳

    # ========== 步骤2：提取会话摘要（首条用户消息的前 80 字符） ==========
    summary = ""
    for msg in messages:
        if msg.role == "user" and msg.text.strip():
            summary = msg.text.strip()[:80]
            break

    # ========== 步骤3：构建会话快照数据结构 ==========
    payload = {
        "app": "ohqa",  # 应用标识
        "session_id": sid,  # 会话唯一 ID
        "session_key": session_key,  # 会话路由键（用于多平台隔离）
        "cwd": str(Path(cwd).resolve()),  # 当前工作目录（项目路径）
        "model": model,  # 使用的模型
        "system_prompt": system_prompt,  # 完整系统提示词（包含 ohqa 人格）
        "messages": [message.model_dump(mode="json") for message in messages],  # 对话历史
        "usage": usage.model_dump(),  # Token 使用量统计
        "created_at": now,  # 创建时间戳
        "summary": summary,  # 会话摘要
        "message_count": len(messages),  # 消息总数
    }

    # ========== 步骤4：序列化为 JSON ==========
    data = json.dumps(payload, indent=2) + "\n"

    # ========== 步骤5：写入全局最新会话文件 ==========
    latest_path = session_dir / "latest.json"
    latest_path.write_text(data, encoding="utf-8")

    # ========== 步骤6：写入 session_key 对应的最新会话（多平台隔离） ==========
    if session_key:
        _session_key_latest_path(workspace, session_key).write_text(data, encoding="utf-8")

    # ========== 步骤7：写入具体会话快照文件 ==========
    session_path = session_dir / f"session-{sid}.json"
    session_path.write_text(data, encoding="utf-8")

    # ========== 步骤8：返回全局最新会话路径 ==========
    return latest_path


# ========== 会话加载函数组 ==========

def load_latest(workspace: str | Path | None = None) -> dict[str, Any] | None:
    """加载全局最新会话快照（~/.ohqa/sessions/latest.json）"""
    path = get_session_dir(workspace) / "latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_latest_for_session_key(workspace: str | Path | None, session_key: str) -> dict[str, Any] | None:
    """按 session_key 加载最新会话（多平台隔离）

    例如：session_key = "telegram:123456:789"
          返回 ~/.ohqa/sessions/latest-a1b2c3d4e5f6.json
    """
    path = _session_key_latest_path(workspace, session_key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def list_snapshots(workspace: str | Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """列出所有会话快照（按创建时间倒序）

    返回会话元数据列表，每个元素包含：
    - session_id: 会话 ID
    - summary: 会话摘要（首条用户消息）
    - message_count: 消息数量
    - model: 使用的模型
    - created_at: 创建时间戳
    """
    session_dir = get_session_dir(workspace)
    sessions: list[dict[str, Any]] = []

    # 按修改时间倒序遍历所有 session-*.json 文件
    for path in sorted(session_dir.glob("session-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # 跳过损坏的文件

        sessions.append(
            {
                "session_id": data.get("session_id", path.stem.replace("session-", "")),
                "summary": data.get("summary", ""),
                "message_count": data.get("message_count", len(data.get("messages", []))),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", path.stat().st_mtime),
            }
        )

        if len(sessions) >= limit:
            break

    return sessions


def load_by_id(workspace: str | Path | None, session_id: str) -> dict[str, Any] | None:
    """按 session_id 或 "latest" 加载会话

    支持两种方式：
    1. session_id = "abc123def456" -> 加载 session-abc123def456.json
    2. session_id = "latest" -> 加载 latest.json
    """
    # 尝试加载具体会话文件
    path = get_session_dir(workspace) / f"session-{session_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    # 尝试加载全局最新会话
    latest = load_latest(workspace)
    if latest and (latest.get("session_id") == session_id or session_id == "latest"):
        return latest

    return None


# ========== 导出功能 ==========

def export_session_markdown(
    *,
    cwd: str | Path,
    workspace: str | Path | None = None,
    messages: list[ConversationMessage],
) -> Path:
    """将会话导出为 Markdown 文件

    生成格式：
    # ohqa Session Transcript

    ## User
    用户消息内容

    ## Assistant
    AI 回复内容
    """
    path = get_session_dir(workspace) / "transcript.md"
    parts = ["# ohqa Session Transcript"]

    for message in messages:
        parts.append(f"\n## {message.role.capitalize()}\n")
        text = message.text.strip()
        if text:
            parts.append(text)

    path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
    return path


# ========== SessionBackend 接口实现 ==========

class OhqaSessionBackend(SessionBackend):
    """ohqa 会话后端 - 实现 OpenHarness 的 SessionBackend 接口

    此类将所有函数调用委托给上面的模块级函数，
    提供统一的会话管理接口供 runtime 和 gateway 使用。
    """

    def __init__(self, workspace: str | Path | None = None) -> None:
        """初始化会话后端

        Args:
            workspace: ohqa 工作区路径（默认 ~/.ohqa）
        """
        self._workspace = workspace

    def get_session_dir(self, cwd: str | Path) -> Path:
        """获取会话目录路径"""
        return get_session_dir(self._workspace)

    def save_snapshot(
        self,
        *,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        messages: list[ConversationMessage],
        usage: UsageSnapshot,
        session_id: str | None = None,
        session_key: str | None = None,
    ) -> Path:
        """保存会话快照（委托给 save_session_snapshot）"""
        return save_session_snapshot(
            cwd=cwd,
            workspace=self._workspace,
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            usage=usage,
            session_id=session_id,
            session_key=session_key,
        )

    def load_latest(self, cwd: str | Path) -> dict[str, Any] | None:
        """加载全局最新会话（委托给 load_latest）"""
        return load_latest(self._workspace)

    def list_snapshots(self, cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
        """列出所有会话快照（委托给 list_snapshots）"""
        return list_snapshots(self._workspace, limit=limit)

    def load_by_id(self, cwd: str | Path, session_id: str) -> dict[str, Any] | None:
        """按 ID 加载会话（委托给 load_by_id）"""
        return load_by_id(self._workspace, session_id)

    def load_latest_for_session_key(self, session_key: str) -> dict[str, Any] | None:
        """按 session_key 加载最新会话

        用于 gateway 多平台会话隔离：
        - Telegram: session_key = "telegram:{chat_id}:{thread_id}"
        - Slack: session_key = "slack:{channel_id}:{thread_ts}"
        - Discord: session_key = "discord:{channel_id}"
        """
        return load_latest_for_session_key(self._workspace, session_key)

    def export_markdown(
        self,
        *,
        cwd: str | Path,
        messages: list[ConversationMessage],
    ) -> Path:
        """导出会话为 Markdown（委托给 export_session_markdown）"""
        return export_session_markdown(cwd=cwd, workspace=self._workspace, messages=messages)
