# OpenAlex API 契约

本说明对应 2026-07；发布前重新核对官方文档：

- Authentication: https://developers.openalex.org/api-reference/authentication
- Deprecations: https://developers.openalex.org/guides/deprecations
- List works: https://developers.openalex.org/api-reference/works/list-works
- Paging: https://developers.openalex.org/guides/page-through-results

## 认证与安全

通过 query string 的 `api_key` 认证。融合版读取顺序为：CLI 单次覆盖、环境变量 `OPENALEX_API_KEY`、融合版用户配置、`~/.config/openalex/api_key`。日常运行交互式 `fusion.py config`，不要把 key 写入命令历史。

配置保存在 `$XDG_CONFIG_HOME/yize-rd/bibliometric-fusion/openalex.json`；未设置 XDG 时使用 `~/.config/...`。先验证，再以临时文件、`fsync`、`chmod 0600`、`os.replace` 原子写入。任何异常文本必须对 `api_key` 脱敏。

## 当前请求规范

- 使用 `/topics` 和 `topics.id`，不使用已弃用 Concepts。
- 使用 `per_page`、`group_by`、`api_key` 等 snake_case 参数。
- `per_page` 不超过 100。
- 全量分页从 `cursor=*` 开始并读取 `meta.next_cursor`。
- `sample` 不与 `page` 或 `cursor` 混用；每批最多 100，使用不同固定 seed 并按 Work ID 去重。
- OpenAlex 已停止 mailto polite pool；CLI 仅保留 `mailto` 给 Crossref 使用。

## 规范聚合字段

| 指标 | `group_by` |
| --- | --- |
| 年度发文 | `publication_year` |
| 作者 | `authorships.author.id` |
| 机构 | `authorships.institutions.id` |
| 国家 | `authorships.institutions.country_code` |
| 来源 | `primary_location.source.id` |
| Primary Topic | `primary_topic.id` |
| 关键词 | `keywords.id` |

聚合请求不附加 `per_page`，避免无意截断返回的分组集合。

国家分组返回后必须按 `country_code` 归一化显示名；对缺失或异常代码再使用名称别名兜底。港澳台不得直接使用 OpenAlex 原始简称，统一交由融合层的中英文命名策略输出。

## 故障处理

- 401/403：停止并要求更新 key。
- 429：遵循 `Retry-After`，重试次数有上限。
- 5xx、超时和非法 JSON：有限指数退避；失败后明确记录到 `failed_sections`。
- 年度发文是核心维度，失败时不写新 bundle；可选维度失败时 bundle 标记 `partial=true`。
