"""Runtime helpers for ohmo."""
# ohmo 运行时模块 - 提供三种运行模式：后端模式、React TUI、打印模式

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# ========== OpenHarness 导入 ==========
from openharness.api.client import SupportsStreamingMessages  # 流式消息客户端接口
from openharness.engine.stream_events import (
    AssistantTextDelta,       # AI 文本增量事件
    AssistantTurnComplete,    # AI 回合完成事件
    ErrorEvent,               # 错误事件
    StatusEvent,              # 状态事件
)
from openharness.ui.backend_host import run_backend_host  # 后端主机运行器
from openharness.ui.runtime import (
    build_runtime,   # 构建 runtime bundle
    close_runtime,   # 关闭 runtime
    handle_line,     # 处理单行输入
    start_runtime,   # 启动 runtime
)
from openharness.ui.react_launcher import (
    _resolve_npm,    # 解析 npm 可执行文件路径
    _resolve_tsx,    # 解析 tsx 可执行文件路径
    get_frontend_dir, # 获取 React 前端目录
)

# ========== ohmo 导入 ==========
from ohmo.prompts import build_ohmo_system_prompt  # 构建 ohmo 系统提示词
from ohmo.session_storage import OhmoSessionBackend  # ohmo 会话后端
from ohmo.workspace import initialize_workspace  # 初始化工作区


# ========== 运行模式 1：后端模式（--backend-only）⭐⭐⭐ ==========
async def run_ohmo_backend(
    *,
    cwd: str | None = None,  # 当前工作目录（项目路径）
    workspace: str | Path | None = None,  # ohmo 工作区路径（默认 ~/.ohmo）
    model: str | None = None,  # 模型名称
    max_turns: int | None = None,  # 最大回合数
    provider_profile: str | None = None,  # 提供商配置（codex/anthropic/等）
    api_client: SupportsStreamingMessages | None = None,  # API 客户端（可选）
    restore_messages: list[dict] | None = None,  # 恢复的消息历史
    backend_only: bool = True,  # 是否仅运行后端
) -> int:
    """运行 ohmo 后端模式（供 React TUI 使用）

    此函数启动一个标准化的后端服务器，通过 stdio 与 React TUI 通信。
    这是 ohmo 与 OpenHarness 共用的后端协议。

    工作流程：
    1. 初始化 ohmo 工作区
    2. 构建 ohmo 系统提示词
    3. 启动 OpenHarness 后端主机
    4. 使用 OhmoSessionBackend 管理会话持久化
    """
    del backend_only  # 保留参数兼容性
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    initialize_workspace(workspace)  # 初始化工作区（创建目录和模板文件）

    # 调用 OpenHarness 的 run_backend_host，传入 ohmo 特定配置
    return await run_backend_host(
        cwd=cwd_path,  # 当前工作目录
        model=model,  # 模型名称
        max_turns=max_turns,  # 最大回合数
        system_prompt=build_ohmo_system_prompt(cwd_path, workspace=workspace),  # ⭐ 关键：使用 ohmo 系统提示词
        active_profile=provider_profile,  # API 提供商配置
        api_client=api_client,  # 自定义 API 客户端（可选）
        restore_messages=restore_messages,  # 恢复的消息历史
        enforce_max_turns=max_turns is not None,  # 是否强制执行最大回合数
        session_backend=OhmoSessionBackend(workspace),  # ⭐ 关键：使用 ohmo 会话后端
    )


# ========== 辅助函数：构建后端命令行 ==========
def build_ohmo_backend_command(
    *,
    cwd: str | None = None,  # 当前工作目录
    workspace: str | Path | None = None,  # ohmo 工作区路径
    model: str | None = None,  # 模型名称
    max_turns: int | None = None,  # 最大回合数
    provider_profile: str | None = None,  # 提供商配置
) -> list[str]:
    """构建 React TUI 的后端启动命令

    返回格式：
    ["python", "-m", "ohmo", "--backend-only", "--cwd", "/path/to/project", ...]

    这个命令将被传递给 React TUI 的前端进程，前端通过 stdio 与后端通信。
    """
    command = [sys.executable, "-m", "ohmo", "--backend-only"]

    # 添加可选参数
    if cwd:
        command.extend(["--cwd", cwd])
    if workspace:
        command.extend(["--workspace", str(workspace)])
    if model:
        command.extend(["--model", model])
    if max_turns is not None:
        command.extend(["--max-turns", str(max_turns)])
    if provider_profile:
        command.extend(["--profile", provider_profile])

    return command


