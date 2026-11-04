# 项目知识库（只读）

AstrBot 本地只读项目知识库插件。

## 功能

为 AI 助手提供两个工具：

- `search_project_docs`：在指定的五份项目 Markdown 文档中搜索关键词。
- `read_project_doc`：读取指定的项目 Markdown 文档。

## 允许访问的文件

- `CLAUDE.md`
- `ARCHITECTURE.md`
- `DEVELOPMENT_STATUS.md`
- `AI_HANDOFF.md`
- `PERSONALITY.md`

## 安全限制

- 仅允许读取上述五份文件。
- 不提供文件写入、删除功能。
- 不提供命令执行功能。
- 不允许模型自行指定任意文件路径。

## 安装要求

AstrBot >= 4.29

## 配置

项目目录在插件 `main.py` 中固定配置，需要根据实际环境修改 `PROJECT_ROOT`。
