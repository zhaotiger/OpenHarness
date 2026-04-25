"""CLI entry point for the ohmo personal-agent app."""
# ohmo 个人应用的 CLI 入口点

from __future__ import annotations  # 启用后注解类型（Python 3.7+ 兼容）

import asyncio  # 异步 I/O（用于运行后端服务）
import sys
from pathlib import Path

import typer  # 类型提示 + CLI 框架

# ========== OpenHarness 核心导入 ==========
from openharness.auth.manager import AuthManager  # 认证管理器（处理 API 密钥、OAuth 等）
from openharness.config import load_settings  # 加载全局配置（~/.openharness/settings.json）

# ========== ohmo 网关相关导入 ==========
from ohmo.gateway.config import load_gateway_config, save_gateway_config  # 网关配置读写
from ohmo.gateway.models import GatewayConfig  # 网关配置数据模型
from ohmo.gateway.service import (
    OhmoGatewayService,  # 网关服务类（管理多平台消息）
    gateway_status,  # 查询网关运行状态
    start_gateway_process,  # 启动网关后台进程
    stop_gateway_process,  # 停止网关进程
)

# ========== ohmo 核心功能导入 ==========
from ohmo.memory import add_memory_entry, list_memory_files, remove_memory_entry  # 记忆管理
from ohmo.runtime import launch_ohmo_react_tui, run_ohmo_backend, run_ohmo_print_mode  # 运行时
from ohmo.session_storage import OhmoSessionBackend  # 会话持久化
from ohmo.workspace import (
    get_gateway_config_path,  # 获取网关配置文件路径
    get_workspace_root,  # 获取工作区根目录
    get_soul_path,  # 获取 soul.md 路径
    get_state_path,  # 获取 state.json 路径
    get_user_path,  # 获取 user.md 路径
    initialize_workspace,  # 初始化工作区（创建目录和模板文件）
    workspace_health,  # 检查工作区健康状态
)


# ========== Typer 应用结构定义 ==========
app = typer.Typer(
    name="ohmo",
    help="ohmo: a personal-agent app built on top of OpenHarness.",
    invoke_without_command=True,  # 关键：没有子命令时也执行 main() 函数
    add_completion=False,  # 不生成 shell 自动补全脚本
)
# ========== 子命令组 ==========
memory_app = typer.Typer(name="memory", help="Manage .ohmo memory")  # 记忆管理子命令
soul_app = typer.Typer(name="soul", help="Inspect or edit soul.md")  # soul.md 编辑子命令
user_app = typer.Typer(name="user", help="Inspect or edit user.md")  # user.md 编辑子命令
gateway_app = typer.Typer(name="gateway", help="Run the ohmo gateway")  # 网关管理子命令

# ========== 注册子命令到主应用 ==========
app.add_typer(memory_app)  # ohmo memory ...
app.add_typer(soul_app)    # ohmo soul ...
app.add_typer(user_app)    # ohmo user ...
app.add_typer(gateway_app) # ohmo gateway ...

# ========== 支持的交互渠道 ==========
_INTERACTIVE_CHANNELS = ("telegram", "slack", "discord", "feishu")  # 四大即时通讯平台


# ========== UI 辅助函数：检测是否支持交互式终端 ==========
def _can_use_questionary() -> bool:
    """检查是否可以使用 questionary（美化交互库）"""
    # 条件1：stdin 和 stdout 必须是真实的 TTY（终端）
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    # 条件2：不能被重定向（例如：ohmo | grep "xxx"）
    if sys.stdin is not sys.__stdin__ or sys.stdout is not sys.__stdout__:
        return False
    # 条件3：questionary 库必须可用
    try:
        import questionary  # noqa: F401
    except ImportError:
        return False
    return True


def _select_with_questionary(
    title: str,
    options: list[tuple[str, str]],
    *,
    default_value: str | None = None,
) -> str:
    import questionary

    choices = [
        questionary.Choice(
            title=label,
            value=value,
            checked=(value == default_value),
        )
        for value, label in options
    ]
    result = questionary.select(title, choices=choices, default=default_value).ask()
    if result is None:
        raise typer.Abort()
    return str(result)


def _confirm_prompt(message: str, *, default: bool = False) -> bool:
    """Ask for confirmation, preferring questionary in a real TTY."""
    if _can_use_questionary():
        import questionary

        result = questionary.confirm(message, default=default).ask()
        if result is None:
            raise typer.Abort()
        return bool(result)
    return typer.confirm(message, default=default)


