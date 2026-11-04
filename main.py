
from pathlib import Path
import re

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


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

MAX_FILE_SIZE = 512 * 1024
MAX_READ_CHARS = 18000
MAX_SEARCH_RESULTS = 8


def read_allowed_doc(doc_name: str):
    """只读取白名单中的文件，返回内容和错误信息。"""

    key = doc_name.strip().lower()

    if key not in ALLOWED_DOCS:
        return None, (
            "不允许读取这个文件。"
            "可选文件：CLAUDE.md、ARCHITECTURE.md、"
            "DEVELOPMENT_STATUS.md、AI_HANDOFF.md、PERSONALITY.md"
        )

    filename = ALLOWED_DOCS[key]
    target = PROJECT_ROOT / filename

    try:
        root = PROJECT_ROOT.resolve(strict=True)

        # 不允许白名单文件本身是符号链接
        if target.is_symlink():
            return None, "安全检查失败：目标文件不能是符号链接。"

        resolved = target.resolve(strict=True)

        # 确保最终路径仍是项目根目录下的指定文件
        if resolved.parent != root or resolved.name != filename:
            return None, "安全检查失败：文件路径不符合白名单。"

        if not resolved.is_file():
            return None, "指定文档不存在。"

        if resolved.stat().st_size > MAX_FILE_SIZE:
            return None, "文件超过允许读取的大小限制。"

        content = resolved.read_text(encoding="utf-8")

        return content[:MAX_READ_CHARS], None

    except FileNotFoundError:
        return None, f"找不到文档：{filename}"

    except UnicodeDecodeError:
        return None, f"文档编码不是 UTF-8：{filename}"

    except Exception as exc:
        return None, f"读取文档失败：{type(exc).__name__}"


@register(
    "astrbot_plugin_project_knowledge",
    "Local",
    "游戏项目 Markdown 文档只读检索",
    "1.0.0",
)
class ProjectKnowledgePlugin(Star):

    def __init__(self, context: Context):
        super().__init__(context)

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
            yield event.plain_result(error)
            return

        filename = ALLOWED_DOCS[doc_name.strip().lower()]

        yield event.plain_result(
            f"文档：{filename}\n\n{content}"
        )

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

        query = query.strip()

        if not query:
            yield event.plain_result("搜索关键词不能为空。")
            return

        if len(query) > 100:
            yield event.plain_result("搜索关键词不能超过100个字符。")
            return

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
            yield event.plain_result(
                f"在五份项目文档中没有找到：{query}"
            )
            return

        results.sort(key=lambda item: item[0], reverse=True)

        output = [f"项目文档搜索：{query}\n"]

        for score, filename, line_number, excerpt in (
            results[:MAX_SEARCH_RESULTS]
        ):
            output.append(
                f"\n【{filename} · 第 {line_number} 行】\n"
                f"{excerpt}"
            )

        response = "\n".join(output)

        yield event.plain_result(response[:12000])
