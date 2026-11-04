from pathlib import Path
import hashlib
import os
import re
import tempfile
import threading

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


# ============================================================
# 基础配置
# ============================================================

# 固定项目目录，不允许模型修改或传入路径
PROJECT_ROOT = Path(
    r"E:\No Place Without You\no-place-without-you"
)

# 唯一允许读取的五份文件
ALLOWED_DOCS = {
    "claude.md": "CLAUDE.md",
    "architecture.md": "ARCHITECTURE.md",
    "development_status.md": "DEVELOPMENT_STATUS.md",
    "ai_handoff.md": "AI_HANDOFF.md",
    "personality.md": "PERSONALITY.md",
}

# 唯一允许写入的文件
WRITABLE_DOC = "PERSONALITY.md"

MAX_FILE_SIZE = 512 * 1024
MAX_READ_CHARS = 18000
MAX_SEARCH_RESULTS = 8
MAX_PROPOSAL_CHARS = 30000

# 待确认提案只保存在内存中。
# AstrBot 重启后，未确认的提案会自动失效。
PENDING_PROPOSALS = {}

# 防止多个操作同时修改待确认提案
PROPOSAL_LOCK = threading.Lock()


# ============================================================
# 文件安全检查
# ============================================================

def get_safe_project_root():
    """获取经过解析的项目根目录。"""

    try:
        return PROJECT_ROOT.resolve(strict=True)
    except Exception:
        return None


def get_safe_doc_path(filename: str):
    """
    获取白名单文件的安全路径。

    不允许：
    - 项目目录之外的路径
    - 符号链接
    - 非白名单文件
    """

    if filename not in ALLOWED_DOCS.values():
        return None, "文件不在允许访问的白名单中。"

    root = get_safe_project_root()

    if root is None:
        return None, "项目目录不存在或无法访问。"

    target = PROJECT_ROOT / filename

    try:
        if target.is_symlink():
            return None, "安全检查失败：目标文件不能是符号链接。"

        resolved = target.resolve(strict=True)

        if resolved.parent != root or resolved.name != filename:
            return None, "安全检查失败：文件路径不符合白名单。"

        if not resolved.is_file():
            return None, "指定路径不是文件。"

        return resolved, None

    except FileNotFoundError:
        return None, f"找不到文件：{filename}"

    except Exception as exc:
        return None, f"文件安全检查失败：{type(exc).__name__}"


def read_allowed_doc(doc_name: str):
    """只读取白名单中的文件，返回内容和错误信息。"""

    if not isinstance(doc_name, str):
        return None, "文件名必须是字符串。"

    key = doc_name.strip().lower()

    if key not in ALLOWED_DOCS:
        return None, (
            "不允许读取这个文件。"
            "可选文件：CLAUDE.md、ARCHITECTURE.md、"
            "DEVELOPMENT_STATUS.md、AI_HANDOFF.md、PERSONALITY.md"
        )

    filename = ALLOWED_DOCS[key]
    target, error = get_safe_doc_path(filename)

    if error:
        return None, error

    try:
        if target.stat().st_size > MAX_FILE_SIZE:
            return None, "文件超过允许读取的大小限制。"

        content = target.read_text(encoding="utf-8")

        return content[:MAX_READ_CHARS], None

    except UnicodeDecodeError:
        return None, f"文档编码不是 UTF-8：{filename}"

    except Exception as exc:
        return None, f"读取文档失败：{type(exc).__name__}"


def get_personality_file():
    """获取唯一允许写入的 PERSONALITY.md。"""

    return get_safe_doc_path(WRITABLE_DOC)


def calculate_sha256(data: bytes):
    """计算文件内容的 SHA256。"""

    return hashlib.sha256(data).hexdigest()


# ============================================================
# 插件主体
# ============================================================