def _text_prompt(message: str, *, default: str = "") -> str:
    """Prompt for text input, preferring questionary in a real TTY."""
    if _can_use_questionary():
        import questionary

        result = questionary.text(message, default=default).ask()
        if result is None:
            raise typer.Abort()
        return str(result)
    return typer.prompt(message, default=default)


def _select_from_menu(
    title: str,
    options: list[tuple[str, str]],
    *,
    default_value: str | None = None,
) -> str:
    """Render a simple numbered picker and return the selected value."""
    if _can_use_questionary():
        return _select_with_questionary(title, options, default_value=default_value)
    print(title)
    default_index = 1
    for index, (value, label) in enumerate(options, 1):
        marker = " (default)" if value == default_value else ""
        if value == default_value:
            default_index = index
        print(f"  {index}. {label}{marker}")
    raw = typer.prompt("Choose", default=str(default_index))
    try:
        selected = options[int(raw) - 1]
    except (ValueError, IndexError):
        raise typer.BadParameter(f"Invalid selection: {raw}") from None
    return selected[0]


def _format_provider_profile_label(info: dict[str, object]) -> str:
    label = str(info["label"])
    if bool(info["configured"]):
        return label
    return f"{label} (missing)"


# ========== 配置向导：选择认证提供商 ==========
def _prompt_provider_profile(workspace: str | Path) -> str:
    """引导用户选择 API 提供商（Claude、OpenAI 等）"""
    settings = load_settings()
    # 获取所有可用的认证配置状态
    statuses = AuthManager(settings).get_profile_statuses()
    # 当前网关配置的提供商
    default_value = load_gateway_config(workspace).provider_profile

    # 提供商提示信息（美化显示）
    hints = {
        "claude-api": ("Claude / Kimi / GLM / MiniMax", "fg:#7aa2f7"),  # 蓝色
        "openai-compatible": ("OpenAI / OpenRouter", "fg:#9ece6a"),  # 绿色
    }

    if _can_use_questionary():
        import questionary

        choices = []
        for name, info in statuses.items():
            label = str(info["label"])
            missing = "" if bool(info["configured"]) else " (missing)"
            hint = hints.get(name)
            if hint is None:
                title = label if not missing else [("", label), ("fg:#d3869b", missing)]
            else:
                hint_text, hint_style = hint
                title = [
                    ("", f"{label}  "),
                    (hint_style, hint_text),
                ]
                if missing:
                    title.extend([("", "  "), ("fg:#d3869b", missing.strip())])
            choices.append(questionary.Choice(title=title, value=name, checked=(name == default_value)))
        result = questionary.select("Choose provider profile for ohmo:", choices=choices, default=default_value).ask()
        if result is None:
            raise typer.Abort()
        return str(result)

    options = []
    for name, info in statuses.items():
        label = _format_provider_profile_label(info)
        hint = hints.get(name)
        if hint is not None:
            label = f"{label} ({hint[0]})"
        options.append((name, label))
    return _select_from_menu(
        "Choose provider profile for ohmo:",
        options,
        default_value=default_value,
    )


