"""Prompt assembly for ohqa persona and workspace context."""
# ohqa 人格和工作区上下文的提示词组装

from __future__ import annotations

from pathlib import Path

# ========== OpenHarness 导入 ==========
from openharness.memory import load_memory_prompt as load_project_memory_prompt  # 加载项目级记忆
from openharness.prompts.system_prompt import get_base_system_prompt  # 获取 OpenHarness 基础系统提示词

# ========== ohqa 导入 ==========
from ohqa.memory import load_memory_prompt as load_ohqa_memory_prompt  # 加载 ohqa 个人记忆
from ohqa.workspace import (
    get_bootstrap_path,  # 获取首次运行引导文件路径
    get_identity_path,  # 获取身份文件路径
    get_soul_path,  # 获取灵魂文件路径
    get_user_path,  # 获取用户文件路径
    get_workspace_root,  # 获取工作区根目录
)


# ========== 辅助函数：安全读取文件内容 ==========
def _read_text(path: Path) -> str | None:
    """读取文件内容，如果文件不存在或为空则返回 None"""
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    return content or None


# ========== 核心函数：构建 ohqa 系统提示词 ⭐⭐⭐ ==========
def build_ohqa_system_prompt(
    cwd: str | Path,  # 当前工作目录
    *,
    workspace: str | Path | None = None,  # ohqa 工作区路径（默认 ~/.ohqa）
    extra_prompt: str | None = None,  # 额外的提示词（可选）
    include_project_memory: bool = False,  # 是否包含项目级记忆
) -> str:
    """构建 ohqa 会话的自定义基础提示词

    这是 ohqa 的核心函数，负责组装个性化的 AI 系统提示词。
    提示词由多个部分拼接而成，形成 ohqa 的完整人格。
    """
    # ========== 步骤1：获取工作区根目录 ==========
    root = get_workspace_root(workspace)

    # ========== 步骤2：初始化提示词部分列表 ==========
    sections = [get_base_system_prompt()]  # 从 OpenHarness 基础提示词开始

    # ========== 步骤3：添加额外指令（如果提供） ==========
    if extra_prompt:
        sections.extend(["# Additional Instructions", extra_prompt.strip()])

    # ========== 步骤4：添加 ohqa "灵魂" - 核心行为准则 ==========
    soul = _read_text(get_soul_path(root))  # 读取 ~/.ohqa/soul.md
    if soul:
        sections.extend(["# ohqa Soul", soul])

    # ========== 步骤5：添加身份标识 ==========
    identity = _read_text(get_identity_path(root))  # 读取 ~/.ohqa/identity.md
    if identity:
        sections.extend(["# ohqa Identity", identity])

    # ========== 步骤6：添加用户画像 ==========
    user = _read_text(get_user_path(root))  # 读取 ~/.ohqa/user.md
    if user:
        sections.extend(["# User Profile", user])

    # ========== 步骤7：添加首次运行引导（如果存在） ==========
    bootstrap = _read_text(get_bootstrap_path(root))  # 读取 ~/.ohqa/BOOTSTRAP.md
    if bootstrap:
        sections.extend(["# First-Run Bootstrap", bootstrap])

    # ========== 步骤8：添加工作区说明 ==========
    sections.extend(
        [
            "# ohqa Workspace",
            f"- Personal workspace root: {root}",
            "- Personal memory and sessions live under the shared ohqa workspace root.",
            "- Resume only within ohqa sessions; do not assume interoperability with plain OpenHarness sessions.",
        ]
    )

    # ========== 步骤9：添加个人记忆（ohqa 专属） ==========
    if ohqa_memory := load_ohqa_memory_prompt(root):  # 从 ~/.ohqa/memory/ 加载
        sections.append(ohqa_memory)

    # ========== 步骤10：可选：添加项目级记忆 ==========
    if include_project_memory:  # 从当前项目的 .memory/ 加载
        project_memory = load_project_memory_prompt(cwd)
        if project_memory:
            sections.append(project_memory)

    # ========== 步骤11：拼接所有部分 ==========
    return "\n\n".join(section for section in sections if section and section.strip())
