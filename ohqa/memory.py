"""Personal memory helpers for ``.ohqa``."""
# ohqa 个人记忆管理模块 - 管理用户的长期记忆和偏好设置

from __future__ import annotations

from pathlib import Path
from re import sub

# ========== ohqa 导入 ==========
from ohqa.workspace import get_memory_dir  # 获取 ~/.ohqa/memory 目录
from ohqa.workspace import get_memory_index_path  # 获取 ~/.ohqa/memory/MEMORY.md 路径


# ========== 记忆文件管理函数组 ==========

def list_memory_files(workspace: str | Path | None = None) -> list[Path]:
    """列出所有个人记忆文件（排除索引文件 MEMORY.md）

    返回排序后的文件列表，例如：
    [
        ~/.ohqa/memory/project_preferences.md,
        ~/.ohqa/memory/frequent_contacts.md,
        ~/.ohqa/memory/work_habits.md
    ]
    """
    memory_dir = get_memory_dir(workspace)
    return sorted(path for path in memory_dir.glob("*.md") if path.name != "MEMORY.md")


# ========== 核心函数：添加记忆条目 ⭐⭐⭐ ==========
def add_memory_entry(workspace: str | Path | None, title: str, content: str) -> Path:
    """创建个人记忆文件并更新索引

    工作流程：
    1. 从 title 生成文件名 slug（小写、连字符）
    2. 创建记忆文件 {slug}.md
    3. 在 MEMORY.md 索引中添加链接

    示例：
    - title = "项目偏好"
    - slug = "项目偏好" → "project_preferences"（假设是英文）
    - 文件路径: ~/.ohqa/memory/project_preferences.md
    - 索引添加: - [项目偏好](project_preferences.md)
    """
    # ========== 步骤1：获取记忆目录并确保存在 ==========
    memory_dir = get_memory_dir(workspace)
    memory_dir.mkdir(parents=True, exist_ok=True)

    # ========== 步骤2：从 title 生成文件名 slug ==========
    # 例如：
    # "Project Preferences" → "project_preferences"
    # "常联系的人" → "常联系的人"（非英文字符保留）
    # "Hello!!! World..." → "hello_world"
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"
    path = memory_dir / f"{slug}.md"

    # ========== 步骤3：写入记忆文件 ==========
    path.write_text(content.strip() + "\n", encoding="utf-8")

    # ========== 步骤4：更新 MEMORY.md 索引 ==========
    index_path = get_memory_index_path(workspace)
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else "# Memory Index\n"

    # 仅当链接不存在时才添加（避免重复）
    if path.name not in existing:
        existing = existing.rstrip() + f"\n- [{title}]({path.name})\n"
        index_path.write_text(existing, encoding="utf-8")

    return path


def remove_memory_entry(workspace: str | Path | None, name: str) -> bool:
    """删除记忆文件并从索引中移除对应链接

    参数：
    - name: 文件名（支持 "project_preferences" 或 "project_preferences.md"）

    返回：
    - True: 删除成功
    - False: 文件不存在

    示例：
    - remove_memory_entry(None, "project_preferences")  # 删除文件并更新索引
    - remove_memory_entry(None, "project_preferences.md")  # 同样支持
    """
    memory_dir = get_memory_dir(workspace)

    # 查找匹配的文件（支持带或不带 .md 扩展名）
    matches = [path for path in memory_dir.glob("*.md") if path.stem == name or path.name == name]
    if not matches:
        return False

    path = matches[0]
    path.unlink(missing_ok=True)  # 删除文件

    # 从索引中移除对应链接
    index_path = get_memory_index_path(workspace)
    if index_path.exists():
        # 过滤掉包含此文件名的行
        lines = [line for line in index_path.read_text(encoding="utf-8").splitlines() if path.name not in line]
        index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    return True


# ========== 核心函数：加载记忆为提示词 ⭐⭐⭐ ==========
def load_memory_prompt(workspace: str | Path | None = None, *, max_files: int = 5) -> str | None:
    """加载个人记忆为提示词格式（供系统提示词使用）

    此函数将个人记忆转换为 AI 可理解的提示词格式，
    会被 prompts.py 的 build_ohqa_system_prompt() 调用。

    输出格式：
    ```markdown
    # ohqa Memory
    - Personal memory directory: /home/user/.ohqa/memory
    - Use this memory for stable user preferences and durable personal context.

    ## MEMORY.md
    ```md
    # Memory Index
    - [项目偏好](project_preferences.md)
    ```

    ## project_preferences.md
    ```md
    # 项目偏好
    ## 代码风格
    - 使用 TypeScript...
    ```
    ```

    参数：
    - max_files: 最多加载多少个记忆文件（默认 5，避免提示词过长）

    返回：
    - 格式化的记忆提示词字符串
    """
    memory_dir = get_memory_dir(workspace)
    index_path = get_memory_index_path(workspace)

    # ========== 步骤1：构建基础信息 ==========
    lines = [
        "# ohqa Memory",
        f"- Personal memory directory: {memory_dir}",
        "- Use this memory for stable user preferences and durable personal context.",
    ]

    # ========== 步骤2：添加索引文件（前 200 行） ==========
    if index_path.exists():
        index_lines = index_path.read_text(encoding="utf-8").splitlines()[:200]
        lines.extend(["", "## MEMORY.md", "```md", *index_lines, "```"])

    # ========== 步骤3：添加具体记忆文件（最多 max_files 个） ==========
    for path in list_memory_files(workspace)[:max_files]:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            continue

        # 每个文件最多 4000 字符（避免提示词过长）
        lines.extend(["", f"## {path.name}", "```md", content[:4000], "```"])

    return "\n".join(lines)