# ========== 配置向导：配置消息渠道 ==========
def _prompt_channels(existing: GatewayConfig) -> tuple[list[str], dict[str, dict]]:
    """交互式配置四大消息渠道"""
    enabled: list[str] = []  # 启用的渠道列表
    configs: dict[str, dict] = {}  # 各渠道的配置字典

    print("Configure channels for ohmo gateway:")

    # 遍历四大消息平台
    for channel in _INTERACTIVE_CHANNELS:  # telegram, slack, discord, feishu
        current = channel in existing.enabled_channels
        prior = dict(existing.channel_configs.get(channel, {}))

        # 如果已启用，询问是否重新配置
        if current:
            enabled.append(channel)
            if not _confirm_prompt(f"Reconfigure {channel}?", default=False):
                configs[channel] = prior
                continue

        # 如果未启用，询问是否启用
        elif not _confirm_prompt(f"Enable {channel}?", default=False):
            continue
        else:
            enabled.append(channel)

        # ========== 收集渠道特定配置 ==========
        # 通用：允许哪些用户/群组使用（"*" 表示所有人）
        allow_from_raw = _text_prompt(
            f"{channel} allow_from (comma separated, '*' for everyone)",
            default=",".join(prior.get("allow_from", ["*"])) or "*",
        )
        allow_from = [item.strip() for item in allow_from_raw.split(",") if item.strip()] or ["*"]
        config: dict[str, object] = {"allow_from": allow_from}

        # Telegram 特定配置
        if channel == "telegram":
            config["token"] = _text_prompt(
                "Telegram bot token",
                default=str(prior.get("token", "")),
            )
            config["reply_to_message"] = _confirm_prompt(
                "Reply to the original Telegram message?",
                default=bool(prior.get("reply_to_message", True)),
            )
        elif channel == "slack":
            config["bot_token"] = _text_prompt(
                "Slack bot token",
                default=str(prior.get("bot_token", "")),
            )
            config["app_token"] = _text_prompt(
                "Slack app token",
                default=str(prior.get("app_token", "")),
            )
            config["mode"] = "socket"
            config["reply_in_thread"] = _confirm_prompt(
                "Reply in thread?",
                default=bool(prior.get("reply_in_thread", True)),
            )
            config["group_policy"] = _select_from_menu(
                "Slack group policy:",
                [
                    ("mention", "Mention only"),
                    ("open", "Always reply in channels"),
                    ("allowlist", "Only allow configured channels"),
                ],
                default_value=str(prior.get("group_policy", "mention")),
            )
        elif channel == "discord":
            config["token"] = _text_prompt(
                "Discord bot token",
                default=str(prior.get("token", "")),
            )
            config["gateway_url"] = _text_prompt(
                "Discord gateway URL",
                default=str(prior.get("gateway_url", "wss://gateway.discord.gg/?v=10&encoding=json")),
            )
            config["intents"] = int(
                _text_prompt(
                    "Discord intents bitmask",
                    default=str(prior.get("intents", 513)),
                )
            )
            config["group_policy"] = _select_from_menu(
                "Discord group policy:",
                [
                    ("mention", "Mention only"),
                    ("open", "Always reply in channels"),
                ],
                default_value=str(prior.get("group_policy", "mention")),
            )
        elif channel == "feishu":
            config["app_id"] = _text_prompt(
                "Feishu app id",
                default=str(prior.get("app_id", "")),
            )
            config["app_secret"] = _text_prompt(
                "Feishu app secret",
                default=str(prior.get("app_secret", "")),
            )
            config["encrypt_key"] = _text_prompt(
                "Feishu encrypt key",
                default=str(prior.get("encrypt_key", "")),
            )
            config["verification_token"] = _text_prompt(
                "Feishu verification token",
                default=str(prior.get("verification_token", "")),
            )
            config["react_emoji"] = _text_prompt(
                "Feishu reaction emoji",
                default=str(prior.get("react_emoji", "OK")),
            )
        configs[channel] = config
    return enabled, configs


def _run_gateway_config_wizard(workspace: str | Path) -> GatewayConfig:
    """Interactive flow for provider/channel setup."""
    existing = load_gateway_config(workspace)
    provider_profile = _prompt_provider_profile(workspace)
    enabled_channels, channel_configs = _prompt_channels(existing)
    send_progress = _confirm_prompt(
        "Send progress updates to channels?",
        default=existing.send_progress,
    )
    send_tool_hints = _confirm_prompt(
        "Send tool hints to channels?",
        default=existing.send_tool_hints,
    )
    config = existing.model_copy(
        update={
            "provider_profile": provider_profile,
            "enabled_channels": enabled_channels,
            "channel_configs": channel_configs,
            "send_progress": send_progress,
            "send_tool_hints": send_tool_hints,
        }
    )
    save_gateway_config(config, workspace)
    return config


def _print_gateway_config_summary(config: GatewayConfig) -> None:
    if config.enabled_channels:
        print(
            "Configured channels: "
            + ", ".join(config.enabled_channels)
            + f" | provider_profile={config.provider_profile}"
        )
    else:
        print(f"Configured provider_profile={config.provider_profile}; no channels enabled yet.")


def _maybe_restart_gateway(*, cwd: str | Path, workspace: str | Path) -> None:
    state = gateway_status(cwd, workspace)
    if not state.running:
        return
    if not _confirm_prompt("Gateway is running. Restart now to apply changes?", default=True):
        print("Configuration saved. Restart later with `ohmo gateway restart`.")
        return
    stop_gateway_process(cwd, workspace)
    pid = start_gateway_process(cwd, workspace)
    print(f"ohmo gateway restarted (pid={pid})")


