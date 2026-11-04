from pathlib import Path
import hashlib
import os
import re
import tempfile
import threading

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest


# ============================================================
# 基础配置
# ============================================================

PROJECT_ROOT = Path(
    r"E:\No Place Without You\no-place-without-you"
)

DOCS_DIR_NAME = "md"

ALLOWED_DOCS = {
    "claude.md": "CLAUDE.md",
    "architecture.md": "ARCHITECTURE.md",
    "development_status.md": "DEVELOPMENT_STATUS.md",
    "ai_handoff.md": "AI_HANDOFF.md",
    "personality.md": "PERSONALITY.md",
    "memory.md": "MEMORY.md",
}

WRITABLE_DOCS = {
    "MEMORY.md",
    "PERSONALITY.md",
}

PERSONALITY_DOC = "PERSONALITY.md"
MEMORY_DOC = "MEMORY.md"

MAX_FILE_SIZE = 512 * 1024
MAX_READ_CHARS = 18000
MAX_SEARCH_RESULTS = 8
MAX_PROPOSAL_CHARS = 30000
MAX_QUERY_LENGTH = 100

PERSONALITY_MARKER = "<shared_personality_core>"

PENDING_PROPOSALS = {}
PROPOSAL_LOCK = threading.Lock()
FILE_LOCK = threading.Lock()


# ============================================================
# 安全路径
# ============================================================

def get_safe_docs_root():
    """获取 md 目录，并拒绝符号链接目录。"""

    try:
        project_root = PROJECT_ROOT.resolve(strict=True)

        docs_dir = PROJECT_ROOT / DOCS_DIR_NAME

        if docs_dir.is_symlink():
            return None, "md 目录不能是符号链接。"

        resolved = docs_dir.resolve(strict=True)

        if resolved.parent != project_root:
            return None, "md 目录路径检查失败。"

        if not resolved.is_dir():
            return None, "md 路径不是目录。"

        return resolved, None

    except Exception:
        return None, "项目 md 目录不存在或无法访问。"


def get_safe_doc_path(filename: str):
    """只允许访问 md 目录中的白名单文件。"""

    if filename not in ALLOWED_DOCS.values():
        return None, "文件不在允许访问的白名单中。"

    root, error = get_safe_docs_root()

    if error:
        return None, error

    target = root / filename

    try:
        if target.is_symlink():
            return None, "目标文件不能是符号链接。"

        resolved = target.resolve(strict=True)

        if resolved.parent != root:
            return None, "文件路径检查失败。"

        if resolved.name != filename:
            return None, "文件名称检查失败。"

        if not resolved.is_file():
            return None, "指定路径不是文件。"

        return resolved, None

    except FileNotFoundError:
        return None, f"找不到文件：{filename}"

    except Exception as exc:
        return None, f"文件检查失败：{type(exc).__name__}"


def read_allowed_doc(doc_name: str):
    """读取白名单文件。"""

    if not isinstance(doc_name, str):
        return None, "文件名必须是字符串。"

    key = doc_name.strip().lower()

    if key not in ALLOWED_DOCS:
        return None, (
            "不允许读取该文件。可选文件："
            + "、".join(ALLOWED_DOCS.values())
        )

    filename = ALLOWED_DOCS[key]

    target, error = get_safe_doc_path(filename)

    if error:
        return None, error

    try:
        if target.stat().st_size > MAX_FILE_SIZE:
            return None, "文件超过大小限制。"

        content = target.read_text(encoding="utf-8")

        return content[:MAX_READ_CHARS], None

    except UnicodeDecodeError:
        return None, "文件不是 UTF-8 编码。"

    except Exception as exc:
        return None, f"读取失败：{type(exc).__name__}"


def calculate_sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


# ============================================================
# 人格缓存
# ============================================================

