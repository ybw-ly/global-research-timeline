# 经纬研究 · Global Research Timeline

一个独立的、本地优先的**公开研究报告 / 论文元数据时间线**。

它从公开 API 抓取标题、摘要、作者、机构、发布日期、DOI / 报告编号和原文链接，按时间展示，支持搜索和筛选。

> 第一阶段只做**元数据与摘要**，不下载、不解析全文 PDF，也不做全文再分发。

- 页面：`http://127.0.0.1:8770`
- 数据：`data/research.db`（SQLite）
- 源配置：`config/research.json`
- 运行环境：Python 3.10+，只用标准库，无需 pip 安装

---

## 已接入的来源

| 来源 | 适配器 | 内容 |
|---|---|---|
| arXiv | `arxiv` | AI / ML / NLP 论文 |
| OpenAlex | `openalex` | 公开论文元数据、机构、摘要 |
| PubMed | `pubmed` | 生物医学 / 长寿 / 健康老龄化论文 |
| US Federal Register | `federal_register` | 美国政府公开文件与报告 |
| Crossref | `crossref` | 期刊论文元数据（适配器已实现，默认关闭） |

Crossref 的公共 IP 经常被限流（HTTP 429）。在 `config/research.json` 的 `settings.contact_email` 填入联系邮箱后可尝试启用，或直接使用 OpenAlex。

---

## 快速开始

```bash
cd global-research-timeline
python3 server/app.py
```

Windows：

```bat
run.bat
```

打开 <http://127.0.0.1:8770>。

首次启动后后台会自动抓取一轮；也可以点页面右上角 **“刷新元数据”**。

### Docker

```bash
docker compose up -d --build
```

打开 <http://127.0.0.1:8770>。数据保存在 `./data/research.db`。

---

## 目录结构

```text
global-research-timeline/
├── server/
│   ├── app.py          # 独立 HTTP 服务与 API
│   ├── research.py     # 源适配器、抓取、入库、查询
│   ├── db.py           # SQLite schema
│   └── util.py         # HTTP / 文本 / 日期工具
├── web/
│   ├── research.html
│   ├── research.js
│   └── research.css
├── config/
│   └── research.json   # 源与刷新配置
├── tests/
│   └── test_research.py
├── data/               # 运行时生成，已 gitignore
├── Dockerfile
└── docker-compose.yml
```

---

## 配置

`config/research.json`：

```json
{
  "settings": {
    "request_timeout": 30,
    "user_agent": "GlobalResearchTimeline/1.0",
    "contact_email": "",
    "default_refresh_minutes": 360,
    "max_items_per_source": 20
  },
  "sources": [
    {
      "id": "arxiv-ai",
      "name": "arXiv · AI/ML",
      "adapter": "arxiv",
      "query": "cat:cs.AI OR cat:cs.LG OR cat:cs.CL",
      "doc_type": "paper",
      "days": 30,
      "refresh_minutes": 360,
      "max_items": 20,
      "enabled": true
    }
  ]
}
```

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` | `127.0.0.1` | 监听地址 |
| `PORT` | `8770` | 端口；被占用时自动尝试 8771–8789 |
| `RESEARCH_DB` | `data/research.db` | SQLite 路径 |
| `RESEARCH_CONFIG_PATH` | `config/research.json` | 源配置路径 |
| `RESEARCH_NO_FETCH` | 空 | 设为 `1` 关闭后台抓取 |
| `ADMIN_TOKEN` | 空 | 设置后 `POST /api/research/refresh` 需 `X-Admin-Token`；不设置仅允许本机回环调用 |

---

## API

```text
GET  /api/health
GET  /api/research?q=&source=&doc_type=&days=90&limit=50&offset=0
GET  /api/research/sources
POST /api/research/refresh  {"force": true}
```

示例：

```bash
curl 'http://127.0.0.1:8770/api/research?q=climate&doc_type=paper&days=90'
curl 'http://127.0.0.1:8770/api/research/sources'
curl -X POST 'http://127.0.0.1:8770/api/research/refresh' \
  -H 'Content-Type: application/json' -d '{"force":true}'
```

---

## 数据字段

`research_items` 存储：

- `title`、`abstract`
- `authors`、`institutions`（JSON 数组）
- `published_at`
- `doi`、`report_no`
- `url`、`pdf_url`
- `doc_type`（`paper` / `report`）
- `source_id`、`source_name`、`publisher`
- `language`、`country`、`topics`
- `fetched_at`、`fingerprint`

跨源去重优先按 DOI；没有 DOI 的按来源 + 原文 URL/标题生成稳定 ID。

---

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖 arXiv / Crossref / OpenAlex / PubMed / Federal Register 的标准化，以及数据库入库、DOI 去重、筛选和统计；不访问网络。

---

## 边界与合规

- 只抓公开 API / 公开元数据，不绕过登录、付费墙、验证码或反爬限制。
- 默认只监听 `127.0.0.1`；对外暴露时请在反向代理层加认证。
- 摘要和元数据版权归原作者/出版方；页面始终链接原文，不做全文再分发。
- `pdf_url` 只是原文提供的 PDF 链接，本工具不下载、不转存全文。
- 公开 API 可能限流、字段变更或暂时不可用；单个源失败不会阻塞其他源。

## 路线图

- 第二阶段：开放获取 PDF 的文本提取（可选、按源启用）
- 第三阶段：独立的全文索引 / 检索，不与新闻时间线混合
- 更多政府/智库报告源：World Bank、IMF、OECD、WHO、gov.uk 等

## License

MIT