# ========== 主命令：ohmo 启动入口 ⭐⭐⭐ ==========
@app.callback(invoke_without_command=True)  # 关键装饰器：没有子命令时也执行
def main(
    ctx: typer.Context,  # Typer 上下文（用于检测是否调用了子命令）
    # ========== 会话模式选项 ==========
    print_mode: str | None = typer.Option(None, "--print", "-p", help="Run a single prompt and exit"),
    model: str | None = typer.Option(None, "--model", help="Model override for this session"),
    profile: str | None = typer.Option(None, "--profile", help="Provider profile to use"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
    max_turns: int | None = typer.Option(None, "--max-turns", help="Override max turns"),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Working directory"),
    # ========== 隐藏选项（内部使用） ==========
    backend_only: bool = typer.Option(False, "--backend-only", hidden=True),  # React TUI 使用
    # ========== 会话恢复选项 ==========
    resume: str | None = typer.Option(None, "--resume", help="Resume an ohmo session by id"),
    continue_session: bool = typer.Option(False, "--continue", help="Continue the latest ohmo session"),
) -> None:
    """启动 ohmo 应用或调用子命令"""

    # ========== 步骤1：检查是否调用了子命令 ==========
    if ctx.invoked_subcommand is not None:  # 例如：ohmo init, ohmo gateway start
        return  # 直接返回，让子命令处理

    # ========== 步骤2：初始化工作区 ==========
    cwd_path = str(Path(cwd).resolve())  # 规范化工作目录
    workspace_root = initialize_workspace(workspace)  # 创建 ~/.ohmo/ 和模板文件
    backend = OhmoSessionBackend(workspace_root)  # 创建会话存储后端

    # ========== 步骤3：恢复会话（可选） ==========
    restore_messages = None

    if continue_session:  # --continue：恢复最新会话
        latest = backend.load_latest(cwd_path)
        if latest is None:
            print("No previous ohmo session found in this directory.", file=sys.stderr)
            raise typer.Exit(1)
        restore_messages = latest.get("messages")

    elif resume:  # --resume <session_id>：恢复指定会话
        snapshot = backend.load_by_id(cwd_path, resume)
        if snapshot is None:
            print(f"ohmo session not found: {resume}", file=sys.stderr)
            raise typer.Exit(1)
        restore_messages = snapshot.get("messages")

    # ========== 步骤4：根据模式启动 ==========

    # 模式1：backend-only（React TUI 使用）
    if backend_only:
        raise SystemExit(
            asyncio.run(
                run_ohmo_backend(  # 调用 OpenHarness 的后端主机
                    cwd=cwd_path,
                    workspace=workspace_root,
                    model=model,
                    max_turns=max_turns,
                    provider_profile=profile,
                    restore_messages=restore_messages,
                )
            )
        )

    # 模式2：print-mode（单次执行）
    if print_mode is not None:
        raise SystemExit(
            asyncio.run(
                run_ohmo_print_mode(  # 运行单个提示词并退出
                    prompt=print_mode,
                    cwd=cwd_path,
                    workspace=workspace_root,
                    model=model,
                    max_turns=max_turns,
                    provider_profile=profile,
                )
            )
        )

    # 模式3：React TUI（默认，交互式终端界面）
    raise SystemExit(
        asyncio.run(
            launch_ohmo_react_tui(  # 启动 React 终端 UI
                cwd=cwd_path,
                workspace=workspace_root,
                model=model,
                max_turns=max_turns,
                provider_profile=profile,
            )
        )
    )


# ========== 子命令组：工作区管理 ==========
@app.command("init")
def init_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory (reserved for future project overrides)"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Run the provider/channel setup wizard when attached to a terminal",
    ),
) -> None:
    """Initialize the .ohmo workspace."""
    root_path = get_workspace_root(workspace)
    already_exists = root_path.exists()
    root = initialize_workspace(root_path)
    print(f"Initialized ohmo workspace at {root}")
    if already_exists:
        print("ohmo workspace already exists.")
        if not interactive:
            print("Use `ohmo config` to update provider and channel settings.")
            return
        if not _confirm_prompt("Open configuration now?", default=True):
            print("Use `ohmo config` when you want to change provider or channel settings.")
            return
    if interactive:
        config = _run_gateway_config_wizard(root)
        _print_gateway_config_summary(config)
        print(f"Saved gateway config to {get_gateway_config_path(root)}")