class ProjectKnowledgePlugin(Star):

    def __init__(self, context: Context):
        super().__init__(context)

        self.personality_cache = ""
        self.personality_cache_error = None

        self.refresh_personality_cache()

    def refresh_personality_cache(self):
        """从磁盘读取人格文件并更新内存缓存。"""

        content, error = read_allowed_doc(PERSONALITY_DOC)

        if error:
            self.personality_cache = ""
            self.personality_cache_error = error
            return False

        self.personality_cache = content.strip()
        self.personality_cache_error = None

        return True

    @filter.on_llm_request()
    async def inject_personality(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ):
        """
        将缓存的人格核心加入系统提示词。

        不在这里读取 MEMORY.md。
        不在这里进行任何文件写入。
        """

        personality = self.personality_cache

        if not personality:
            return

        current_prompt = req.system_prompt or ""

        # 避免同一请求重复注入
        if PERSONALITY_MARKER in current_prompt:
            return

        req.system_prompt = (
            current_prompt
            + "\n\n"
            + PERSONALITY_MARKER
            + "\n"
            + personality
            + "\n"
            + PERSONALITY_MARKER
        )

    # ========================================================
    # 通用搜索
    # ========================================================

    def search_documents(self, query: str, filenames):
        if not isinstance(query, str):
            return "搜索关键词必须是字符串。"

        query = query.strip()

        if not query:
            return "搜索关键词不能为空。"

        if len(query) > MAX_QUERY_LENGTH:
            return f"搜索关键词不能超过 {MAX_QUERY_LENGTH} 个字符。"

        terms = [
            term.casefold()
            for term in re.split(r"[\s,，、;；]+", query)
            if term
        ]

        results = []

        for filename in filenames:
            content, error = read_allowed_doc(filename)

            if error or content is None:
                continue

            lines = content.splitlines()

            for index, line in enumerate(lines):
                lowered = line.casefold()

                if query.casefold() in lowered:
                    score = lowered.count(query.casefold())

                elif len(terms) > 1 and any(
                    term in lowered for term in terms
                ):
                    score = sum(
                        lowered.count(term)
                        for term in terms
                        if term in lowered
                    )

                else:
                    continue

                start = max(0, index - 1)
                end = min(len(lines), index + 2)

                excerpt = "\n".join(lines[start:end])

                results.append(
                    (score, filename, index + 1, excerpt)
                )

        if not results:
            return f"没有找到与“{query}”相关的内容。"

        results.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        output = [f"搜索关键词：{query}\n"]

        for score, filename, line_number, excerpt in (
            results[:MAX_SEARCH_RESULTS]
        ):
            output.append(
                f"\n【{filename} · 第 {line_number} 行】\n"
                f"{excerpt}"
            )

        return "\n".join(output)[:12000]

    # ========================================================
    # 通用修改提案
    # ========================================================

    async def create_proposal(
        self,
        event: AstrMessageEvent,
        filename: str,
        reason: str,
        new_content: str,
    ):
        if filename not in WRITABLE_DOCS:
            return "安全检查失败：该文件不允许修改。"

        if not isinstance(reason, str) or not reason.strip():
            return "提案失败：必须提供修改理由。"

        if not isinstance(new_content, str) or not new_content.strip():
            return "提案失败：必须提供完整的新文件内容。"

        if len(new_content) > MAX_PROPOSAL_CHARS:
            return "提案失败：新内容超过长度限制。"

        session_id = getattr(
            event,
            "unified_msg_origin",
            None,
        )

        if not session_id:
            return "提案失败：无法识别当前会话。"

        session_id = str(session_id)

        target, error = get_safe_doc_path(filename)

        if error:
            return f"提案失败：{error}"

        try:
            original_bytes = target.read_bytes()

            if len(original_bytes) > MAX_FILE_SIZE:
                return "提案失败：原文件超过大小限制。"

            original_content = original_bytes.decode("utf-8")

            original_hash = calculate_sha256(original_bytes)

        except Exception as exc:
            return f"提案失败：{type(exc).__name__}"

        if new_content == original_content:
            return "提案取消：新内容与当前文件完全相同。"

        proposal = {
            "filename": filename,
            "reason": reason.strip(),
            "new_content": new_content,
            "original_hash": original_hash,
        }

        # 每个会话只保留一个待确认提案
        with PROPOSAL_LOCK:
            PENDING_PROPOSALS[session_id] = proposal

        confirm_text = (
            "确认修改记忆"
            if filename == MEMORY_DOC
            else "确认修改人格"
        )

        return (
            f"【{filename} 修改提案】\n\n"
            f"修改理由：\n{reason.strip()}\n\n"
            "以下是完整的新文件内容：\n\n"
            "==============================\n"
            f"{new_content}\n"
            "==============================\n\n"
            "当前文件尚未发生任何修改。\n\n"
            f"只有当用户单独发送：\n{confirm_text}\n"
            "才允许执行保存。\n\n"
            "如果需要调整，可以先修改提案。"
        )

    # ========================================================
    # 通用确认与写入
    # ========================================================

    async def confirm_proposal(
        self,
        event: AstrMessageEvent,
        expected_filename: str,
        expected_phrase: str,
    ):
        message = getattr(event, "message_str", "")

        if not isinstance(message, str):
            return "确认失败：无法读取当前消息。"

        if message.strip() != expected_phrase:
            return (
                "确认失败：必须单独发送精确文本："
                + expected_phrase
            )

        session_id = getattr(
            event,
            "unified_msg_origin",
            None,
        )

        if not session_id:
            return "确认失败：无法识别当前会话。"

        session_id = str(session_id)

        with PROPOSAL_LOCK:
            proposal = PENDING_PROPOSALS.get(session_id)

        if proposal is None:
            return "确认失败：当前会话没有待确认的修改提案。"

        if proposal.get("filename") != expected_filename:
            return (
                "确认失败：当前提案的目标文件与确认类型不一致。"
            )

        target, error = get_safe_doc_path(expected_filename)

        if error:
            return f"保存失败：{error}"

        temp_path = None

        try:
            with FILE_LOCK:

                current_bytes = target.read_bytes()

                if calculate_sha256(current_bytes) != proposal["original_hash"]:
                    with PROPOSAL_LOCK:
                        PENDING_PROPOSALS.pop(session_id, None)

                    return (
                        "保存取消：文件在提案生成后发生了变化。\n"
                        "为了避免覆盖新修改，本次提案已经失效。"
                    )

                new_content = proposal["new_content"]

                if not isinstance(new_content, str) or not new_content.strip():
                    return "保存失败：新文件内容无效。"

                new_bytes = new_content.encode("utf-8")

                if len(new_bytes) > MAX_FILE_SIZE:
                    return "保存失败：新文件超过大小限制。"

                # 保留原文件的 CRLF 换行风格
                if b"\r\n" in current_bytes:
                    new_content = new_content.replace(
                        "\r\n", "\n"
                    ).replace(
                        "\n", "\r\n"
                    )

                    new_bytes = new_content.encode("utf-8")

                target_again, error = get_safe_doc_path(
                    expected_filename
                )

                if error or target_again != target:
                    return "保存失败：目标路径检查未通过。"

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=str(target.parent),
                    prefix=".project_update_",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:

                    temp_path = Path(temp_file.name)

                    temp_file.write(new_bytes)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())

                latest_bytes = target.read_bytes()

                if calculate_sha256(latest_bytes) != proposal["original_hash"]:
                    with PROPOSAL_LOCK:
                        PENDING_PROPOSALS.pop(session_id, None)

                    return (
                        "保存取消：写入前发现原文件发生变化。"
                        "请重新生成提案。"
                    )

                os.replace(temp_path, target)
                temp_path = None

            with PROPOSAL_LOCK:
                PENDING_PROPOSALS.pop(session_id, None)

            # 人格文件修改成功后，立即刷新缓存
            if expected_filename == PERSONALITY_DOC:
                self.refresh_personality_cache()

            return (
                f"【{expected_filename} 修改成功】\n\n"
                "已按照用户确认的完整提案保存。\n"
                "其他项目文件没有被修改。"
            )

        except Exception as exc:
            return f"保存失败：{type(exc).__name__}"

        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    # ========================================================
    # 工具一：读取项目文档
    # ========================================================

    @filter.llm_tool(name="read_project_doc")
    async def read_project_doc(
        self,
        event: AstrMessageEvent,
        doc_name: str,
    ):
        """读取 md 目录中的一份项目文档。

        Args:
            doc_name(string): 文件名，例如 CLAUDE.md、ARCHITECTURE.md、
                DEVELOPMENT_STATUS.md、AI_HANDOFF.md。
        """

        content, error = read_allowed_doc(doc_name)

        if error:
            return error

        filename = ALLOWED_DOCS[doc_name.strip().lower()]

        return f"文档：{filename}\n\n{content}"

    # ========================================================
    # 工具二：搜索项目文档
    # ========================================================

    @filter.llm_tool(name="search_project_docs")
    async def search_project_docs(
        self,
        event: AstrMessageEvent,
        query: str,
    ):
        """在项目技术文档中搜索关键词。

        Args:
            query(string): 搜索关键词或短语。
        """

        filenames = [
            "CLAUDE.md",
            "ARCHITECTURE.md",
            "DEVELOPMENT_STATUS.md",
            "AI_HANDOFF.md",
        ]

        return self.search_documents(query, filenames)

    # ========================================================
    # 工具三：搜索长期记忆
    # ========================================================

    @filter.llm_tool(name="search_memory")
    async def search_memory(
        self,
        event: AstrMessageEvent,
        query: str,
    ):
        """按需搜索 MEMORY.md 中的长期记忆。

        当需要回忆用户偏好、共同经历、关系约定、
        自我认知或后续事项时使用。

        不要在每轮普通对话中无意义地调用。

        Args:
            query(string): 需要寻找的记忆关键词或短语。
        """

        return self.search_documents(
            query,
            [MEMORY_DOC],
        )

    # ========================================================
    # 工具四：按章节读取记忆
    # ========================================================

    @filter.llm_tool(name="read_memory")
    async def read_memory(
        self,
        event: AstrMessageEvent,
        section: str,
    ):
        """读取 MEMORY.md 中指定标题下的一个章节。

        不要为了方便而默认读取整份记忆。

        Args:
            section(string): 章节标题，例如用户、共同经历、关系约定。
        """

        if not isinstance(section, str) or not section.strip():
            return "请提供需要读取的记忆章节标题。"

        if len(section) > 100:
            return "章节标题不能超过100个字符。"

        content, error = read_allowed_doc(MEMORY_DOC)

        if error:
            return error

        lines = content.splitlines()

        wanted = section.strip().casefold()

        start = None
        start_level = None

        for index, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)

            if not match:
                continue

            heading = match.group(2).strip().casefold()

            if heading == wanted:
                start = index
                start_level = len(match.group(1))
                break

        if start is None:
            return f"没有找到记忆章节：{section}"

        end = len(lines)

        for index in range(start + 1, len(lines)):
            match = re.match(r"^(#{1,6})\s+", lines[index])

            if match and len(match.group(1)) <= start_level:
                end = index
                break

        result = "\n".join(lines[start:end]).strip()

        return (
            f"MEMORY.md · {section}\n\n"
            f"{result[:MAX_READ_CHARS]}"
        )

    # ========================================================
    # 工具五：提出记忆修改
    # ========================================================

    @filter.llm_tool(name="propose_memory_update")
    async def propose_memory_update(
        self,
        event: AstrMessageEvent,
        reason: str,
        new_content: str,
    ):
        """提出 MEMORY.md 修改方案，不会直接写入文件。

        必须提供完整的新 MEMORY.md 内容。
        只有用户明确确认后才会保存。

        Args:
            reason(string): 修改原因。
            new_content(string): 完整的新 MEMORY.md 内容。
        """

        return await self.create_proposal(
            event,
            MEMORY_DOC,
            reason,
            new_content,
        )

    # ========================================================
    # 工具六：确认记忆修改
    # ========================================================

    @filter.llm_tool(name="confirm_memory_update")
    async def confirm_memory_update(
        self,
        event: AstrMessageEvent,
    ):
        """只有用户当前消息精确等于“确认修改记忆”才允许保存。"""

        return await self.confirm_proposal(
            event,
            MEMORY_DOC,
            "确认修改记忆",
        )

    # ========================================================
    # 工具七：提出人格修改
    # ========================================================

    @filter.llm_tool(name="propose_personality_update")
    async def propose_personality_update(
        self,
        event: AstrMessageEvent,
        reason: str,
        new_content: str,
    ):
        """提出 PERSONALITY.md 修改方案，不会直接写入。

        Args:
            reason(string): 修改原因。
            new_content(string): 完整的新 PERSONALITY.md 内容。
        """

        return await self.create_proposal(
            event,
            PERSONALITY_DOC,
            reason,
            new_content,
        )

    # ========================================================
    # 工具八：确认人格修改
    # ========================================================

    @filter.llm_tool(name="confirm_personality_update")
    async def confirm_personality_update(
        self,
        event: AstrMessageEvent,
    ):
        """只有用户当前消息精确等于“确认修改人格”才允许保存。"""

        return await self.confirm_proposal(
            event,
            PERSONALITY_DOC,
            "确认修改人格",
        )


# ============================================================
# 注册插件
# ============================================================

@register(
    "astrbot_plugin_project_knowledge",
    "Local",
    "游戏项目文档检索与受控人格、记忆管理",
    "1.2.0",
)
