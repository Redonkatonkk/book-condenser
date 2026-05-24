# 书籍浓缩器项目规划

## 目标

构建一个可在 Docker 中运行的网页应用，通过 MiniMax 大模型把 EPUB、PDF、TXT 书籍按章节浓缩，并导出完整 EPUB 成品。应用监听 `9121` 端口。

## 核心流程

1. 用户上传 EPUB、PDF 或 TXT，并选择 MiniMax 模型版本。
2. 后端保存原始文件，解析章节结构，统计每章字数，生成完整性报告。
3. 后端按章节并发调用 MiniMax，将每章单独浓缩，保留原书章节结构。
4. 前端持续轮询任务状态，显示总体进度、预计剩余时间、每章原字数、每章浓缩进度和浓缩后字数。
5. 完成后自动进入预览视图；预览一次只加载一章内容。
6. 用户点击下载按钮，下载完整 EPUB 浓缩版。

## 技术方案

- 后端：FastAPI
- 前端：原生 HTML/CSS/JavaScript，由 FastAPI 静态托管
- 书籍解析：
  - EPUB：`ebooklib` + `BeautifulSoup`
  - PDF：`pypdf`
  - TXT：`charset-normalizer`
- EPUB 输出：`ebooklib`
- 并发：`ThreadPoolExecutor`
- 运行方式：单容器 Docker，`0.0.0.0:9121`

## MiniMax 集成

- 默认模型：`MiniMax-M2.7`
- 可选模型：
  - `MiniMax-M2.7`
  - `MiniMax-M2.7-highspeed`
  - `MiniMax-M2.5`
  - `MiniMax-M2.5-highspeed`
  - `MiniMax-M2.1`
  - `MiniMax-M2.1-highspeed`
  - `MiniMax-M2`
- API 地址：`https://api.minimax.io/v1/chat/completions`
- API key 由服务端环境变量 `MINIMAX_API_KEY` 提供，前端不显示也不要求用户填写。

## 质量要求

- 文件格式校验和错误提示清晰。
- 解析结果包含章节数量、总字数和完整性警告。
- 浓缩任务失败时保留已完成章节状态，并在界面显示错误。
- 下载文件必须是有效 EPUB。
- 自动化测试覆盖章节拆分、TXT/EPUB/PDF 解析、EPUB 导出、API 上传和任务完成流程。

## 执行步骤

1. 建立工程骨架、依赖和 Docker 配置。
2. 实现解析器、完整性分析、MiniMax 客户端、任务管理器和 EPUB 写入器。
3. 实现网页上传、进度表、预览和下载。
4. 编写测试和样例文件。
5. 运行测试、Docker 构建、容器启动和端到端接口验证。