@app.command("config")
def config_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    """Configure provider profile and gateway channels."""
    cwd_path = str(Path(cwd).resolve())
    workspace_root = initialize_workspace(workspace)
    config = _run_gateway_config_wizard(workspace_root)
    _print_gateway_config_summary(config)
    print(f"Saved gateway config to {get_gateway_config_path(workspace_root)}")
    _maybe_restart_gateway(cwd=cwd_path, workspace=workspace_root)


@app.command("doctor")
def doctor_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    """Check .ohmo workspace and provider readiness."""
    cwd_path = str(Path(cwd).resolve())
    workspace_root = initialize_workspace(workspace)
    health = workspace_health(workspace_root)
    settings = load_settings()
    statuses = AuthManager(settings).get_profile_statuses()
    lines = ["ohmo doctor:"]
    for name, ok in health.items():
        lines.append(f"- {name}: {'ok' if ok else 'missing'}")
    lines.append(f"- project_cwd: {cwd_path}")
    lines.append(f"- workspace_root: {workspace_root}")
    lines.append(f"- workspace_state: {get_state_path(workspace_root)}")
    lines.append(f"- gateway_config: {get_gateway_config_path(workspace_root)}")
    lines.append("- available_profiles:")
    for name, info in statuses.items():
        lines.append(
            f"  - {name}: {info['label']} ({'configured' if info['configured'] else 'missing auth'})"
        )
    print("\n".join(lines))


@memory_app.command("list")
def memory_list_cmd(workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)")) -> None:
    for path in list_memory_files(workspace):
        print(path.name)


@memory_app.command("add")
def memory_add_cmd(
    title: str = typer.Argument(...),
    content: str = typer.Argument(...),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    path = add_memory_entry(workspace, title, content)
    print(f"Added memory entry {path.name}")


@memory_app.command("remove")
def memory_remove_cmd(
    name: str = typer.Argument(...),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    if remove_memory_entry(workspace, name):
        print(f"Removed memory entry {name}")
        return
    print(f"Memory entry not found: {name}", file=sys.stderr)
    raise typer.Exit(1)


def _show_or_edit(path: Path, set_text: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if set_text is not None:
        path.write_text(set_text.strip() + "\n", encoding="utf-8")
        print(f"Updated {path}")
        return
    if not path.exists():
        print(f"{path} does not exist yet.", file=sys.stderr)
        raise typer.Exit(1)
    print(path.read_text(encoding="utf-8"))


@soul_app.command("show")
def soul_show_cmd(workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)")) -> None:
    _show_or_edit(get_soul_path(workspace), None)


@soul_app.command("edit")
def soul_edit_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
    set_text: str | None = typer.Option(None, "--set", help="Replace soul.md with this text"),
) -> None:
    _show_or_edit(get_soul_path(workspace), set_text)


@user_app.command("show")
def user_show_cmd(workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)")) -> None:
    _show_or_edit(get_user_path(workspace), None)


@user_app.command("edit")
def user_edit_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
    set_text: str | None = typer.Option(None, "--set", help="Replace user.md with this text"),
) -> None:
    _show_or_edit(get_user_path(workspace), set_text)


# ========== 子命令组：网关管理 ==========
@gateway_app.command("run")
def gateway_run_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    """Run the ohmo gateway in the foreground."""
    service = OhmoGatewayService(cwd, workspace)
    raise SystemExit(asyncio.run(service.run_foreground()))


@gateway_app.command("start")
def gateway_start_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    pid = start_gateway_process(cwd, workspace)
    print(f"ohmo gateway started (pid={pid})")


@gateway_app.command("stop")
def gateway_stop_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    if stop_gateway_process(cwd, workspace):
        print("ohmo gateway stopped.")
        return
    print("ohmo gateway is not running.")


@gateway_app.command("restart")
def gateway_restart_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    stop_gateway_process(cwd, workspace)
    pid = start_gateway_process(cwd, workspace)
    print(f"ohmo gateway restarted (pid={pid})")


@gateway_app.command("status")
def gateway_status_cmd(
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="Project working directory"),
    workspace: str | None = typer.Option(None, "--workspace", help="Path to the ohmo workspace (defaults to ~/.ohmo)"),
) -> None:
    state = gateway_status(cwd, workspace)
    print(state.model_dump_json(indent=2))