@register(
    "astrbot_plugin_project_knowledge",
    "Local",
    "游戏项目文档检索与受控人格修改",
    "1.1.0",
)
class ProjectKnowledgePlugin(Star):

    def __init__(self, context: Context):
        super().__init__(context)

    # ========================================================
    # 工具一：读取项目文档
    # ========================================================

    @filter.llm_tool(name="read_project_doc")
    async def read_project_doc(
        self,
        event: AstrMessageEvent,
        doc_name: str,
    ):
        """读取游戏项目中的一份指定 Markdown 文档。

        Args:
            doc_name(string): 文件名，只能是 CLAUDE.md、ARCHITECTURE.md、DEVELOPMENT_STATUS.md、AI_HANDOFF.md、PERSONALITY.md。
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
        """在游戏项目的五份白名单 Markdown 文档中搜索关键词。

        Args:
            query(string): 要搜索的关键词或短语。
        """

        if not isinstance(query, str):
            return "搜索关键词必须是字符串。"

        query = query.strip()

        if not query:
            return "搜索关键词不能为空。"

        if len(query) > 100:
            return "搜索关键词不能超过100个字符。"

        terms = [
            term.casefold()
            for term in re.split(r"[\s,，、;；]+", query)
            if term
        ]

        results = []

        for filename in ALLOWED_DOCS.values():

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

                excerpt = "\n".join(
                    lines[start:end]
                )

                results.append(
                    (score, filename, index + 1, excerpt)
                )

        if not results:
            return f"在五份项目文档中没有找到：{query}"

        results.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        output = [f"项目文档搜索：{query}\n"]

        for score, filename, line_number, excerpt in (
            results[:MAX_SEARCH_RESULTS]
        ):
            output.append(
                f"\n【{filename} · 第 {line_number} 行】\n"
                f"{excerpt}"
            )

        response = "\n".join(output)

        return response[:12000]

    # ========================================================
    # 工具三：提出人格修改方案
    # ========================================================

    @filter.llm_tool(name="propose_personality_update")
    async def propose_personality_update(
        self,
        event: AstrMessageEvent,
        reason: str,
        new_content: str,
    ):
        """提出 PERSONALITY.md 修改方案。

        此工具绝对不会写入文件。

        助手应当在长期交流中发现值得调整的人格规则时，
        主动提出修改建议，并提供完整的新文件内容。

        工具会保存待确认提案，并返回完整提案供用户审阅。

        Args:
            reason(string): 为什么建议修改人格文件，以及希望解决什么问题。
            new_content(string): 完整的新 PERSONALITY.md 内容，不能只提供修改片段。
        """

        if not isinstance(reason, str) or not reason.strip():
            return "提案失败：必须提供修改理由。"

        if not isinstance(new_content, str) or not new_content.strip():
            return "提案失败：必须提供完整的新文件内容。"

        if len(new_content) > MAX_PROPOSAL_CHARS:
            return (
                f"提案失败：新内容超过 {MAX_PROPOSAL_CHARS} 个字符的限制。"
            )

        # 获取当前微信会话标识
        session_id = getattr(
            event,
            "unified_msg_origin",
            None,
        )

        if not session_id:
            return "提案失败：无法识别当前会话。"

        target, error = get_personality_file()

        if error:
            return f"提案失败：{error}"

        try:
            original_bytes = target.read_bytes()

            if len(original_bytes) > MAX_FILE_SIZE:
                return "提案失败：原人格文件超过大小限制。"

            original_content = original_bytes.decode("utf-8")

            original_hash = calculate_sha256(original_bytes)

        except Exception as exc:
            return f"提案失败：无法读取原文件（{type(exc).__name__}）。"

        # 不允许提交与当前文件完全相同的内容
        if new_content == original_content:
            return "提案取消：新内容与当前 PERSONALITY.md 完全相同。"

        proposal = {
            "reason": reason.strip(),
            "new_content": new_content,
            "original_hash": original_hash,
            "filename": WRITABLE_DOC,
        }

        with PROPOSAL_LOCK:
            PENDING_PROPOSALS[str(session_id)] = proposal

        return (
            "【PERSONALITY.md 修改提案已生成】\n\n"
            f"修改理由：\n{reason.strip()}\n\n"
            "以下是完整的新文件内容，请仔细审阅：\n\n"
            "==============================\n"
            f"{new_content}\n"
            "==============================\n\n"
            "当前文件尚未发生任何修改。\n"
            "只有当用户单独发送精确文本：\n"
            "确认修改人格\n"
            "才允许执行保存。\n"
            "用户也可以要求修改提案，或直接放弃。"
        )

    # ========================================================
    # 工具四：确认并保存人格修改
    # ========================================================

    @filter.llm_tool(name="confirm_personality_update")
    async def confirm_personality_update(
        self,
        event: AstrMessageEvent,
    ):
        """确认并保存已经提出的人格修改方案。

        只有当用户当前这条微信消息的完整文本严格等于
        “确认修改人格”时，才允许执行。

        不接受“好”“可以”“同意”等其他表达。
        不允许助手自行推断用户已经同意。

        必须先存在当前会话中尚未确认的人格修改提案。
        """

        # 必须读取用户当前发送的原始消息
        message = getattr(event, "message_str", "")

        if not isinstance(message, str):
            return "确认失败：无法读取用户当前消息。"

        if message.strip() != "确认修改人格":
            return (
                "确认失败：用户必须单独发送精确文本："
                "确认修改人格"
            )

        session_id = getattr(
            event,
            "unified_msg_origin",
            None,
        )

        if not session_id:
            return "确认失败：无法识别当前会话。"

        session_id = str(session_id)

        # 读取当前会话的待确认提案
        with PROPOSAL_LOCK:
            proposal = PENDING_PROPOSALS.get(session_id)

        if proposal is None:
            return (
                "确认失败：当前会话没有待确认的人格修改提案。"
                "请先重新提出修改方案。"
            )

        # 再次确认目标文件只能是 PERSONALITY.md
        if proposal.get("filename") != WRITABLE_DOC:
            return "安全检查失败：提案目标文件不合法。"

        target, error = get_personality_file()

        if error:
            return f"保存失败：{error}"

        try:
            # 读取当前文件，检查是否在提案之后被其他程序修改
            current_bytes = target.read_bytes()

            current_hash = calculate_sha256(current_bytes)

            if current_hash != proposal["original_hash"]:
                with PROPOSAL_LOCK:
                    PENDING_PROPOSALS.pop(session_id, None)

                return (
                    "保存已取消：PERSONALITY.md 在提案生成后"
                    "发生了变化。\n\n"
                    "为了避免覆盖你或 Claude Code 的新修改，"
                    "本次提案已经失效。\n"
                    "请重新读取人格文件并生成新提案。"
                )

            new_content = proposal["new_content"]

            if not isinstance(new_content, str):
                return "保存失败：提案内容格式不正确。"

            if not new_content.strip():
                return "保存失败：新文件内容不能为空。"

            new_bytes = new_content.encode("utf-8")

            if len(new_bytes) > MAX_FILE_SIZE:
                return "保存失败：新文件超过大小限制。"

            # 尽可能保留原文件的换行风格
            if b"\r\n" in current_bytes:
                new_content = new_content.replace(
                    "\r\n",
                    "\n",
                ).replace(
                    "\n",
                    "\r\n",
                )

                new_bytes = new_content.encode("utf-8")

            # 再次检查目标路径
            target_again, error = get_personality_file()

            if error:
                return f"保存失败：{error}"

            if target_again != target:
                return "保存失败：目标文件路径发生变化。"

            # 原子写入：先写入同目录临时文件，再替换原文件
            temp_path = None

            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=str(target.parent),
                    prefix=".personality_update_",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:

                    temp_path = Path(temp_file.name)

                    temp_file.write(new_bytes)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())

                # 替换前再检查一次原文件是否变化
                latest_bytes = target.read_bytes()

                if calculate_sha256(latest_bytes) != proposal["original_hash"]:
                    with PROPOSAL_LOCK:
                        PENDING_PROPOSALS.pop(session_id, None)

                    return (
                        "保存取消：原文件在保存过程中发生了变化。"
                        "请重新生成提案。"
                    )

                os.replace(temp_path, target)

                temp_path = None

            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            # 保存成功后清除待确认提案
            with PROPOSAL_LOCK:
                PENDING_PROPOSALS.pop(session_id, None)

            return (
                "【人格文件修改成功】\n\n"
                "PERSONALITY.md 已按照你确认的提案完成修改。\n"
                "其他四份项目文档没有被修改。\n"
                "没有访问或修改其他项目文件。"
            )

        except Exception as exc:
            return (
                f"保存失败：{type(exc).__name__}。\n"
                "原文件可能没有发生变化，请检查文件状态后重试。"
            )