# ========== 运行模式 2：React TUI（默认模式）⭐⭐⭐ ==========
async def launch_ohmo_react_tui(
    *,
    cwd: str | None = None,  # 当前工作目录
    workspace: str | Path | None = None,  # ohmo 工作区路径
    model: str | None = None,  # 模型名称
    max_turns: int | None = None,  # 最大回合数
    provider_profile: str | None = None,  # 提供商配置
) -> int:
    """启动 React 终端 UI（ohmo 的默认交互模式）

    工作流程：
    1. 检查 React 前端是否存在
    2. 如果 node_modules 不存在，运行 npm install
    3. 初始化 ohmo 工作区
    4. 构建后端命令并通过环境变量传递给前端
    5. 启动 tsx 运行 React 前端
    6. 前端通过 stdio 与后端通信（后端由前端自动启动）

    架构：
    ┌─────────────────┐
    │  React TUI 前端  │ (tsx 进程)
    │  (用户体验层)    │
    └────────┬────────┘
             │ stdio
    ┌────────▼────────┐
    │  ohmo 后端       │ (python -m ohmo --backend-only)
    │  (业务逻辑层)    │
    └────────┬────────┘
             │ HTTP
    ┌────────▼────────┐
    │  Claude API      │
    └─────────────────┘
    """
    # ========== 步骤1：检查前端是否存在 ==========
    frontend_dir = get_frontend_dir()
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        raise RuntimeError(f"React terminal frontend is missing: {package_json}")

    # ========== 步骤2：安装前端依赖（如果需要） ==========
    npm = _resolve_npm()
    if not (frontend_dir / "node_modules").exists():
        # 运行 npm install（首次运行时）
        install = await asyncio.create_subprocess_exec(
            npm,
            "install",
            "--no-fund",  # 不显示赞助信息
            "--no-audit",  # 跳过审计（加快安装）
            cwd=str(frontend_dir),
        )
        if await install.wait() != 0:
            raise RuntimeError("Failed to install React terminal frontend dependencies")

    # ========== 步骤3：初始化 ohmo 工作区 ==========
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    initialize_workspace(workspace)

    # ========== 步骤4：构建前端配置环境变量 ==========
    env = os.environ.copy()
    env["OPENHARNESS_FRONTEND_CONFIG"] = json.dumps(
        {
            "backend_command": build_ohmo_backend_command(  # ⭐ 关键：后端启动命令
                cwd=cwd_path,
                workspace=workspace,
                model=model,
                max_turns=max_turns,
                provider_profile=provider_profile,
            ),
            "initial_prompt": None,  # 初始提示词（可选）
            "theme": "default",  # UI 主题
        }
    )

    # ========== 步骤5：启动 React 前端进程 ==========
    tsx_cmd = _resolve_tsx(frontend_dir)
    process = await asyncio.create_subprocess_exec(
        *tsx_cmd,
        "src/index.tsx",  # React 应用入口
        cwd=str(frontend_dir),
        env=env,  # 传入包含后端命令的环境变量
        stdin=None,  # 前端不使用标准输入
        stdout=None,  # 前端直接使用终端
        stderr=None,  # 前端直接使用终端
    )

    # ========== 步骤6：等待前端进程结束 ==========
    return await process.wait()


# ========== 运行模式 3：打印模式（--print/-p）⭐⭐⭐ ==========
async def run_ohmo_print_mode(
    *,
    prompt: str,  # 用户提示词
    cwd: str | None = None,  # 当前工作目录
    workspace: str | Path | None = None,  # ohmo 工作区路径
    model: str | None = None,  # 模型名称
    max_turns: int | None = None,  # 最大回合数
    provider_profile: str | None = None,  # 提供商配置
) -> int:
    """运行单次查询模式（适合脚本和管道）

    与 React TUI 不同，此模式：
    - 不启动 UI，直接在 stdout 输出 AI 响应
    - 运行单次查询后退出
    - 适合脚本调用：ohmo -p "帮我生成代码" > output.md

    输出格式：
    - AI 响应：stdout（流式输出）
    - 系统消息/错误：stderr
    """
    cwd_path = str(Path(cwd or Path.cwd()).resolve())
    initialize_workspace(workspace)

    # ========== 步骤1：切换到目标工作目录 ==========
    previous_cwd = Path.cwd()
    os.chdir(cwd_path)  # 切换到项目目录（确保工具在正确位置执行）
    try:
        # ========== 步骤2：构建 runtime bundle ==========
        bundle = await build_runtime(
            model=model,
            max_turns=max_turns,
            system_prompt=build_ohmo_system_prompt(cwd_path, workspace=workspace),  # ⭐ ohmo 系统提示词
            active_profile=provider_profile,
            session_backend=OhmoSessionBackend(workspace),  # ⭐ ohmo 会话后端
            enforce_max_turns=max_turns is not None,
        )
        await start_runtime(bundle)

        # ========== 步骤3：定义事件处理函数 ==========

        # 系统消息处理（输出到 stderr，不干扰 stdout）
        async def _print_system(message: str) -> None:
            print(message, file=sys.stderr)

        # 流式事件处理
        async def _render_event(event) -> None:
            if isinstance(event, AssistantTextDelta):
                # AI 文本增量：直接写入 stdout（流式输出）
                sys.stdout.write(event.text)
                sys.stdout.flush()
            elif isinstance(event, AssistantTurnComplete):
                # AI 回合完成：添加换行符
                sys.stdout.write("\n")
                sys.stdout.flush()
            elif isinstance(event, ErrorEvent):
                # 错误事件：输出到 stderr
                print(event.message, file=sys.stderr)
            elif isinstance(event, StatusEvent):
                # 状态事件：输出到 stderr
                print(event.message, file=sys.stderr)

        # 清空输出（打印模式不需要清屏）
        async def _clear_output() -> None:
            return None

        # ========== 步骤4：处理用户输入并输出 AI 响应 ==========
        await handle_line(
            bundle,
            prompt,
            print_system=_print_system,  # 系统消息处理
            render_event=_render_event,  # 流式事件处理
            clear_output=_clear_output,  # 清空输出（空操作）
        )

        # ========== 步骤5：清理并退出 ==========
        await close_runtime(bundle)
        return 0
    finally:
        # ========== 步骤6：恢复原始工作目录 ==========
        os.chdir(previous_cwd)
