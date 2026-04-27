"""Structured protocol models for the React TUI backend."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.state.app_state import AppState
from openharness.bridge.manager import BridgeSessionRecord
from openharness.mcp.types import McpConnectionStatus
from openharness.tasks.types import TaskRecord


class FrontendRequest(BaseModel):
    """One request sent from the React frontend to the Python backend."""       #从前端发送给 Python 后端的一个 消息对象封装。
    """
      | 请求类型             | 处理方式                     | 说明                           |
      |----------------------|------------------------------|--------------------------------|
      | shutdown             | 发送 shutdown 事件并退出循环 | 前端主动断开连接               |
      | permission_response  | 设置 Future 结果             | 响应后端的权限请求（弹窗确认）  |
      | question_response    | 设置 Future 结果             | 响应后端的问题请求（弹窗提问）  |
      | list_sessions        | 调用处理方法                 | 列出所有会话                 |
      | select_command       | 调用处理方法                 | 选择要执行的命令              |
      | apply_select_command | 执行命令并控制会话忙碌状态   | 实际执行选中的命令               |
      | submit_line          | 处理用户输入                  | 提交用户输入的一行内容        |
    """
    type: Literal[
        "submit_line",
        "permission_response",
        "question_response",
        "list_sessions",
        "select_command",
        "apply_select_command",
        "shutdown",
    ]
    line: str | None = None
    command: str | None = None
    value: str | None = None
    request_id: str | None = None
    allowed: bool | None = None
    answer: str | None = None


class TranscriptItem(BaseModel):
    """One transcript row rendered by the frontend."""

    role: Literal["system", "user", "assistant", "tool", "tool_result", "log"]
    text: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    is_error: bool | None = None


class TaskSnapshot(BaseModel):
    """UI-safe task representation."""

    id: str
    type: str
    status: str
    description: str
    metadata: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: TaskRecord) -> "TaskSnapshot":
        return cls(
            id=record.id,
            type=record.type,
            status=record.status,
            description=record.description,
            metadata=dict(record.metadata),
        )


class BackendEvent(BaseModel):
    """One event sent from the Python backend to the React frontend.""" # 从 Python 后端发送的一次事件传到了 React 前端。 消息对象封装

    """
        响应类型说明
        "ready",              # 后端启动完成，通知前端已准备就绪
        "state_snapshot",     # 应用状态快照（包含 app_state、mcp_servers、bridge_sessions）
        "tasks_snapshot",     # 任务列表快照
        "transcript_item",    # 会话记录项（收到的内容返回给前端）
        "assistant_delta",    # 助手流式输出增量数据（用于实现打字效果）
        "assistant_complete", # 助手响应完成（标志整个响应结束）
        "line_complete",      # 单行处理完成（用户输入的一行已处理完毕）
        "tool_started",       # 工具开始执行（通知前端显示工具执行状态）
        "tool_completed",     # 工具执行完成（包含工具名称、输入、输出等信息）
        "clear_transcript",   # 清除会话记录（前端清空对话历史）
        "modal_request",      # 模态框请求（权限确认或问题弹窗）
        "select_request",     # 选择请求（让用户从选项中选择）
        "todo_update",        # 待办事项更新（todo 列表变化通知）
        "plan_mode_change",   # 计划模式变更（进入/退出计划模式）
        "swarm_status",       # 群组状态更新（多协作任务状态）
        "error",              # 错误事件（后端发生的错误）
        "shutdown",           # 关闭事件（后端即将关闭）
    """
    type: Literal[
        "ready",
        "state_snapshot",
        "tasks_snapshot",
        "transcript_item",
        "compact_progress",
        "assistant_delta",
        "assistant_complete",
        "line_complete",
        "tool_started",
        "tool_completed",
        "clear_transcript",
        "modal_request",
        "select_request",
        "todo_update",
        "plan_mode_change",
        "swarm_status",
        "error",
        "shutdown",
    ]
    select_options: list[dict[str, Any]] | None = None
    message: str | None = None
    item: TranscriptItem | None = None
    state: dict[str, Any] | None = None
    tasks: list[TaskSnapshot] | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    bridge_sessions: list[dict[str, Any]] | None = None
    commands: list[str] | None = None
    modal: dict[str, Any] | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    output: str | None = None
    is_error: bool | None = None
    compact_phase: str | None = None
    compact_trigger: str | None = None
    attempt: int | None = None
    compact_checkpoint: str | None = None
    compact_metadata: dict[str, Any] | None = None
    # New fields for enhanced events
    todo_markdown: str | None = None
    plan_mode: str | None = None
    swarm_teammates: list[dict[str, Any]] | None = None
    swarm_notifications: list[dict[str, Any]] | None = None

    @classmethod
    def ready(
        cls,
        state: AppState,
        tasks: list[TaskRecord],
        commands: list[str],
    ) -> "BackendEvent":
        return cls(
            type="ready",
            state=_state_payload(state),
            tasks=[TaskSnapshot.from_record(task) for task in tasks],
            mcp_servers=[],
            bridge_sessions=[],
            commands=commands,
        )

    @classmethod
    def state_snapshot(cls, state: AppState) -> "BackendEvent":
        return cls(type="state_snapshot", state=_state_payload(state))

    @classmethod
    def tasks_snapshot(cls, tasks: list[TaskRecord]) -> "BackendEvent":
        return cls(
            type="tasks_snapshot",
            tasks=[TaskSnapshot.from_record(task) for task in tasks],
        )

    @classmethod
    def status_snapshot(
        cls,
        *,
        state: AppState,
        mcp_servers: list[McpConnectionStatus],
        bridge_sessions: list[BridgeSessionRecord],
    ) -> "BackendEvent":
        return cls(
            type="state_snapshot",
            state=_state_payload(state),
            mcp_servers=[
                {
                    "name": server.name,
                    "state": server.state,
                    "detail": server.detail,
                    "transport": server.transport,
                    "auth_configured": server.auth_configured,
                    "tool_count": len(server.tools),
                    "resource_count": len(server.resources),
                }
                for server in mcp_servers
            ],
            bridge_sessions=[
                {
                    "session_id": session.session_id,
                    "command": session.command,
                    "cwd": session.cwd,
                    "pid": session.pid,
                    "status": session.status,
                    "started_at": session.started_at,
                    "output_path": session.output_path,
                }
                for session in bridge_sessions
            ],
        )


def _state_payload(state: AppState) -> dict[str, Any]:
    return {
        "model": state.model,
        "cwd": state.cwd,
        "provider": state.provider,
        "auth_status": state.auth_status,
        "base_url": state.base_url,
        "permission_mode": _format_permission_mode(state.permission_mode),
        "theme": state.theme,
        "vim_enabled": state.vim_enabled,
        "voice_enabled": state.voice_enabled,
        "voice_available": state.voice_available,
        "voice_reason": state.voice_reason,
        "fast_mode": state.fast_mode,
        "effort": state.effort,
        "passes": state.passes,
        "mcp_connected": state.mcp_connected,
        "mcp_failed": state.mcp_failed,
        "bridge_sessions": state.bridge_sessions,
        "output_style": state.output_style,
        "keybindings": dict(state.keybindings),
    }


_MODE_LABELS = {
    "default": "Default",
    "plan": "Plan Mode",
    "full_auto": "Auto",
    "PermissionMode.DEFAULT": "Default",
    "PermissionMode.PLAN": "Plan Mode",
    "PermissionMode.FULL_AUTO": "Auto",
}


def _format_permission_mode(raw: str) -> str:
    """Convert raw permission mode to human-readable label."""
    return _MODE_LABELS.get(raw, raw)


__all__ = [
    "BackendEvent",
    "FrontendRequest",
    "TaskSnapshot",
    "TranscriptItem",
]
