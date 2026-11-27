# 改动登记簿

格式：`时间 | 谁 | 文件 | 改了什么 | 为什么`

**新条目加在最上面。** 动手前先看下面 20 条，别改对方正在改的文件。

---

### 11:35（11-27） | Claude Code | 生产发版 | `20261127-release-notice` 已上线 | 人要求 11:30 后发版

- **11:31 已部署 `rag-bff:20261127-release-notice`，health 200**；回滚点 `20261127-docs`。
- 生产核对：
  - `/api/release` 管理员/普通用户均返回 `20261127-release-notice`，8 条更新说明；
  - 页面弹「本次更新」，每个账号每版本一次；
  - 「⚡ 快速」按钮可见，演示模式菜单已移除；
  - 重建索引确认框正常，说明影响；
  - 完整手册 v3.1，10/10 截图加载；
  - 统计接口正常：今日 20 条提问、11 个活跃账号（核对时）。
- 分享 PDF：`dist/新人AI导师使用手册_2026-11-27.pdf`（24 页，本地文件，未发布到公网）。

---

### 11:25（11-27） | Claude Code | `app/static/manual.md`、`dist/新人AI导师使用手册_2026-11-27.pdf` | 手册 v3.1 + 分享 PDF | 人要求：转 PDF 发给别人

- 手册版本更新为 v3.1（2026-11-27），「本次更新」改为建库/创建人权限/快速模式/系统设置/统计反馈/重建确认。
- 新增「配套 PDF 版：可向管理员索取」；生成 A4 PDF（24 页，含封面、目录、10 张截图），
  路径 `dist/新人AI导师使用手册_2026-11-27.pdf`。
- 生产暂未部署；准备 11:30 后按发版流程上线。

---

### 02:05（11-27） | Claude Code | 问答统计/引导/快速模式/重建索引 | 统计口径修正、引导补建库、快速跳过重排、重建确认、移除演示模式 | 人要求（均未上生产）

- **问答统计修正**：`admin/overview` 的「今日问答」不再把反馈记录算成提问；今日平均耗时/活跃用户同步只用 ask；
  `qa_log.stats` 的差评率只统计本时间段内提问收到的反馈，worst 列表与分母一致；
  普通用户统计改为按 uid 过滤，不再混入全局反馈/耗时。
  本地构造 4 条提问 + 4 条反馈验证：全局 99 条/反馈 2 条、个人 2 条、今日 3 条均与 API 一致；测试日志已恢复。
- **建库引导**：首次引导增加「自己建库」和拖文件自动建库；快速上手新增「二、创建自己的知识库」；
  完整手册新增 4.1（新建/拖拽/索引/归属人权限）、5.1 引用；空对话区/入口卡片也更明确。
- **快速模式**：输入栏新增「⚡ 快速」按钮（默认关），跳过重排，回答更快；按钮/开关提示会说明
  “引用排序可能不够准，复杂问题建议关闭”；文档 5.6、快速上手、首次引导同步。
  后端 `AskIn.fast` 跳过主链路/手册定向重排，快速答案不写正常缓存；本地实测 sources
  从 3.07–12.05s 降到 0.27s，快照仍返回 5 条引用；前端请求体 `fast:true` 验证通过。
- **重建索引确认**：点击「重建索引」弹确认框，列明重建期间可能变慢/不完整、完成后才含最新资料、
  不删文件和权限、会占 CPU；取消不发请求，确认才调用。
- **移除演示模式**：菜单项/JS/localStorage 开关全部删除，保留「换个回答」强制重算；手册同步。
- **未部署生产**；只本地验证，无 JS 报错，手机端快速按钮不挤压输入框。

---

### 01:35（11-27） | Claude Code | `app/server.py`、`app/static/index.html`、`app/static/manual.md` | 知识库归属人可分配权限 + 显示创建人 | 人要求：个人上传的库自己有全部权限、能给别人赋权；每个库显示创建人，其他人可直接找人开通

- 后端 `/api/kb/{id}/share` 从「仅管理员」放宽为「知识库创建人或管理员」；
  新增登录可读的 `/api/share/users` 用户目录（不含管理员字段），供归属人分配时选择人员。
- `/api/kb` 对所有用户返回 `owner_name` 和 `owner_contact`（工号/邮箱），未授权库也显示创建人。
- 前端知识库行显示「👤 我创建的 / 👤 创建人：xxx（工号/邮箱）」；未授权行显示
  「🔒 未授权 · 创建人… · 找 TA 开通」，点击提示联系创建人；归属人的 ⋯ 菜单新增「📤 分配权限」。
- 分配权限弹窗对归属人只列自己的库；用户列表改用 `/api/share/users`，搜索框继续可用。
- 本地实测：归属人创建库→看到「我创建的」→⋯分配权限→勾选目标用户→保存成功；
  目标用户从「未授权」变为可读，显示创建人；非归属人调用分配接口返回 403；测试库已清理。
- **未部署生产**（按要求）。

---

### 01:20（11-27） | Claude Code | `app/static/index.html`、`app/static/manual.md`、`app/static/manual_img/ui-share.webp` | 赋权界面增加用户搜索 | 人要求：用户多时不好找，先不上生产

- 「分配知识库权限」弹窗在知识库选择下方新增搜索框：按**姓名 / 邮箱 / 工号 / 用户名**过滤。
- 显示「显示 X / Y 人」；无匹配时给出提示；切换知识库自动清空搜索。
- 过滤只影响当前列表，已勾选状态保留在内存集合里，保存时仍包含被过滤掉的已选用户。
- 手册 10.1 同步说明搜索；配图更新为带搜索框的赋权界面。
- 本地实测 3 个模拟用户：按姓名/邮箱过滤、勾选状态跨过滤保留、无匹配提示正常；
  手机端无横向溢出，无 JS 报错。
- **未部署生产**（按要求）。

---

### 01:10（11-27） | Claude Code | 手册/UI 文案/`system_settings.py`/`server.py` | 修正「大模型并发/个人 Key」错误描述 | 人反馈：现在主要不是大模型并发，外部大模型已解决；统一改为本地检索/重排瓶颈

- 手册第三章重写：默认使用管理员配置的全局模型；个人 Key 改为**可选**，
  明确它只影响自己的模型调用，不能改善本地检索/重排速度；3.4 报错表同步修正。
- 手册 FAQ、快速上手「六、常见问题」、首次引导、知识库/答辩提示、我的 Key 面板、
  topbar 徽标提示、清除 Key 确认、服务端 429/超时提示，统一不再把申请个人 Key 当解决方案。
- `system_settings.py` 增加旧默认提示迁移：只替换「服务器性能有限/回答速度会明显变慢」旧句，
  保留管理员自定义内容；生产旧存储提示会在下次部署后自动显示新口径。
- `release_notes.json` 增加一条「性能/提示文案修正」，供下次上线弹窗。
- 未部署生产；本地验证迁移、主页面提示、快速上手、答辩提示、我的 Key 面板均正确，无 JS 报错。

---

### 00:30（11-27） | Claude Code | `app/server.py`、`app/static/index.html`、`app/static/defense.html`、`app/release_notes.json`、`deploy/release_to_server.py` | 每次上线给所有用户弹更新说明 | 人要求：后续每次上生产都弹提示做了什么更新，目前不动生产

- 新接口 `GET /api/release` 读 `app/release_notes.json`（登录后返回）。
- 主页面和模拟答辩页都增加「🆕 本次更新」弹窗：
  - 每个账号每个版本只弹一次（localStorage 按用户 + 版本）；
  - 等首次引导/入口选择/进入提示/使用文档等弹窗关闭后再弹，不叠加；
  - 「知道了」或点遮罩关闭并记住该版本。
- `deploy/release_to_server.py` 发版前校验：`release_notes.json.version` 必须等于本次 `--tag`，
  items 不能为空；不一致直接中止，避免弹旧内容；紧急可用 `--skip-release-check`。
- 本次**未部署生产**（按要求）；下一次上线需把 `release_notes.json` 更新为对应 tag。
- 本地实测：主页面/答辩页、管理员/普通用户、手机端、关闭后刷新不再弹、无 JS 报错；
  版本不匹配时发版脚本直接拒绝（exit 2）。

---

### 00:09（11-27） | Claude Code | 文档/截图 + 测试记录 | 系统设置整合后全流程、10 人并发、排版 | 人要求：多测并发和所有界面，有问题直接修

- **全窗口冒烟**：0 issues；桌面 1280 / 平板 768 / 手机 375 / 1440 均无横向溢出；
  系统参数 5 个面板、普通用户只有我的 Key、旧弹窗已移除、无 JS 报错。
- **单人问答**：检索 5.5s、首字 5.8s、总 7.6s、回答 595 字。
- **10 人并发（10 个不同问题，真实网关，no_cache，thinking=false，global_max=10）**：
  10/10 HTTP 200，0 个 429，无排队事件；墙钟 152.3s；首字 p50 127.9s / p95 151.0s；
  总耗时 p50 136.9s / p95 152.3s。
- **A/B 测试 global_max=8**（临时改生产配置后已恢复 10）：
  10/10 HTTP 200，2 个请求排队，墙钟 182.6s；首字 p50 131.8s / p95 179.4s，反而更慢。
  结论：瓶颈是本地 CPU 重排串行，调低准入只会把等待变成排队；保留 10。
- 文档新增/更新截图：系统参数（含我的 Key/全局设置导航）、我的大模型 Key 面板；
  手册 3.3/10.4/附录入口路径已同步。
- 生产配置已确认恢复 `ask_limits.global_max=10`，active_total=0。
- **00:15 已部署 `rag-bff:20261127-docs`，healthy**：完整手册 10/10 图加载（含新系统参数、
  我的大模型 Key 截图）；系统设置整合后生产仍正常；最终单人问答 9.9s；
  `global_max=10`、active_total=0；回滚点 `20261126-sysllm`。

---

### 00:05（11-27） | Claude Code | `app/static/index.html`、`app/static/manual.md` | 「我的大模型 Key」「大模型全局设置」整合进系统参数设置 | 人要求

- 「⚙ 更多 → ⚙ 系统参数设置」现在所有用户可见；左侧导航：
  🔑 我的大模型 Key（所有用户）/ 🧠 大模型全局设置（管理员）/
  新账号默认知识库 / 新账号默认模拟答辩 / 进入提示（后三者管理员）。
- 管理员打开默认还是「新账号默认知识库」；普通用户打开默认「我的大模型 Key」，
  且不会请求管理员接口。
- 原来两个弹窗的内容在启动时搬进系统设置对应面板，旧弹窗移除；关闭按钮统一关闭系统设置。
- 系统默认区显示「保存」；我的 Key / 全局设置显示自己的保存按钮，底部改为「关闭」。
- 手册、首次引导、快速说明同步更新入口路径；共用的提示文案改为「系统参数设置 → 我的大模型 Key」。
- 本地实测：管理员 5 个导航项、切换/加载正常；普通用户只看到我的 Key；
  topbar 徽标可打开；旧弹窗不存在、无重复 DOM id、无 JS 报错、手机端无横向溢出。
- **00:05 已部署 `rag-bff:20261126-sysllm` 到 10.20.154.26:8000，healthy**：
  生产全窗口冒烟 0 issues；管理员 5 个面板、普通用户只有我的 Key、
  旧弹窗不存在、无 JS 报错；回滚点 `20261126-kb-dots-perm`。

---

### 23:38（11-26） | Claude Code | `app/static/index.html`、`app/static/manual.md` | 知识库操作收进「⋯」下拉菜单 | 人要求：左侧知识库按钮太多，缩减为三个点

- 每个知识库行只保留一个 **⋯** 按钮；点开固定定位下拉菜单：
  📂 查看文件 / ⚖️ 文件权重 / 📤 分配权限（仅管理员）/ ✎ 重命名 / ✕ 删除。
- 菜单用 fixed 定位并自动上下翻转，避开知识库列表滚动裁剪；点外部、Esc、滚动都关闭；
  同一时间只保留一个菜单；手机端 375px 内不越界。
- 未授权（置灰）知识库没有 ⋯；共享只读库的 ⋯ 只保留「查看文件」，
  不显示权重/重命名/删除；权重仍仅归属人/管理员可点。
- 本地实测：管理员菜单 5 项、190×206、Esc/外部点击关闭、点权重打开 41 个文件；
  手机菜单在 375×800 视口内；无 JS 报错。手册/快速上手入口文案改为「⋯ → ⚖️ 文件权重」。
- **23:45 已部署 `rag-bff:20261126-kb-dots-perm`，healthy**：生产管理员 9 个 ⋯、
  菜单 5 项、文件权重 47 个；共享只读业务手册的 ⋯ 只有「查看文件」；
  归属人菜单 4 项（无分配权限）；置灰库无 ⋯；无 JS 报错。回滚点 `20261126-kb-dots`。

---

### 22:20（11-26） | Claude Code | `app/static/index.html`、`app/static/manual.md` | 文件权重入口更明显 | 人反馈“文件权重设置的地方没看到”

- 左侧知识库列表里，**每个有权限的库名右侧新增 ⚖️ 按钮**（归属人/管理员可见）；
  点它直接打开该库的文件权重，不用先去「更多」找。
- 「⚙ 更多 → ⚖️ 文件权重」在管理员未选库时也显示，文案提示“请先选知识库”；
  选中库后文案带上库名，避免不知道在设哪个库。
- 修复 `window.__isAdmin` 在 `syncKbWeightsItem()` 之后才赋值，导致管理员没选库时入口被误隐藏。
- 无权限/共享只读用户仍不显示入口；点入口前也有权限校验和提示。
- 本地实测：管理员未选库可见并提示；选中业务手册后行内 ⚖️ 打开 41 行、文件名 674px；
  共享用户不可见；手机端 4 个管理按钮无横向溢出。
- 手册新增 7.5 文件权重说明，快速上手加了一行入口提示。
- **22:25 已部署 `rag-bff:20261126-weights-entry`，healthy**：生产管理员未选库时入口显示
  “⚖️ 文件权重（请先选知识库）”；选中业务手册后文案带库名，行内 ⚖️ 打开 47 个文件、
  文件名宽 674px；共享只读用户在该库不显示入口；无 JS 报错。回滚点 `20261126-hub`。

---

### 22:12（11-26） | Claude Code | 测试记录（无代码改动） | 10 人并发 + 单人问答 + 全窗口冒烟 | 人要求：并发测试主要功能、单人测所有窗口/排版，发现问题直接修

- **10 人并发**（10 个不同账号、各 1 路、真实网关、no_cache、thinking=false、业务手册）：
  **10/10 HTTP 200，0 个 429，0 个排队事件**；墙钟 51.2s；
  首字 p50 **30.9s** / p95 **49.2s**；总耗时 p50 31.7s / p95 51.1s；
  结束后 active_total=0、global_free=10。
- **单人**同问题：检索 4.8s、首字 5.2s、总 6.9s、回答 738 字。
- **全窗口冒烟**：登录页、首次引导、知识库进入提示、完整手册 9/9 截图、文件权重
  （文件名宽 674px 正常）、统计与反馈四个标签、系统参数、用户管理、我的 Key、
  大模型设置、写内容、文件列表、分享、普通用户仅提交反馈、手机端输入框 355px、
  答辩开屏/勾选/反馈；无重复 DOM id、无 JS 报错、桌面/手机无横向溢出。
- 本轮测试中修复：文件权重弹窗文件名被 select 的 width:100% 挤成 0 宽；
  PPT 预览表头 sticky top:52px 偏移。

---

### 22:00（11-26） | Claude Code | `app/static/index.html`、`app/static/manual.md` | 合并为「统计与反馈」中心 + 列表分页 | 人要求：问答统计/用户反馈/测试集候选/提交反馈整合、分页、提升易用性

- 菜单只保留一个入口：管理员「📊 统计与反馈」，普通用户「💬 反馈 / 提需求」。
- 新弹窗内含四个标签：📈 问答统计 / 📝 用户反馈 / 📋 测试集候选 / 💬 提交反馈；
  普通用户只看到「提交反馈」。
- 原来的四个弹窗内容在启动时移入中心（不复制 DOM，避免重复 ID），旧弹窗移除；
  关闭旧弹窗的按钮统一关闭中心。
- 用户反馈、测试集候选列表按 12 条/页分页，带上一页/下一页和总数；切换筛选重置到第 1 页。
- 本地用 30 条反馈 + 30 条候选验证：各 3 页、翻页正常；普通用户提交后自动关闭；
  页脚关闭按钮有效；无重复 ID、无 JS 报错；375px 无横向溢出。
- 手册同步说明管理员/普通用户的入口和分页。
- **22:03 已部署 `rag-bff:20261126-hub` 到 10.20.154.26:8000，healthy**：
  生产全窗口冒烟通过（登录、首次引导、进入提示、完整手册 9/9 图、文件权重文件名、
  Hub 四个标签、系统参数、用户管理、我的 Key、大模型设置、写内容、文件列表、分享、
  普通用户仅提交反馈、手机端无横向溢出、答辩开屏/反馈、无重复 ID、无 JS 报错）。
  回滚点 `20261126-weights-name-placeholder`。

---

### 21:52（11-26） | Claude Code | `app/static/index.html` | 知识库输入框提示写清问题 | 人要求：默认灰字提醒把问题描述清楚，答案质量差别很大

- 初始 placeholder：写清系统/模块、环境、目标，越具体回答越准（Enter 发送，可 Ctrl+V 贴图）。
- 选中知识库后的动态 placeholder 同步加“写清系统/模块、环境、目标，越具体回答越准”；
  手机端简化为“越具体回答越准”。
- textarea 增加 title 提示：哪个系统/模块、哪个环境、目标、已试过什么。
- 本地实测选中业务手册后 placeholder/title 正常，无 JS 报错。

---

### 21:48（11-26） | Claude Code | `app/static/index.html` | 修复文件权重弹窗只看到下拉、看不到文件名 | 人反馈打开后不知道在设置哪个文件

- 根因：`.modal select { width:100% }` 让每个权重下拉占满整行，`.wname` 被挤成 0 宽。
- 给 `.wrow .wsel` 加 `width:auto; min-width:110px; max-width:150px`；
  文件名恢复显示（实测 674px，下拉 110px）。
- 本地实测 41 行文件名正常，无 JS 报错。

---

### 21:35（11-26） | Claude Code | `app/static/index.html` | 点踩反馈框增加资料质量提示 | 人要求：回答不好时提示可能是上传资料质量不高

- 👎 弹出输入框顶部增加灰字提示：
  “回答不好也可能是上传的资料不全、版本旧或质量不高；可以补充/更新资料并重建索引后再问。”
- 本地注入 fbbox 实测提示和 textarea 都正常显示，无 JS 报错。
- **21:42 已部署 `rag-bff:20261126-feedback-hint` 到 10.20.154.26:8000，healthy**：
  生产点踩框显示资料质量提示和输入框；无 JS 报错。回滚点 `20261126-kb-weights`。

---

### 21:28（11-26） | Claude Code | `app/kb_weights.py`、`app/kb.py`、`app/server.py`、`app/static/index.html` | 每个知识库可配置文件检索权重 | 人要求：默认都一样，用户设置自己的库，管理员可设所有库

- 新增 `app/kb_weights.py`：`<kb_dir>/retrieval_settings.json` 存 `{文件相对路径: 权重}`，
  默认全部 1.0、只存非 1.0 项；权重范围 0.5～2.0；重建索引不丢。
- 新增 API：`GET/POST /api/kb/{id}/weights`；只有知识库归属人或管理员可读写
  （共享只读用户 403），管理员可设所有库。
- 生效位置：
  - BM25 召回打分乘权重（让高权重文件进入候选）；
  - 重排后对 `rerank_score` 做 `+log(weight)`（对负分也友好）；
  - 多路检索 `_multi_search` 的最终重排结果再补一次 `kb.apply_file_weights`。
- UI：知识库选中后「⚙ 更多 → ⚖️ 文件权重」，列出文件，可选
  0.7 降权 / 1.0 普通 / 1.3 优先 / 1.6 强优先，支持搜索和一键恢复默认；
  共享只读用户看不到入口。
- 本地验证：库归属人 etf2.0 可读写（13 文件）；共享用户 GET/POST 均 403；
  管理员可读业务手册（41 文件）；BM25 `b.pdf` 权重 2.0 后从第 2 升第 1；
  重排分 1.0 + log(1.6) 后反超 1.2；UI 保存/回读成功、无 JS 报错。
- 默认权重为空，现有问答行为不变；待生产发版。
- **21:34 已部署 `rag-bff:20261126-kb-weights` 到 10.20.154.26:8000，healthy**：
  管理员业务手册权重入口可见、47 个文件正常列出；PPT 预览表头恢复 static，
  `th` 底边与首行 `td` 顶边对齐；无 JS 报错。回滚点 `20261126-defense-optout`。

---

### 21:25（11-26） | Claude Code | `app/server.py` | 修复 PPT 预览表格表头偏移 | 引用点开的 PPT 表格表头位置错位

- 预览页通用 `th` 原来是 `position:sticky; top:52px`；表格在 `.tw` 横向滚动容器里，
  这个 52px 不是相对表头容器，导致表头下沉/与表体错位。
- 去掉 sticky（改为普通表头），`th` 底边与第一行 `td` 顶边对齐。
- 实测「投资系统日志体系和周边工具.pptx」预览：th position=static、top=auto，
  thBottom=tdTop=7171.17px；表头与表体对齐。

---

### 21:12（11-26） | Claude Code | `app/static/defense.html` | 答辩开屏提示增加「不再显示这条提示」勾选框 | 人反馈开屏提示没有“下次不再提醒”选项

- 提示弹窗底部新增勾选框；勾选后点「我知道了」/「去反馈」都会记到
  `localStorage.rag.betaNoMore.v1.<uid>`，之后进入答辩页不再弹。
- 未勾选则每次进入仍提示；管理员仍可在「系统参数设置 → 进入提示」关闭或改文案。
- 本地实测：勾选关闭后刷新不再弹；localStorage key 正确；无 JS 报错。

---

### 20:38（11-26） | Claude Code | `app/static/manual_img/`、`app/static/manual.md` | 手册截图改为 WebP 压缩 | 手册图片过大，完整手册要等很久才全部显示

- 9 张界面截图从 PNG（约 3.6MB）压缩为 WebP（约 568KB，最大宽 1100px）；
- `manual.md` 图片路径同步改为 `.webp`；
- 本地实测：打开「完整手册」1.2s 内 9/9 加载，点击可放大。
- **20:40 已部署 `rag-bff:20261126-manual-webp` 到 10.20.154.26:8000，healthy**：
  生产完整手册 9/9 截图在 1.6s 内加载，点击可放大；无 JS 报错。
  回滚点 `20261126-final-ui`。

---

### 20:30（11-26） | Claude Code | `app/static/index.html`、`app/static/manual.md`、`app/static/manual_img/` | 手册截图、快速上手/项目背景重写、输入栏排版、演示模式仅管理员、进入提示并入系统参数 | 人反馈手册图片不显示、文档太旧、三个开关挤输入框、演示模式权限、设置分散

- 完整手册：新增 9 张界面截图（`/static/manual_img/`，来自 training 资产），
  `md()` 支持 `![alt](/static/manual_img/...)` 渲染、点击放大；截图下标注
  “界面示意，以当前版本为准”。本地验证 9/9 加载、点击可放大。
- 「快速上手」重写：登录/入口、知识库选库提问、图片粘贴/拖拽、深度思考、
  模拟答辩、补充内容/反馈、常见问题。
- 「项目背景与说明」重写：新人AI导师定位（知识库=快速上手；模拟答辩=AI 导师提问评价、
  自检、释放导师精力）、痛点、2026-11 能力、架构简览。
- 输入栏：上下文/标准/深度思考/新会话移到输入框上方的工具行，输入框独占一行；
  桌面输入框约 862px、手机约 355px，无横向溢出。
- 演示模式：仅管理员可见；非管理员即使本地存了开关也不生效（`no_cache` 判断加 admin）。
- 进入提示并入「系统参数设置」第三项；删除单独的「📢 进入提示」菜单和弹窗；
  系统参数保存时同时保存注册默认值 + 进入提示，左侧摘要显示提示开关状态。
- 手册里「更多 → 📢 进入提示」统一改为「更多 → ⚙ 系统参数设置 → 进入提示」。
- 本地验证：系统参数第三项可加载/保存提示、API 回读正确；演示模式普通用户不可见、
  管理员可见；快速上手/背景含新功能；无 JS 报错。

---

### 19:54（11-26） | Claude Code | `app/server.py`、`app/static/index.html`、`app/static/defense.html`、`app/static/manual.md` | 深度思考按钮、答辩开屏、图片粘贴/拖拽、完整使用手册、首次引导、置灰排序 | 人交办的 7 项 UI/交互改动

- 「🧠 思考」按钮文字改为「🧠 深度思考」（答辩手机端显示「🧠深关/开」）。
- 答辩开屏提示改为**每次进入都检查**：只要 `/api/me` 成功、管理员未关闭，就显示；
  不再按标签页 sessionStorage 只弹一次。手机端顶栏「返回/反馈」自动缩短，消除溢出。
- 知识库提问：
  - 删除图片按钮和隐藏 file input；
  - `Ctrl+V` 粘贴图片到输入框即可附到本轮问题（原有 paste 逻辑保留）；
  - 图片可直接拖到输入框/预览条；纯图片拖拽不再触发“上传到知识库”的全屏遮罩，
    拖到页面其它位置也按本轮提问处理；
  - 输入提示同步说明粘贴/拖拽。
- 「使用文档」新增「完整手册」标签：`GET /api/manual` 读取
  `app/static/manual.md`（由原操作手册更新：改名、深度思考、图片提问、置灰库、反馈等），
  前端用 `md()` 渲染；原有「快速上手」「项目背景」保留。
- 首次使用引导：每个账号在本浏览器首次进入时弹「欢迎使用新人AI导师」，
  可直接「查看使用文档」（打开完整手册）或「先开始使用」；关闭后记 localStorage
  不再弹。注册后不再额外叠旧的三步横幅。
- 知识库列表排序：有权限的在上、未授权置灰的在下（组内保持原顺序）。
- 答辩入口按钮改回「🎓 模拟答辩」；点进去后的答辩页左上角仍为「🧑‍🏫 新人AI导师」。
- 本地验证：首次引导弹出、完整手册渲染（约 7,900 字）；粘贴+拖拽各加 1 张图；
  普通用户列表「业务手册」在上、3 个置灰在下；答辩刷新两次都弹提示；375px 顶栏
  无溢出；无 JS 报错。
- **上传速度实测**（本机 → 生产 10.20.154.26，20MB 单文件）：建库 0.22s、
  上传 6.31s ≈ **3.17 MB/s**、删除临时库 0.18s；上传后已清理，未留测试库。
- **19:57 已部署 `rag-bff:20261126-manual-onboard` 到 10.20.154.26:8000，healthy**：
  首次引导弹出、完整手册 7,917 字渲染；粘贴图片成功；有权限库在上、5 个置灰库在下；
  答辩刷新后仍弹提示；手机端顶栏无溢出；无 JS 报错。回滚点 `20261126-brand-locked`。

---

### 19:44（11-26） | Claude Code | `app/server.py`、`app/static/index.html`、`app/static/defense.html` | 品牌改为「新人AI导师」；无权限知识库可见但置灰 | 人要求改名、调整入口文案，并让没权限的用户能看到知识库但不可用

- 项目名/登录标题/浏览器标题/侧栏标题/答辩页标题/答辩入口统一为「新人AI导师」；
  - 知识库侧栏小字：帮助新人了解项目情况，助力快速上手
  - 模拟答辩小字：基于学习的关键字，AI模拟导师提问并评价；帮助新人自检学习效果，
    助力查漏补缺，释放导师精力
  - 首页入口卡片同步这两组文案。
- `/api/kb` 对普通用户也返回全部知识库，新增 `can_read` 字段；无权限的：
  - 侧栏显示 🔒、置灰、隐藏文件/改名/删除/分享按钮；
  - 点击提示"该知识库未授权，请联系管理员开通"，不会选中；
  - 后端接口仍按原权限拦截，不能越权读写。
- 答辩「出题依据的知识库」下拉、关键词直练下拉：未授权库以 disabled 选项显示并标
  「（未授权）」；默认选中第一个可读库。
- 本地验证：普通用户 4 个库、3 个置灰，点击弹提示且不切换；管理员 0 个置灰；
  关键词下拉未授权项 disabled；答辩页 375px 顶栏无溢出；无 JS 报错。
- **19:46 已部署 `rag-bff:20261126-brand-locked` 到 10.20.154.26:8000，healthy**：
  普通用户 `xiehy58148` 看到 9 个库、5 个置灰；浏览器标题/登录/侧栏/答辩页均为
  「新人AI导师」；答辩关键词下拉未授权项 disabled；375px 顶栏无溢出；无 JS 报错。
  回滚点 `20261126-notice-edit`。

---

### 17:31（11-26） | Claude Code | `app/system_settings.py`、`app/server.py`、`app/static/index.html`、`app/static/defense.html` | 进入界面的提醒文案改为管理员可编辑（知识库 + 模拟答辩） | 人要求"进入界面的提醒里面可以修改内容"

- `system_settings` 新增 `notice.kb` / `notice.defense`：`enabled / title / body`，
  存 `app/data/system_settings.json`；`save()` 改为"只覆盖传入的段落"，
  保存注册默认值时不会清掉提示文案，反之亦然。
- 新增 `GET /api/notice`（登录用户可读）与 `POST /api/notice`（管理员保存）。
- 知识库管理员「⚙ 更多 → 📢 进入提示」：可分别编辑知识库/模拟答辩的启用、
  标题、正文（支持换行）；知识库和答辩页进入时都从该配置读取。
- 默认文案保持原样（实验推广阶段、排队/性能、反馈入口），首次部署行为不变。
- 本地验证：GET 默认文案；保存后注册设置不被覆盖、保存注册设置后提示不被覆盖；
  Playwright 走通管理员编辑 → 保存 → 重新进入知识库看到新文案；答辩页读取同一
  配置显示新文案；关闭启用后答辩不再弹；无 JS 报错。
- **17:35 已部署 `rag-bff:20261126-notice-edit` 到 10.20.154.26:8000，healthy**：
  `/api/notice` 200；知识库/答辩默认提示正常；管理员「📢 进入提示」可打开、
  字段为默认值；知识库与答辩页的「🧠 思考」按钮都在；无 JS 报错。
  回滚点 `20261126-thinking-toggle`。

**17:30 生产验证（`20261126-thinking-toggle`，healthy）**：
同一问题（交易所指令怎么下单）单用户、no_cache：
- 关思考：总 **7.3s**、首字 **6.6s**、无 thinking 事件；
- 开思考：总 **18.3s**、首字 17.3s、1,449 个 thinking 增量。
前端默认关，用户可手动开启查看思考过程；从最初的 95.95s 降到 7.3s。
（`20261126-notice-edit` 待发版。）

---

### 17:23（11-26） | Claude Code | `app/llm.py`、`app/server.py`、`app/static/index.html`、`app/static/defense.html`、`app/defense_coach.py`、`app/defense_api.py` | 知识库 + 模拟答辩的「思考」开关与思考过程展示 | 人要求按钮控制思考；开启时展示思考过程，避免用户觉得卡；两边都要有

- **知识库**：输入栏新增「🧠 思考：开/关」，默认关（按 A/B 更快），偏好存
  `localStorage.rag.think`；`ask` 请求带 `thinking` 布尔值。
  SSE 新增 `thinking` 事件（`text`/`reset`）；前端用可折叠 `.thinkbox` 实时展示，
  最终答案上方保留思考过程。
- **llm.py**：新增 `stream_events_async` + `_stream_anthropic_events_async`，
  thinking/text 分离；重试时发 `thinking_reset`；`stream_async` 保留纯文本包装，
  原有调用方不受影响。
- **模拟答辩**：顶栏新增「🧠 思考：开/关」（与知识库共用偏好）；`start` /
  `start_keywords` / `answer` / `reveal` / `report` / `analysis` 请求都带 `thinking`，
  后端用 `_apply_thinking_choice` 覆盖 cfg。
  答辩模型调用是非流式的：开启思考时等待卡继续显示秒数；返回后
  `start`/`answer`/`reveal` 把捕获的 thinking 放在题目/参考答案上方展示
  （`_chat_anthropic` 现在会把 thinking 块写进 `meta["thinking"]`）。
  `next_question` 增加 `out_meta`；`make_question` 把 thinking 存到
  `sess["last_thinking"]`；`reveal` 返回值增加 `thinking`。
- 本地用 mock LLM 验证：知识库开=10 个 thinking + 4 个 text，关=0 thinking；
  两种模式 UI 均无 JS 报错；答辩 start/answer 返回 thinking；375px 顶栏无溢出。
- 生产未发版；默认关闭思考（A/B：6 条模块用例 5/6 对 5/6，首字 0.4~0.9s
  vs 1.8~20.2s）。

---

### 17:10（11-26） | RAG-Agent | `.dockerignore` | 修 `__pycache__` 过滤不生效，验证生产镜像可离线构建 | 本机 Docker 此前只跑 demo，生产镜像从未在本机验证过

### 问题

`deploy/Dockerfile` 是本机→服务器的发版链路（服务器无外网，镜像在服务器上离线构建）。
但**从未在本机验证过能否构建成功**——万一在服务器上才失败，没外网很难排查。

本机 Docker 里只有 `tools/local_dev` 的演示栈（`demo_bff` + `mock_rag`，假数据），
和真实的 8000 是两套东西。生产镜像 `rag-bff` 从没构建过。

### 验证结果

| 步骤 | 结果 |
|---|---|
| `docker load -i deploy/assets/base-python312.tar` | ✅ 载入 `python:3.12-slim` |
| `docker build -f deploy/Dockerfile -t rag-bff .` | ✅ 8 步全过，离线装 47 个 wheel 用 18.8s |
| 镜像内无 `app/data` / `config.json` | ✅ 只有代码和依赖 |
| 镜像大小 | 913MB |
| **实际跑容器**（挂 data/model/config） | ✅ 启动成功，health 200，向量一致性 ok |

容器日志确认：jieba 加载、LLM 已配置、`Application startup complete`、
`/api/health` 返回 `{"ok":true,...}`。

### 修的问题

`.dockerignore` 里的 `__pycache__` **只匹配根目录，不匹配子目录**，
导致 `app/__pycache__` 被打进镜像。改成 `**/__pycache__` 后重新构建确认已排除。

pyc 文件是宿主机 Python 3.11 编的，镜像里是 3.12，虽然一般不会用到，
但带着不干净，且可能在某些情况下被误加载。

### 发版链路（记录一下，避免以后再搞混）

```
本机：准备离线资产（docker 包 / base 镜像 / wheels / 源码包 / 数据包）
   ↓ 拷贝到服务器
服务器：装一次 Docker → docker build 镜像 → 换 tag 发版
```

**镜像在服务器上构建，不是本机构建后导出。** 本机 Docker 的作用是
提前验证 Dockerfile 和依赖能装成功。

### 17:04（11-26） | Claude Code | 部署/验证 | 上线 `rag-bff:20261126-evalset`（点赞入评测候选 + `llm.thinking` 开关 + 合并 agent/phase1-limit） | 人要求点赞答案可进测试集；同时完成思考开关与并发合并

- 容器 `rag-bff` healthy，`/api/health` 200；备份
  `/opt/rag/backup/20261126-evalset-20261126-170041`（.env/config/镜像列表），
  旧源码 `/opt/rag/src.bak-20261126-170041`；回滚点 `20261126-manual-hits`。
- 首页含「📋 测试集候选」；`/openapi.json` 出现
  `/api/eval/candidates`、`/api/eval/candidates/export`、
  `/api/eval/candidates/{cid}/status`。
- 生产实测：用真实 qa id `20261126170420-7f2e9d` 提交 👍 → 候选生成
  （模块识别 交易所），管理员列表/导出正常；随后标记 `ignored`，待核对列表保持干净。
- 合并后单问题复测（交易所指令怎么下单，no_cache）：检索 **9.96s**
  （日志 `[manual] skip` 5 条手册页），引用 3 条，首字 7.82s，总 19.41s；
  仍偏向 `<期货指令>`，mod-01 的“个股指令”内容缺口仍在（检索内容问题）。
- `thinking` 生产仍保持默认（未切）；A/B 结论见 16:57，确认后改
  `/opt/rag/config.json` 的 `llm.thinking={"type":"disabled"}` 即可（热读）。

---

### 16:57（11-26） | Claude Code | `app/eval_store.py`、`app/server.py`、`app/static/index.html`、`app/llm.py` | 用户点赞的回答进入评测候选；新增 `llm.thinking` 开关 | 人要求"点赞的可以添加到测试集"；并验证思考开/关的业务影响

- 👍 时前端随 `/api/feedback` 带完整问题/答案/引用/kb；👎 不带（减少请求体）。
- 新增 `app/eval_store.py`：写 `app/data/eval_candidates.jsonl`，按 qa_id + 问答哈希去重；
  状态 `pending / included / ignored`；自动用问题识别业务模块。
- 管理员「更多 → 📋 测试集候选」：待核对/已收录/已忽略、复制单条 JSON、导出全部 JSON；
  导出的 `expected_modules` 自动填、`must_include_any` 留空，需人工核对后补。
- 新增 `llm.thinking` 配置：`disabled`/`off`/`none` 或 dict（如
  `{"type":"enabled","budget_tokens":1024}`）；不配置 = 不传，保持网关默认。
  已接到 sync/async 的 chat/stream 四条 Anthropic 请求路径。
- 思考开关 A/B（模块评测 6 条，生产真实数据、新手册定向逻辑）：
  - think：5/6；首字 1.8~20.2s，总 2.7~21.8s。
  - no-think：5/6；首字 0.4~0.9s，总 0.9~2.4s。
  - 唯一失败 mod-01 两边都缺"个股指令"（检索内容缺口），不是思考导致。
  - 结论：本业务以"查文档/给步骤"为主，建议关闭思考；复杂推理是否降质需更大评测集再确认。
- 本地验证：eval_store 单测（去重/状态/导出）；管理员弹窗、收录过滤、导出、
  👍/👎 提交负载均通过 Playwright；无 JS 报错。**生产尚未切 thinking，等确认。**
- **17:00 合并集成**：`agent/phase1-limit` 合入 main（`7942094`）后，非流式
  `kb_ask` 单路新增了一处 `_manual_hits` 直调；已统一收敛到 `_manual_reserved()`，
  自动跳过/过滤/`full`/`off` 行为与流式路径一致。
  该合并把当时未提交的 `app/eval_store.py`、`app/server.py` 也一并带进去了；
  已逐项核对导入/端点/前端调用齐全。`training/` 8.5MB 也随合并入库（若不想入库可再处理）。
  本地检索验证：单路 manual 0 → filtered 1.1s；多路 `_multi_search` 2.2s。

---

### 16:48（11-26） | Claude Code | 诊断（未改代码） | 生产"网关慢"根因：模型思考阶段 + 空流重试，不是网关网络/排队 | 单人问答 sources 后 60s 才出首字

- 网关本身响应正常：`/v1/models` 约 109ms；非流式最小请求 482ms；
  直连 DNS 11ms、HTTP 200。
- 流式响应先给 `thinking` 块（`delta.type=thinking_delta`），再给正文
  （`text_delta`）。应用只统计 `text_delta`，所以用户看到的首字 =
  模型思考时长；思考期间页面没有任何内容。
- 用真实检索上下文（约 2.9k 字符 / 1600 input tokens）直连网关实测：
  - 首 `thinking_delta` **0.8s**；首 `text_delta` **18.0s**；总 19.6s；
  - thinking 2,162 块 / 4,081 字；正文 296 块 / 491 字；`stop_reason=end_turn`。
- 另一次全链路：`sources` 后 8.26s 出首字；更早一次 60.45s。
  当日日志出现两次 `[llm] 异步第 N 次失败: 网关返回空流(没有任何文本)`，
  间隔 2s/4s 重试；说明部分请求只返回 thinking、没有 text（尤其 `max_tokens`
  很小时思考吃满额度），应用判定"空流"并重试，进一步放大首字时间。
- **网关支持关闭思考**：同一真实上下文加 `"thinking": {"type": "disabled"}`，
  首字 **17.9s → 0.7s**、总 **18.3s → 3.0s**；答案仍引用手册页
  （`【指令管理】→【交易所指令】→<期货指令>`），且本次正文更长（552 个 text delta，
  基线只有 82 个）。这是单人延迟最大的可调项；但推理类/多跳问题是否降质需跑
  `eval/run_module_eval.py` 验证后再默认。
- 结论：不是 GPU/检索，也不是网关网络；主要是 **推理模型思考时间 + 空流重试**。
  可做的方向（未实施）：
  1. 答案生成限/关 thinking（需网关支持 `thinking`/`reasoning_effort` 等参数）；
  2. 把 `thinking_delta` 透传成"模型思考中…"进度，避免用户以为卡死；
  3. `stream_async` 的重试判定区分"收到过 thinking"和"完全空流"，避免重复思考；
  4. 复核 `read_timeout=30s` 与思考阶段是否匹配。

---

### 16:40（11-26） | Claude Code | `app/server.py`、`app/config.example.json` | 手册定向检索 `_manual_hits` 减负：主链路已有手册页则跳过，否则先过滤再只重排少量候选 | 单人 how-to 问题仅 `_manual_hits` 就约 30s，58 个候选全量重排只取 2 条

- 实测根因：`_manual_hits` → `backend.retrieve(enable_rerank=True)` → `hybrid_search`
  对 **58 个候选**（BM25 top48 + 向量 top15 合并）全部重排；最终只过滤出 2 条手册页。
  单条约 250 token，4 核 CPU、无 GPU，约 0.5s/条 → 约 30s。
- 新增 `retrieval.manual_hits` 配置：
  - `mode`: `auto`（默认）| `full`（旧行为）| `filtered`（总是过滤后重排）| `off`
  - `pool`: 召回候选池，默认 64
  - `rerank_n`: 过滤后最多重排多少条，默认 16
- `auto`：主链路 `cand + reserved` 里已有 >=2 条"手册路径 + 当前模块"的片段 → **跳过**
  `_manual_hits`（本问题主链路已有 5 条手册页）；否则走 `filtered`：
  召回不重排 → 只留手册路径+模块 → 按 fused 取前 `rerank_n` 条再重排。
- 日志：`[manual] skip ...` / `[manual] filtered rerank N/M cands in Xs`，便于线上核对。
- 本地 CPU 验证（比例参考）：本问题 `auto`/`filtered` 2~3s，`full` 10.5s；
  待生产 4 核容器 A/B 与模块评测确认引用质量。
- 保留 `mode=full` 可一键回滚；`app/config.json` 不必改，代码默认 `auto`。

**16:48 生产部署与实测**（`rag-bff:20261126-manual-hits`，回滚 `20261126-feedback`）：
- 同一问题（交易所指令怎么下单，业务手册，no_cache，单用户）：
  - 检索 `0 -> sources`：**34.76s → 5.66s**；日志显示
    `[manual] skip: main candidates already have 5 manual pages`。
  - 端到端：**95.95s → 14.63s**；LLM 首字 8.26s（前一次 60.45s，网关思考时长波动大），
    生成 0.71s；引用 7 → 5 条（都是 O3.2 手册页，auto 跳过后的主链路结果）。
- 生产镜像已 healthy；配置无需改（代码默认 auto）。
- 质量验证待跑 `eval/run_module_eval.py`（生成 token 后可对生产跑 6 条模块用例）。

---

### 15:16（11-26） | Claude Code | `app/feedback_store.py`、`app/server.py`、`app/static/index.html`、`app/static/defense.html` | 新增「反馈 / 提需求」入口 + 登录提示 + 答辩页实验推广提醒 | 人明确要求：登录进入知识库时提示可在「更多 → 反馈」加内容/提建议/说需求；进入模拟答辩也要提醒实验阶段排队与反馈入口

- 新增 `app/feedback_store.py`：反馈写 `app/data/feedback.jsonl`（追加写、20MB 轮转、可按 `RAG_FEEDBACK_DIR` 覆盖）；
  字段 `kind`（内容补充/改进建议/新需求/其他）、`source`（qa/defense）、`content`、`contact`、`page`；
  管理员「标记已处理」写 `{"t":"done"}` 记录，不改原反馈。
- `server.py` 新增 3 个接口：`POST /api/suggestion`（登录用户）、`GET /api/suggestions`（管理员，
  支持 days/limit/unhandled）、`POST /api/suggestions/{id}/handled`（管理员）。原有 `/api/feedback`
  （答案 👍/👎）保持不变。
- `index.html`：「更多」菜单新增「💬 反馈 / 提需求」（所有人）和「📝 用户反馈」（管理员）；
  反馈弹窗支持类型、1000 字上限、选填联系方式；管理员弹窗按待处理/全部/类型筛选；
  进入知识库时**复用已有的「实验推广阶段」提示弹窗**，在其中加入反馈说明和
  「💬 反馈 / 提需求」按钮（不另加横幅，避免双重打扰）。
- 注意：答案的 👍/👎 已有同名函数 `submitFeedback(box,id,vote,comment)`，新的弹窗提交
  命名为 `submitUserFeedback()`，避免覆盖旧逻辑（已加回归检查）。
- `defense.html`：进入答辩页弹一次「实验推广阶段 / 服务器有限 / 多人提问排队 / 请耐心等待」提醒
  （含「去反馈」按钮）；顶栏新增「💬 反馈」入口。
- 自测：Playwright(Edge) 走通主页面登录→入口选择→实验提示里的反馈按钮→提交→
  管理员查看/标记已处理，答辩页提醒→去反馈→提交；桌面/375px 无 JS 报错、顶栏无横向溢出。
  测试反馈写到临时目录（`RAG_FEEDBACK_DIR`），未污染共享 `app/data`。
- 合并：main 上 11-19~11-26 的已上线改动先提交为 `bd1c4b0`（checkpoint），
  再把 `claude/rerank-tune` 合入 main；冲突仅限 import、入口提示调用和 defense CSS 三处。
- **15:44 已部署 `rag-bff:20261126-feedback` 到 10.20.154.26:8000，healthy**
  （容器 `Up (healthy)`，`/api/health` 200）；服务器备份
  `/opt/rag/backup/20261126-feedback-20261126-154203`（.env/config/镜像列表），
  旧源码 `/opt/rag/src.bak-20261126-154203`；回滚点 `20261126-sql-backfill`。
  验证：首页和答辩页都含「💬 反馈 / 提需求」，`/openapi.json` 出现
  `/api/suggestion`、`/api/suggestions`、`/api/suggestions/{fid}/handled`；
  `app/data/feedback.jsonl` 在第一条反馈时自动创建（当前尚未创建）。
  发版脚本 `deploy/release_to_server.py` 一键完成；本次脚本在最后打印 ✅ 时
  遇到 GBK 控制台 `UnicodeEncodeError`（实际发版已成功），已改为 UTF-8 输出 +
  ASCII `[OK]/[FAIL]` 标记。

---

### 16:25（11-18） | Claude Code | `app/imgindex.py`、`app/server.py`、`app/static/index.html`、`app/config.example.json` | 架构图/流程图结构化 + 明确意图展示最多 2 张 | 人拍板：最多 2 张、明确问、操作图只留文字、架构/流程/拓扑/时序都纳入

- 图片新增 `role`：diagram / operation / text / decorative；从已有 `kind/summary/keywords/ocr` 推断；
- 对 diagram 增加结构化 JSON：title/diagram_type/components/links/layers/flows/aliases/confidence，
  检索文本包含组件、连线、流程、别名；
- `imgindex.search_diagrams` 只检索 diagram，最多 2 张；`diagram_blocks` 供前端展示；
- 只有问题明确命中“架构/拓扑/网络结构/部署图/流程图/时序图/数据流/组件关系”等词，
  才把架构图加入上下文并展示；操作截图保留 OCR 文本但不展示；
- 前端配图底部区最多 2 张，标题改为“相关架构图”；
- `figures.enabled=true, mode=diagram_only`；服务器配置已同步；
- 单张架构图结构化因 thinking 吃 token 曾失败，max_tokens 提到 6000 后正常；
  业务手册 69 张图正在后台补跑结构化（可先按已有摘要展示）。

---
### 16:50（11-26） | RAG-Agent | 检索片段去重：只删重复，不按分数裁 | 人需求「答案明确就少给」，但按分数裁会丢关键内容，实测后改为去重

### 需求

> 「每次都 8 条，感觉不好，能不能改为定为到答案了，那就少点，
> 如果 8 条里面都没有就适当多点，当然不能无限查询，
> 有的时候就是知识库没有这个内容。」

### 尝试过两个方向，都失败（有数据）

**方向一：按重排分数裁**（首条强→3 条，平滑→5 条，全负→说没找到）

失败原因——**关键内容的重排分往往偏低**：

```
xy-03「otrade 部署和升级要注意什么」
  8 条里含 JDK 的只有第 7、8 条，rerank 3.75；
  前 6 条是 4.26~5.41，都不含 JDK。全库仅这 2 条提到 JDK。
  → 裁到 3 条或 5 条都会把它裁掉。
```

根因：**「像不像在回答」和「内容是不是答案」是两件事**。
「JDK 要求 8u181」这种技术参数片段，字面确实不太像在回答"部署要注意什么"，
但它是答案的一部分。按分数裁 = 系统性优先删掉最专业的那部分。

**方向二：按分数裁但不裁到 0**（全负时给 3 条弱片段）

仍不稳定。评测对照（每档连跑两轮）：

| 方案 | 第 1 轮 | 第 2 轮 |
|---|---|---|
| 8 条不裁 | 6/6 | 6/6 |
| 按分数裁 | 6/6 | 5/6 |
| **去重（最终）** | **6/6** | **6/6** |

### 最终实现：只删重复片段

`app/server.py` 新增 `_tech_terms()` / `_shingles()` / `_overlap()`，
`_trim_by_scores()` 改为按文本重合度去重：

1. **5 字滑窗重合度 ≥ 0.85** → 判为重复，只留分数高的那条
2. **同一份文档最多留 3 条**（长文档切成多块时内容相似）
3. **技术名词保护**：这条片段带来了前面没有的英文技术词（JDK/Redis/Zookeeper/O32…）
   → **绝不删**，它可能正是唯一讲这个点的那段

第 3 条是修 xy-03 的关键：含 JDK 的片段与前面的尽调材料文本高度重合（同族文档），
没有保护时会被误判成重复删掉。

不设"必须裁到 N 条"的目标——有多少重复删多少，结果是 8/7/6/5 条都有可能。
这是正确行为：该少的时候少，但绝不能因为"想要少"而丢掉唯一答案。

### 改动

- `app/server.py`：新增 `_tech_terms` / `_shingles` / `_overlap`；重写 `_trim_by_scores`；
  三条问答链路（`kb_ask` 非流式 + 两条流式）统一在 `_build_sources` 前调用；
  空结果分支改为防御性代码（去重理论上不会返回空）
- `app/config.json`：`retrieval.trim` = `{enabled, dup_ratio: 0.85, same_doc_keep: 3, protect_unique_terms: true}`

### 验证

- 单测：强+全重复→3 条；强+各不相同→5 条；首条弱→8 条；全低分→按去重
- **评测连跑两轮都是 6/6**，引用条数 5-8 条（不再固定 8）
- 与 8 条完整基线对比：同样稳定 6/6，没有退化

### 教训（记下来避免重犯）

1. **`t.replace(anchor, new, 1)` 用字符串切片重写文件时，会删掉区间里没注意到的内容**。
   这次就误删了 `_NOT_FOUND_MARKS` 常量，导致非流式接口 500、评测 0/6。
   以后改大块代码用精确行号定位，改完立刻验证 import 和关键常量还在。
2. **评测有波动**，单次结果不足以判断好坏。同一份代码可能 6/6 也可能 5/6，
   必须连跑两轮再下结论。
3. **先查清"是不是我改坏的"**：关掉裁剪跑一次 6/6，才确定 4/6 是裁剪造成的。

### 14:57（11-18） | Claude Code | `app/users.py`、`app/server.py`、`app/defense_api.py`、`app/static/index.html`、`defense.html` | 用户管理的知识库分配拆成智能问答/模拟答辩 | 人要求分配知识库权限也分开

- 知识库记录新增 `shared_with_defense`；`shared_with` 保留为问答；
  老数据没有 defense 字段时沿用 QA 列表，避免升级后答辩看不到已分享库；
- `users.can_read_kb(kb,user,mode)` 支持 qa/defense；`/api/kb?mode=defense`
  只列答辩共享，答辩页改用它；答辩 start/推荐库也按 defense 校验；
- `/api/kb/{id}/share` GET/POST 同时返回/保存两套；
- 用户管理「分配知识库权限」每个用户两列勾选：智能问答 / 模拟答辩；
  问答/答辩可各选不同的人；
- 实测：u1 只分问答、u2 只分答辩；u1 问答列表有 etf、答辩列表无；
  u2 相反；弹窗每用户两个勾选框，无 JS 错误。
- 15:10 已部署 `rag-bff:20261118-b74bb4d` 到 10.20.154.26:8000，healthy；
  服务端 share GET 返回两套列表，管理员弹窗每人两个勾选框正常。

---

### 13:55（11-18） | Claude Code | `app/static/index.html` | 系统参数改为“左侧参数列表 + 右侧详情” | 人反馈参数变多后弹窗排版会飞出去

- 弹窗宽度 820px、固定最大高度，左侧 2 个参数项，右侧只显示当前项；
- 每行显示摘要（入口开/关 + 默认库个数），点开切换详情；
- 手机端参数列表横向排列、详情在下方；
- 保存仍是全局一次保存；以后新增参数只需加一个 `.sysrow` + `.syspane`；
- 本地实测：2 行列表、只显示 1 个详情、切换答辩详情正常、保存成功、无 JS 错误。
- 14:00 已部署 `rag-bff:20261118-982a67a` 到 10.20.154.26:8000，healthy；
  服务端弹窗 2 行参数、单详情、切换正常。

---

### 13:24（11-18） | Claude Code | `app/system_settings.py`、`app/server.py`、`app/static/index.html` | 问答知识库与模拟答辩的知识库权限完全分开 | 人要求两套默认库分别配置

- settings 新增 `registration.defense_kb_ids`；
- 注册时：问答权限开 → 共享 `kb_ids`；答辩权限开 → 共享 `defense_kb_ids`；
  两套都未配置才分别回退业务手册；
- 管理员建号/改答辩权限也走答辩默认库，不再固定只补业务手册；
- 系统参数 UI 两块各有独立知识库多选；
- 实测：问答关+答辩开（kb=[业务手册], defense=[etf]）新号只看到 etf；
  问答开+答辩关只看到业务手册；两者都开看到两者。
- 13:30 已部署 `rag-bff:20261118-8c54df4` 到 10.20.154.26:8000，healthy；
  服务端 GET 已返回 `defense_kb_ids/effective_defense_kb_ids`，UI 两个库列表正常。

---

### 10:56（11-18） | Claude Code | `app/server.py`、`app/docpreview.py` | 预览页改为文档标题目录 + 引用片段定位增强 | 人反馈不是文件目录，要文档大标题/小标题目录，且点开要能看命中上下文

- 预览页左侧从“文件树”改为**当前文档标题树**：
  - docx 的 h1-h6；pptx 每页 h3，没标题的页用“第 N 页”；xlsx 用 sheet 名；
  - 点击标题滚动到对应章节；滚动时高亮当前目录项；手机端仍是抽屉。
- docx 标题识别补充启发式：16pt 加粗、14pt 加粗、“第X章/一、/（一）”编号；
  之前没使用 Heading 样式的现场文档，现在也能生成目录。
- 引用定位改为按段落/表格单元格做 DOM Range 匹配，能处理 Word 拆 run、表格单元格；
  命中后滚动到所在段落上下文并高亮，找不到才退回命中卡片并明确提示。
- 本地实测：docx 标题生成 1 条目录、pptx 15 条；两类都出现 mark；
  点击目录第 2 项 active 正确；页面无 JS 错误。
- 11:00 修复：`surroundContents` 在 Word 加粗 run / 部分文本节点上会抛异常，
  改用 `extractContents + insertNode`；复测“设置截图”在 docx 中 mark=1。
- 11:05 已部署 `rag-bff:20261118-15ecbd5` 到 10.20.154.26:8000，healthy；
  服务端预览 TOC=1、mark=1；系统参数两个独立区块正常显示。

---

### 10:23（11-18） | Claude Code | `app/system_settings.py`（新增）、`app/server.py`、`app/static/index.html` | 管理员系统参数：首次注册默认权限 + 默认共享知识库 | 人要求不做每次开发调整

- 新增 `data/system_settings.json`（运行时数据，Docker 卷持久化），
  不写只读的 `config.json`；支持原子写；
- 默认注册权限：`kb=true, defense=false`（未配置时）；
- 默认共享知识库：未配置时沿用业务手册；可多选，全部不选 = 不自动分配；
- 管理员「⚙ 更多 → 系统参数设置」：知识库/模拟答辩多选、知识库多选，
  `/api/admin/settings` GET/POST；
- `/api/register` 用配置初始化 perms 并共享配置的库；管理员建号仍按表单权限；
- 本地实测：设置 kb+defense+业务手册 → 注册新账号 perms 正确、`/api/kb` 能看到共享库；
  UI 保存成功；测试账号已删除、测试设置文件已清理。
- 10:30 已部署 `rag-bff:20261118-75c1d79` 到 10.20.154.26:8000，healthy；
  服务端 GET 设置正常，默认 kb=true/defense=false、effective=业务手册；
  管理员系统参数弹窗能打开并列出知识库。
- 11:04 按人要求拆成两个独立区块：新账号默认知识库（开关 + 默认共享库）与
  新账号默认模拟答辩（独立开关）。默认共享库只在知识库权限开启时生效；
  退役场景实测：kb 关 + 答辩开 → 不共享默认库、只补答辩用业务手册；
  kb 开 + 答辩关 → 只共享配置的库，不开答辩。

---

### 23:13（11-17） | Claude Code | `app/server.py`、`app/static/index.html`、`app/config.example.json` | 支持 zip 批量上传 + 多文件并行上传/减少 fsync | 人确认 A+C 都做

- **A：/api/kb/{id}/upload_zip**：流式接收 zip，安全解压到 upload；
  路径穿越/符号链接/加密包/zip bomb 限制（500MB 包、2GB 解压、20k 文件、单文件 500MB）；
  支持中文文件名（cp437→GB18030）、跳过 `__MACOSX`/`.DS_Store`/`Thumbs.db`；
  前端新建/添加菜单都有「上传压缩包（zip）」，单 zip 自动走该接口。
- **C：多文件 2–3 路并行上传**：按文件数/总大小拆批并均衡字节，
  XHR 数组统一取消；进度按总字节聚合。实测 25 文件触发 2 路请求，
  100 文件拆成 3 批（33/33/34）。
- **C：fsync 可配置**：新增 `upload.fsync`（默认 false）；
  默认不再每文件 fsync，批量小文件上传更快；需要强持久化可设 true。
- 测试：zip 中文文件名/CSV 正常解压，`../evil.txt` 被拒绝且未落盘；
  单 zip 走 `/upload_zip`，多文件走并行 `/upload`。
- 23:20 已部署 `rag-bff:20261117-7de5753` 到 10.20.154.26:8000，healthy；
  服务端 zip 冒烟：中文目录文件解压成功、文件列表可见，临时库已删除。

---

### 22:42（11-17） | Claude Code | `index_docs.py`、`app/static/index.html` | 支持 DBF 表索引（纯 Python，无新依赖） | 人问“知识库能解析 dbf 文件吗”，确认后实现

- 新增 `extract_dbf` + `.dbf` 注册到 `EXTRACTORS`；
  纯 Python 解析 dBase/FoxPro 头、字段描述、定长记录，不依赖 `dbfread`；
- 表感知输出：文件名、记录数、字段名/类型/长度；每 20 行重复表头，
  行格式 `行N: 字段=值 | 字段=值`，避免长表分块后丢上下文；
- 支持 C/N/F/D/L 字段，I/B/O/Y/T 二进制数值/日期时间；
  字符值按 UTF-8 → GB18030/GBK → cp1252 顺序解码；删除标记记录跳过；
- 尝试读取同名 `.fpt` / `.dbt` memo；解析不到时标记 `[memo 未解析]`；
- 单表默认最多索引 20,000 行、单字段最多 500 字，超出在文本里注明；
- 实测：GBK 中文值、日期、数值、memo、删除记录均正确；经临时库
  `index_kb` 建索引后 chunks 含 `fund.dbf` 和中文值；UI 使用文档补上 DBF。
- 22:45 已部署 `rag-bff:20261117-dc019f4` 到 10.20.154.26:8000，healthy；
  容器内验证 `'.dbf' in index_docs.EXTRACTORS == True`。

---

### 17:38（11-17） | Claude Code | `app/static/index.html`、公开仓库 `hsrag_public` | 每个知识库保留自己的聊天记录 + 公开仓库中文化/同步 | 人要求来回切知识库时问答历史跟着切换，并同步 GitHub

- 主问答新增 `KB_SESSIONS`（内存）：切走时保存当前库的消息 HTML、上下文历史、
  滚动位置；切回时恢复；`resetChat` 只清当前库上下文，删除知识库时清理对应会话。
  实测 A→B→A：A 的问答恢复且不含 B 的内容，B 也恢复自己的记录；刷新页面仍清空。
- 公开仓库 `hsrag_public` 拉到远端最新 `8682765` 后：
  - `README.md` 改为中文介绍、一页结论、目标架构、Demo、2026-11 部署/索引/答辩/交互落地；
  - `docs/deployment.md` 增加离线 Docker 落地记录（脱敏）；
  - `docs/technical-notes.md` 增加索引队列、日志、等待状态、引用定位、按库会话工程化；
  - 敏感词扫描通过，已推送公开仓库 `5588247`。
- 17:40 已部署 `rag-bff:20261117-5058d55` 到 10.20.154.26:8000，healthy；
  服务端实测 A→B→A 各自恢复自己的消息，互不串库。

---

### 15:58（11-17） | Claude Code | `app/server.py` | 文件预览页左侧目录导航 + 命中定位增强 | 人要求点引用文件名后能定位到文件位置，并给预览页加目录导航

- 预览页左侧新增文件树（源目录 + 上传，按真实目录分组展开），当前文件高亮；
  手机端有「☰ 目录」抽屉；点击文件在同一预览页切换
- `/api/kb/{id}/files` 加入只读分享白名单，被分享用户也能看到目录
- 定位片段：`hl` 按 `|`/换行/句号切成短片段再在 `main` 文本节点里查找，
  表格行和跨段片段也能命中；命中后滚动到具体 `mark`，找不到才回到命中卡片
- 本地实测：业务手册预览页 20 个目录 / 41 个文件，当前文件 active=1；
  带 `hl` 的预览出现 1 个 `mark`；点击目录里的其他文件能切换且 active 正常更新
- 16:02 已部署 `rag-bff:20261117-a3cb3d6` 到 10.20.154.26:8000，healthy；
  服务端预览页复测 41 个文件、active=1、命中 mark=1。

---

### 14:26（11-17） | Claude Code | `app/static/index.html`、`app/server.py`、`app/qa_log.py` | Shift+Enter 换行、引用角标联动、点踩补充原因 | 人反馈输入框不能换行、角标与引用区无关联、点踩希望能写原因

- 问题输入由单行 `<input>` 改为可自动增高的 `<textarea>`：
  Enter 发送、Shift+Enter 换行；中文输入法组合期间 Enter 不误发
- 引用角标改成可点击：每个数字带 `data-ref`，点击后展开该回答的引用区、
  滚动到对应 `.r[data-ref]` 并高亮 2 秒；引用摘要提示也写明“点角标可定位”
- 点踩先弹出小文本框“哪里没用”，提交时把 `comment` 与 vote=-1 一起写入
  `qa_log.jsonl` 的反馈记录；管理员问答统计的差评/最近记录里显示这条原因
- 本地 Edge 实测：Shift+Enter 输入 `第一行\n第二行` 且不发送；角标点击能展开并高亮；
  点踩提交的请求体包含 `{"vote":-1,"comment":"答案不具体，没有说明回滚步骤"}`
- 14:30 已部署 `rag-bff:20261117-0d41e2c` 到 10.20.154.26:8000，healthy；
  部署页复测换行/角标高亮/点踩原因均通过。

---

### 10:14（11-17） | Claude Code | defense_coach.py、defense_api.py、server.py、index.html、defense.html、indexer.py | 答辩自动推荐知识库 + 切库清屏 + 文件列表 + 隐藏块/段计数 | 人确认“自动选库但保留选择权”，并要求切换知识库清空旧内容、隐藏技术计数、能看每库文件

- **自动推荐**：PPT 上传后调 `/api/defense/recommend_kb`，后端把 PPT 摘要 +
  用户可读知识库（名称/目录/前 8 个文档示例）交给模型判断；前端预选推荐库，
  显示“📌 根据 PPT 推荐（置信度）”并保留用户改选权；用户改过或已开始演练则不再覆盖。
  实测 O32 总结 PPT → 业务手册，high；单次模型 2.7s、接口 3-9s（后台跑，不挡上传）。
- **切库清屏**：主问答切换知识库时清空 `#chat` 旧消息、上下文和待发图，
  中断正在流的回答，并提示“已切换到「XX」”。
- **文件列表**：新增 `GET /api/kb/{id}/files`（只读、按权限），列出源目录 + 上传目录的
  文件名/大小/来源/修改时间；知识库列表每项加 📂 按钮，弹窗查看。
- **隐藏技术计数**：侧边栏/选中库元信息/答辩知识库下拉不再显示“块 N”“N 段”；
  索引完成消息也不再报块数。管理员问答统计里的技术指标保留。
- 本地 Edge 测试：推荐预选/用户改选不被覆盖/文件列表/切库清屏/下拉无“段”均通过。
- 10:17 已部署 `rag-bff:20261117-fb10f7b` 到 10.20.154.26:8000，healthy；
  服务端实测 O32 PPT 推荐到业务手册（high，6.2s），文件列表/切库清屏正常。

---

### 17:01（11-16） | Claude Code | `app/static/index.html`、`app/indexer.py`、`app/server.py`、`app/config.example.json` | 修“选择文件夹没弹窗”+ 并发审计 | 人反馈新建库点选择文件夹无窗口，怀疑多人使用互相阻塞

- 弹窗问题不是服务端并发：`uploadBusy` 只是当前标签页变量，不会跨用户；
  服务端上传接口也无全局锁。实测（Edge 无头）文件/文件夹/点击虚线框都能弹选择器
- 加固：选择按钮改成真正的 `<button>`，整个虚线框可点（原来是两个小 `<a>`，
  点空白区域没反应）；`<input type=file>` 先挂到 DOM 再 click（部分浏览器
  会忽略游离 input）；服务器端“目录浏览”改名并保留 `allow_fs_browse=false` 的提示
- **并发修复**：
  - `registry.json` 读-改-写加 `_REGISTRY_LOCK`，保存改原子写；
    并发建库/改名/索引完成不会再互相覆盖（40 路并发建库测试 40/40 保留）
  - 上传临时文件、索引状态、chunks 临时文件都改成唯一名，同名文件并发上传不互相踩
  - 索引 worker 从 1 个改为默认 **2 个**（`indexing.workers`，上限 4）：
    不同知识库可并行建索引，同一个库仍只跑一个；实测两个各 1.5s 的索引
    总耗时 1.83s。并行索引时不采样 tracemalloc，避免峰值串台
- 仍保留的有意串行：rerank 并发 1、LLM 全局并发 6/单用户 2（按之前压测的平衡点）；
  这些是限流/资源保护，不是死锁，超限会返回 429 + Retry-After
- 17:02 已部署 `rag-bff:20261116-e9e0f65` 到 10.20.154.26:8000，healthy；
  部署页实测“选择文件夹/选择文件/点虚线框”三个入口都能弹出选择框

---

### 16:25（11-16） | Claude Code | `app/server.py`、`deploy/Dockerfile`、`deploy/docker-compose.yml`、`.gitignore`、deploy/README | 持久化日志目录 `/opt/rag/logs` | 人要求“加个 logs 文件夹，把日志保持到这里”

- `server.py` 启动时把 stdout/stderr 同时写一份到 `RAG_LOG_DIR`
  （容器 `/app/logs`，本地默认仓库 `logs/`），按天 `app-YYYYMMDD.log`，
  启动时清理 30 天前文件；uvicorn 启动/访问日志、`[coach]`、`[llm]`、异常都进文件
- compose 新增 `/opt/rag/logs:/app/logs` 和 `RAG_LOG_DIR=/app/logs`；
  Dockerfile 预建 `/app/logs` 并 chown 1000
- Docker json-file 保留（20MB×5）仍可用 `docker logs`；容器重建不再丢持久化日志
- `.gitignore` 增加 `logs/`；本地实测 `logs/app-20261116.log` 已包含启动和
  `/api/health` 访问日志
- 16:21 已部署 `rag-bff:20261116-809e643` 到 10.20.154.26:8000，healthy；
  `/opt/rag/logs/app-20261116.log` 已验证包含新容器启动和访问日志，
  旧容器日志也已导入该文件

---

### 16:11（11-16） | Claude Code | `app/defense_coach.py`、`app/defense_api.py` | 修复“查看参考答案”失败/慢 | 人反馈服务器一次失败且很慢，要求看日志

- 日志证据：4 次 `[coach] reveal` 分别 70.5s / 64.0s / 38.0s / 78.2s；
  其中 64.0s 的 `out=873 stop=max_tokens`、78.2s 的 `out=153 stop=max_tokens`，
  即模型思考链吃光 3,500 token，只吐出半截 JSON，`_parse_json` 失败 → 界面报失败
- 慢的主因：reveal 走手册精排，4 核容器上 `manual_s=23.5~35.1s`；
  同时上一题的参考答案预生成还没结束，新题又起一个，互相抢 CPU/模型额度
- 修复：reveal/analyze/score 手册检索改 `fast=True`（跳过 rerank）；
  `_REVEAL_SYSTEM` 限制 400 字 / 2 条依据 / 3 个要点；
  reveal/analyze 解析失败或 `stop=max_tokens` 时放大到 6,000 token 重试一次；
  score 重试同样放大；解析失败打印 `stop/out/raw`
- `prefetch_explain` 每会话一次只预生成一题，避免两个参考答案并发生成
- 单测验证：截断输出第一次 `stop=max_tokens`、第二次正常，reveal/analyze 都返回 ok

---

### 15:58（11-16） | Claude Code | 服务器 `/opt/rag/src`、`deploy/README.md` | 部署 `rag-bff:20261116-a27b725` 到 10.20.154.26:8000 | 人确认“好了之后同步服务器更新”

- 上传 6 个 app 文件（server/indexer/defense_api/defense_coach/index.html/defense.html）；
  原文件已备份 `/opt/rag/src-backup-20261116-155552.tar.gz`
- 离线构建新镜像 592MB；`/opt/rag/.env` 固定 `RAG_TAG=20261116-a27b725`；
  `docker-compose -f compose.yml up -d` 后约 30s healthy
- 验证：`/api/health` 200；注册域名仍 `hundsun.com`；首页含 `indexBanner`；
  defense 页含 `waitHtml`；旧镜像 `20261116-o32` 保留
- 回滚：`.env` 改回 `RAG_TAG=20261116-o32`，`docker-compose up -d`

---

### 15:42（11-16） | Claude Code | `app/server.py`、`app/indexer.py`、`app/static/index.html`、`app/defense_api.py`、`app/defense_coach.py` | 索引进度可视化 + 文件/文件夹选择 + 答辩预生成 | 人反馈：建库后不知道没建完、添加文件只能选文件夹、首题 30s/逐题解析 2min

- **索引状态**：`/api/kb` 合并 `indexer.get_index_status`（queued/running/done/failed），
  前端新增常驻进度横幅；新建库无 chunk 时禁用提问并提示“完成后才能提问”；
  完成/失败在对话里明确提示；上传和重建都改轮询 `/index/{id}/status`。
  `index_kb` 进度改成 `解析 3/10: 文件名`。之前前端轮询的是旧 `STATUS`，
  异步队列的 queued/running 根本看不到，进度条会直接消失。
- **文件选择**：新建库和右上角「＋ 添加」都拆成“选择文件（可多选，能看到文件名）/
  选择文件夹”；选完先在对话里列出文件名，再上传。之前两个入口硬编码
  `webkitdirectory=true`，系统框只能选文件夹。
- **答辩提速**：上传 PPT 后后台预生成第一题；答题返回后把本题诊断投到
  1 个 worker 的后台队列；整场解析复用已完成诊断，只补缺题 + 写总体评价。
  出题/诊断的手册检索用 `fast=True` 跳过 rerank（4k 块业务手册实测 6.8s→0.6s），
  看参考答案/整场解析仍走精排。预生成的检索上下文写进 `sess["ctx"]`，
  第一次追问也不再重复检索。
- **实测**（本机 8013，无图 PPT）：首题预生成 5.2s（检索 0.5s + 模型 4.3s），
  追问 3.1s（检索 0.0s）；单题后台诊断 10-13s；3 题汇总解析 14.9s。
  原实现一次性生成 10 题诊断，按同速率约 105-130s，和“2 分钟”吻合。
  带图 PPT 的模型耗时另计。出题/解析/诊断都加了 `[coach]` 耗时日志，
  区分检索 vs 大模型耗时。
- **等待区状态**：答辩页的首题/追问/看答案/报告/逐题解析都改成分阶段状态卡
  （检索手册 → 查历史 → 组织问题 → 模型思考），显示已等待秒数和动态进度条，
  让用户看到页面在动，而不是只有一个静止的“正在追问…”。

---

### 15:10（11-16） | Claude Code | `deploy/prepare_o32_bundle.py`、`app/data/email_domains.txt`、deploy/README | 注册邮箱白名单限定 @hundsun.com | 人补充：注册邮箱格式为 @hundsun.com

- 服务器 `/opt/rag/data/email_domains.txt` 写入 `hundsun.com`，已 chown 1000；
  `/api/register/config` 返回 `["hundsun.com"]`，非该域名返回 400
- 本地 `app/data/email_domains.txt` 同步（共享数据目录，新增文件，未删改）
- `prepare_o32_bundle.py` 打包时复制该文件，避免下次出包退回默认域名
- 未改 `app/server.py`（RAG-Agent 归属）；默认值仍为 `examplecorp.com`，依赖白名单文件覆盖

---

### 14:27（11-16） | Claude Code | `deploy/*`、`.dockerignore`、`.gitignore`、docs | Docker 生产部署上线：RHEL7 + Docker 20.10 + Python3.12，仅业务手册单库 | 人确认长期 Docker

- 新增 `deploy/Dockerfile`、`requirements-server.txt`、`docker-compose.yml`、`docker.service`、
  `prepare_o32_bundle.py`、`README`、根 `.dockerignore`
- 服务器 10.20.154.26：Docker 20.10.24 overlay2；基础 `python:3.12-slim`；
  应用镜像 `rag-bff:20261116-o32` 592MB；容器 healthy，host network 8000
- 只部署业务手册 `20261108_174939_69c8b5`（4,065 块）；镜像验证**无 data/models/config**
- health 200、admin 登录 200、SJSMXn 问答 23.1s 返回引用
- 未改 8123/80；learn-center 仍 active
- 待办：Nginx/TLS/ACL、admin 密码轮换、备份、`docker save`、RAGFlow 另机

---

### 10:44（11-16） | Claude Code | `docs/ai-fde/10-上线方案-目标服务器.md`、README | 目标服务器只读评估 + 两套上线方案 | 人给了一台服务器，先评估、先不动手

- 服务器：RHEL 7.5、16 vCPU、62GB（**11GB 可用**）、50GB 可用；
  无 python3 / docker / redis / postgres / nginx；外网 PyPI 不通；LLM 网关 8080 可达
- 关键卡点：RHEL7 glibc 2.17 只有 **onnxruntime 1.16.3**（cp311）有 manylinux2014 wheel；
  代码 `Path.walk` 需改 `os.walk`；Python 用 3.11.18 standalone
- 端口：8000 应用 + 8080/8443 Nginx；按要求不使用 8123；80 已被 learn-center 占用
- 结论：可上当前单进程应用（离线 wheel + Python 3.11 + ORT 1.16.3）；
  **不建议 RAGFlow / 多 worker**（内存、EOL、无 Docker）；正式生产建议新 VM/OS
- Docker：服务器无运行时；overlay 模块可用，可离线装 Docker 20.10 跑当前应用（A2，
  镜像内解决 Python/ORT/glibc）；Docker 1.13 不可用；RAGFlow 需新 VM + Docker 24+
- 只做只读采集，未安装、未改配置、未重启服务器；文档不含密码

---

### 10:26（11-16） | Claude Code | `app/config.json`、docs | 按人决定关闭配图展示，保留图片文本知识 | 暂不需要看图，文本已够用

- `figures.enabled=false`（main + rag-claude）；`index.image_caption` 保持默认开启
- 图片摘要/OCR 仍在 chunks：鑫元 6,887 块、业务手册 2,231 块，答案可继续引用
- 前端底部区代码保留但不会触发；以后要展示改回 config 即可
- 公开仓库已推送（`8682765`），不含“已展示图片”的承诺

---

### 22:05（11-15） | Claude Code | `app/static/index.html`、docs、data | 前四项：配图底部区、bench 清理、公开仓库脱敏清单、重排零风险测试 | 人要求开始前四项

- 配图：`attachFigures` 改为底部「相关截图」区（visible caption + 来源），
  开启 `figures.enabled`；SSE 实测能推出 figures。**视觉效果需人打开 8000 确认**
- 清理：删 16 个 bench 账号、清空鑫元 `shared_with`、删 7 个 `.bak`
  （users/registry/config 备份，含旧 API Key，已移除）
- 公开仓库：clone 审计，当前无客户名/域名/凭据泄露；已写
  `docs/agents/PUBLIC_SYNC_CHECKLIST.md`（映射、需替换项、建议新增 optimization.md、
  推送前检查），**未推送**
- 重排零风险测试：batch 4/8/12/16/24 均约 1.52–1.61s/job（无收益），
  线程 8 同默认，intra=4 更差；无本地 GPU provider（仅 Azure/CPU）。
  结论：batch/线程不改；下一步只能换更小模型或 GPU/独立服务

---

### 21:45（11-15） | Claude Code | `app/backend.py`、`app/server.py`、`docs/ai-fde/09`、config | 五项交接：global_max=6、Chunk 契约、Ragflow 骨架、09 状态、两个缺陷 | RAG-Agent 20:00 交接

- config `ask_limits.global_max 8→6`（备份 `.bak-global6`）；代码默认值与
  `config.example.json` 同步为 6；8000 重启 PID 17280
- Chunk 新增可选 `doc_id/title/chunk_index/metadata/raw_score`；`available()` 修复
  （按 registry + chunks.jsonl 判断，不再恒 True）；`RagflowBackend` 骨架（未注册）
- health `retrieval.resolved` 由后台线程缓存，探活零文件 I/O；preload 预热一致性
- `09`/README/`07` 状态同步为阶段 1-3 已合并；09 新增 11.6 多用户压测与 `global_max=6` 结论
- eval **6/6**（`20261115_213545`；首轮 5/6 是 xy-03 已知波动）

---

### 21:15（11-15） | Claude Code | `tools/loadtest/*`、docs | 多用户压测：16 账号 × global 4/6/8/12/20 | 单账号测不到 global 层

- 新增 `make_bench_users.py`；`run_loadtest.py` 支持多 token 轮转 +
  `--global-max/--per-user-max/--per-ip`；`test_server.py` 支持限流覆盖
- 放行吞吐从 4 并发起饱和（0.20 条/秒）；global 8 只增延迟不增吞吐
- 20 并发固定 global：4/6/8/12/20 放行 4/6/8/12/20，429 比例 80/70/60/40/0%，
  p95 16.1/25.9/37.6/53.8/85.8s
- **建议 `global_max=6`**（p95 25.9s）；提高并发需先提高重排吞吐
- 结果：`tools/loadtest/results-multiuser-*.json` + `results-multiuser-summary.json`

---

### 18:30（11-15） | Claude Code | `app/rerank.py`、`app/indexer.py`、`tools/export_img_captions.py`、docs | 重排 120s + 图片知识文本化合并上线 | 关闭静默降级；图片展示改文本

- 合并 `claude/rerank-tune` → main `eada098`；8000 重启（PID 18140）
- `RERANK_SLOT_TIMEOUT 10s → 120s`（仅异常兜底），`RERANK_CONCURRENCY=1` 保留
- 图片描述（摘要 + OCR）写成独立 chunk，业务手册重建 1969 块并验证答案可引用图中明细
- **eval 6/6**：`eval/results/20261115_182521.json`
- 已完成：业务手册 caption **1225/1225**（1078 有用 / 147 无内容，1125 有 OCR）；
  鑫元 63,363 块、业务手册 4,065 块，向量均 ready；两库一致性 `ok=true`
- 仍待：前端配图改为底部「相关截图」区后再开 `figures`

---

### 18:25（11-13） | RAG-Agent | 8000 生产合并并重启：阶段 1 + 阶段 2 + Claude Code 索引队列全部上线 | 人同意重启，确认三个分支改动一次性生效

### 做了什么

1. `git merge agent/phase1-limit` 到 main（**无冲突**），带入：
   - 阶段 1：三层限流 + 429 + `Retry-After`、`/api/health` 改 async
   - 阶段 2：`llm.chat_async` / `stream_async` + 共享 `AsyncClient`、SSE 异步生成器
   - 接入：`enqueue_index` / `/index/status` 路由 / `recover_index_statuses` / health 加 `embeddings`
   - Claude Code 的 `claude/rerank-tune`（索引队列、向量一致性校验、建索引内存峰值）

2. 重启 8000（旧 pid 42980 → 新 pid 48484）。重启前确认：
   - main 工作区干净
   - `app/data/*/index_status.json` 里 **0 个** running/queued 任务（不会打断索引）

### 重启前验证

- `py_compile` 全部通过：server / llm / kb / indexer / embeddings / rerank / planner
- 六个关键 API 全部存在：`enqueue_index` / `get_index_status` / `recover_index_statuses`
  / `cached_consistency_report` / `chat_async` / `stream_async`

### 重启后验证

| 项 | 结果 |
|---|---|
| `/api/health` | 200，限流配置 `20 / 2 / 60` 生效，`embeddings` 字段存在 |
| 索引状态路由 | 两个库返回 `idle` |
| 异步 SSE | HTTP 200，正常出流 |
| **评测（跑在 8000 上）** | **6/6 全通过** |

评测六条耗时：20 / 9 / 19 / 20 / 13 / 10 秒，都带 8 条引用。

### 一个需要注意的地方

8000 现在跑的是**单进程**。`_ask_global_active` 等限流计数在进程内存里，
重启会清零——这是预期行为（阶段 4 状态外置才解决）。

### 还没做的

`app/rerank.py` 的 `RERANK_SLOT_TIMEOUT = 10.0` 未改（Claude Code 归属）。
20 并发时 19 个请求白等 10 秒后降级，仍是首字延迟的最大单一贡献（约 10/15 秒）。
已两次在 INBOX 提出，等它处理。
### 18:35（11-15） | RAG-Agent | `global_max` 20→8（扫参拐点）+ 前端补 429 提示 | 人决定：不裁功能，改用准入控制，让进得来的人体验完整、进不来的人立刻知道

### 为什么改

上一轮试过「重排并行」（`RERANK_CONCURRENCY` 1→4 + `intra_op_num_threads=4`），
**实测无效**：并行版 50 并发首字 15.9s，串行版 15.2s，反而略差。
根因是重排单次约 2 秒、且每次推理要吃满多核，4 路并发在真实负载下互相抢 CPU，
ttft 从稳定（13.6/13.6/13.6）变成剧烈抖动（12.9/15.7/25.5/28.5）。
**已回滚**，`rerank.py` 恢复成 Claude Code 的版本（并发 1、超时 120s）。

### 分段实测（`_profile.py`，鑫元库，已预热）

| 阶段 | 耗时 |
|---|---|
| `hybrid_search` 仅 BM25 | **12 ms** |
| `hybrid_search` 向量+BM25（不含重排） | **110 ms** |
| `hybrid_search` 含重排 | **4110 ms** |
| 其中单独 `rerank(16 候选)` | 1825 ms（**占 44%**） |

检索已经压到 110ms，重排是唯一的成本中心。

### 拐点扫参（20 个并发请求，只调 `global_max`）

| global_max | 放行 | 429 | 墙钟 |
|---|---|---|---|
| 4 | 5 | 15 | 25.3s |
| 6 | 7 | 13 | 37.2s |
| **8** | **9** | **11** | **57.2s** |
| 12 | 13 | 7 | 61.5s |
| 20 | 20 | 0 | 97.0s |

取 **8**：再往上放人，墙钟不再变短（57→61→97s），只是把等待摊给更多人。
原始数据 `tools/loadtest/results-global-sweep.json`。

### 改动

1. **`app/server.py`**：`_ASK_LIMIT_CFG["global_max"]` 20→8，
   加载器默认值同步改 8（否则换台机器又变回 20）。
   备注里写清了扫参数据和「服务器扩容后要重测」。

2. **`app/static/index.html`**：**之前完全没有 429 处理**，用户只会看到界面卡住。
   新增：
   - `showBusy(msg, wait)`：显示后端返回的中文原因 + 倒计时按钮，
     倒计时结束变成「↻ 现在重试」，点击重新提问。
   - 流式问答处加 `resp.status === 429` 分支，读 `msg` 和 `Retry-After`。
   - 补 `.busy` / `.busy-act` / `.retry-btn[disabled]` 三条样式，按钮复用现有 `.btn`。

### 验证

- `/api/health` 返回 `{"global_max":8,"per_user_max":2,"per_ip_per_min":60,"retry_after":5}`
- 12 个并发请求（同一账号）→ 放行 2 / 429 10，说明**每用户层先于全局层生效**
- 429 响应体：`{"ok":false,"msg":"你还有 2 个问题正在回答中，等它们结束后再问（每人同时 2 个）。","retry_after":5}`
- **评测 6/6**，每问耗时 **6-12 秒**（改动前 9-20 秒）

### 关于「换小重排模型」

查了本地模型库：只有 `bge-reranker-base-int8`（266MB，xlm-roberta，12 层 768 维，已 INT8 量化）。
HuggingFace 可访问，但 `bge-reranker-v2-m3` 和 `-large` 都比 base **更大**，
BAAI 没有更小的中文重排模型。**这一轮不做**，等有 GPU 或找到合适的小模型再说。

### 16:55（11-13） | RAG-Agent | 接入 Claude Code 的索引队列 API（三处）+ 合并 claude/rerank-tune | 按 INBOX 16:25 交办事项接入并验收

### 三处接入（都在 `app/server.py`，按 Claude Code 在 INBOX 16:25 的要求）

1. **`kb_index` 异步分支改用 `indexer.enqueue_index(kb_id)`**
   原来是起一个裸 `threading.Thread` 直接跑 `index_kb`：没有队列、没有持久化状态、
   服务重启就丢进度、失败不重试。现在交给 `indexer` 的单 worker 队列。
   `sync=True` 分支保持直接跑不变（脚本/排障要同步拿结果）。

2. **新增 `GET /api/kb/{kb_id}/index/status`**
   返回 `indexer.get_index_status(kb_id)`，前端可轮询。
   状态取值：`idle / queued / running / done / failed / interrupted`。
   库不存在返回 404。

3. **启动时恢复 + health 加向量一致性**
   - `preload_kbs()` 最前面调 `indexer.recover_index_statuses()`：
     上次没跑完的 `queued/running` 标成 `interrupted`，否则前端一直转圈。
   - `/api/health` 增加 `embeddings` 字段，来自
     `embeddings.cached_consistency_report()`。**只返回已算过的结果**，
     探活路径不做文件 I/O——否则健康检查又变慢接口。

### 合并 `claude/rerank-tune`

Claude Code 说它的分支已含最新 main、等我阶段 2 提交后再合。阶段 2 已提交（`7791cd4`），
故现在合并：`f34ab27`（索引队列 + 完整性校验 + 峰值观测）、`1178f2f`（INBOX caption 进度）。

**冲突**：只有 `docs/agents/CHANGELOG.md`（双方都往顶部加条目）。
解决方式：**两侧条目全保留**，我的在前、它的在后，只删冲突标记行，不删任何一篇内容。

### 验证

| 项 | 结果 |
|---|---|
| 接口可用性 | `enqueue_index` / `get_index_status` / `recover_index_statuses` / `cached_consistency_report` 全部 True |
| 编译 | `server.py` AST 通过 |
| `/api/health` | 200，`embeddings` 字段已生效（返回 `{}`，符合「只读已算过的」设计） |
| 索引状态路由 | 两个库返回 `status=idle`；不存在的库返回 **404** |
| 异步入队 | POST `/api/kb/{id}/index` 返回 `status=queued`（走队列，不是裸线程） |
| 队列执行 | 1 秒后查状态为 `done`，`attempt=1` |
| **评测** | **6/6** —— 历史最好成绩（基线一直是 5/6） |

**xy-01 和 xy-03 这轮都 PASS**。xy-01 是 11-13 补同义词后转正的；
xy-03 是已知波动项，本轮正常发挥。6/6 值得记一笔：说明当前代码在评测集上
没有已知退化点。

### 服务状态

改动在 `rag-agent` worktree（分支 `agent/phase1-limit`），**8000 生产未重启**。
注意：8000 现在跑的仍是旧 main 代码，不含本次合并与阶段 2，要生效需要合并到 main
并重启（重启前请确认没有在跑的评测）。

### 16:40（11-13） | RAG-Agent | 阶段 2 完成：LLM/SSE 异步化 | 同步 SSE 占线程池，并发越高越慢（50 并发首字 64.9s）

**依据**：`docs/ai-fde/09-并发重构方案.md` 第四节、`07-技术笔记:14-21`

### `app/llm.py` 新增异步层（同步版一个没删）

- `get_async_client()`：按事件循环缓存的共享 `httpx.AsyncClient`，
  `Limits(max_connections=100, max_keepalive_connections=50)`。
  用 `id(loop)` 做键——AsyncClient 绑定 loop，uvicorn 重启/测试换 loop 会报
  "Event loop is closed"。
- `chat_async()` / `stream_async()`：与同步版一一对应，**重试语义完全一致**
  （`chat` 最多 3 次；`stream` 只在"一个字都没吐出来"时重试，避免重复半截回答）。
- `aclose_async_clients()`：进程退出时清理。
- **回滚保证**：`chat()` / `stream()` / `_stream_anthropic()` / `_stream_openai()`
  一行未动，异步版出问题立刻能切回。

### `app/server.py`

- `_run_ask_stream` 里新增 `_agen_body()`（异步生成器，311 行），
  由 `_gen_body()` 用脚本机械复制后替换 4 处调用生成，避免手抄 300 行出错：
  - 4 处 `for token in llm.stream(...)` → `async for token in llm.stream_async(...)`
  - `kb.hybrid_search` / `_multi_search` → `await run_in_threadpool(...)`
  - `planner.rewrite_query` / `planner.plan_queries` → 同样走线程池
    （里面是 `httpx.post`，同步跑会阻塞事件循环）
- `agen()` 包裹 `_agen_body()`，每条 SSE 检查 `request.is_disconnected()`，
  用户关页面就停，不再继续烧网关额度。
- `StreamingResponse(gen())` → `StreamingResponse(agen())`。
  **同步版 `gen()` 保留在上面**，回滚只需改这一行。
- 限流名额仍在 `finally` 里释放，未受影响。

### 实测（mock tokens=100 / delay=0.02 / ttft=0.3，鑫元库 18 问，临时把 per_user_max 放宽到 50 只看全局层）

| 并发 | 阶段 1（同步） | 阶段 2（异步） | 提升 |
|---|---|---|---|
| 10 首字 p50 | 35.9s | **13.6s** | 2.6× |
| 30 首字 p50 | 61.3s | **15.2s** | 4.0× |
| 50 首字 p50 | 64.9s | **15.2s** | 4.3× |
| 50 首字 p95 | 96.6s | **15.2s** | 6.4× |

**最关键的信号**：30 并发和 50 并发的首字几乎相同（15.2s），而同步版是并发越高越慢
（61.3s → 64.9s，p95 96.6s）。说明**线程池不再是瓶颈**，异步化达到了设计目的。

**但没到方案里写的 <10s 目标**，原因见下。

### 找到新的瓶颈：重排并发 1（需 Claude Code 决策）

单独测 `kb.hybrid_search`：

```
含重排   首次 96.29s（模型冷启动）  之后稳定 4.45 / 4.60s
不含重排 0.15s
```

6 个线程同时检索（已预热）：墙钟 12.1s，最慢请求 12.1s，而单请求只要 4.5s。

原因：`rerank.py` 的 `RERANK_CONCURRENCY = 1` + `RERANK_SLOT_TIMEOUT = 10.0`。
20 个并发请求里有 19 个在排队，每个最多等 10 秒，等不到就降级为 BM25 排序。
**这 10 秒白等了还没用上重排**，是当前 15.2s 首字的主要构成。

Claude Code 在 11-13 测的是「并发 1 吞吐 0.30-0.32 job/s，并发 2 是 0.28」——
那是**单请求延迟最优**的结论，成立。但**并发排队场景**下结论不同：
一个请求占着重排 4.5 秒，别人只能干等。这里的取舍需要它来定，
`rerank.py` 是它的归属，我没有动。

> 已在 INBOX 留言，附完整数据，请它决定：提高并发 / 缩短等待超时 / 
> 高并发时直接跳过重排。

### 评测：5/6（与基线一致，且 xy-01 首次通过）

- **xy-01 PASS** —— 11-13 补的同义词（`O-order` / `o-order` / `O订单`）生效了，
  之前的"稳定 FAIL"确认就是评测口径问题。
- xy-03 FAIL（缺依赖组件关键词），已知波动项，连跑多次时好时坏，与本次改动无关。
- 评测走的是 `/api/kb/{id}/ask` 非流式端点，本次改动只影响流式端点 `/ask/stream`。

### 验证

- `py_compile` 通过（llm.py / server.py / kb.py）
- AST 检查：`_agen_body` 为 `AsyncFunctionDef`，await=4 / yield=46 / asyncfor=4
- 单请求异步 SSE 完整输出：状态 200、首字 2.4s、124 个事件、908 字、含 done
- 并发时限流仍生效：6 并发 → 2 放行 / 4 个 429
- 空闲 `/api/health` 24.8ms（p95 31.1ms）

### 服务状态

改动只在 `rag-agent` worktree（分支 `agent/phase1-limit`），**8000 生产未重启**。
`app/config.json` 的 `ask_limits` 已恢复默认（20 / 2 / 60），压测过程中的临时放宽已撤销。

---

### 17:30（11-13） | Claude Code | `tools/export_img_captions.py`、`app/config.json`（figures off）、docs | 配图展示评估 + 关闭内联展示 + 图片摘要转文本 | 前端图文错配

- 评估：图片哈希映射一致、视觉模型可读图、复核能拒绝不相关段落；
  短板是前端 `attachFigures` 按首句前缀猜 DOM 块，后端段落与渲染块不对应
- `figures.enabled=false`（main + rag-claude，备份 `.bak-figures-off`）；
  `index.image_caption` 保持 `true`，文本通道不受影响
- 新增导出工具：summary + OCR 写入 `_imgcap/text` sidecar；
  已导出 鑫元 108 文档/3458 条、业务手册 14 文档/68 条
- `indexer.py` 把图片描述拆成独立 chunk（摘要一行 + OCR 一行），避免被正文稀释、
  也避免模型把 OCR 当成「图片说明」忽略
- 实测：业务手册重建 1969 块；「SJSMXn/SJSJG/SJSFX/SJSGB 用途」的答案直接引用 OCR 明细
- 建议前端改为底部「相关截图」区并显示 caption，再重新打开 figures

---

### 16:50（11-13） | Claude Code | `app/rerank.py`、`docs/ai-fde/07`、`09` | 撤回重排 10s 静默降级，改为「准入控制削峰」 | 人明确：并发是找平衡，不是裁功能

- `RERANK_SLOT_TIMEOUT` 10s → 120s（仅异常兜底）；`RERANK_CONCURRENCY=1` 保留（纯吞吐收益）
- 「候选 24→16」改为可选的高负载降级，不做默认；正常负载保留完整候选与重排
- `07` 第 4/14 节、`09` 11.2/11.5 记录平衡原则：过载由 429 决定，不让已放行的请求静默降质
- 建议阶段 2 后用 mock LLM 重测 `GLOBAL_MAX_ASK` 6 / 8 / 12，按延迟、429 比例、质量选拐点

---

### 16:30（11-13） | Claude Code | `app/indexer.py`、`app/embeddings.py`、`docs/ai-fde/07` | 索引任务队列 + 向量完整性校验 + 建索引内存峰值 | 按 RAG-Agent 15:05 分工的第 1/3/4 件

**`app/indexer.py` — 索引任务队列**

- 新增 `enqueue_index(kb_id)` / `get_index_status(kb_id)` / `recover_index_statuses()`
- 单 worker 后台线程：同库只跑一个，运行中再次请求在本轮后自动重跑
- 状态持久化 `app/data/<kb_id>/index_status.json`：
  `status / progress / started / finished / error / attempt / stats / peak_mb / elapsed`
- 失败重试 2 次（共 3 次）；重启后遗留的 queued/running 标 `interrupted`
- 队列内用 tracemalloc 采样，峰值写入 `peak_mb`

**`app/embeddings.py` — 向量完整性校验**

- `check_consistency(kb_id, v=None)`：比较 chunks 行数与 vectors 行数，按 mtime 缓存
- `consistency_report()`（启动预热，真数行数）/ `cached_consistency_report()`（health 用）
- `search_vectors` 在读锁内校验；不一致时返回空并打 WARN，自动退化为纯 BM25

**验证**

- 沙箱队列：正常完成、失败两次后成功（attempt=3）、一直失败标 failed
- 沙箱完整性：chunks=10 / vectors=8 → 返回 0 条；修正为 10/10 → 正常召回
- 真实全量重建（鑫元源目录 359 文件 / 0.91GB，临时目录）：56,870 块、
  **峰值 192.5MB**、213s（tracemalloc 口径，C 扩展大缓冲未全计入，属下界）

**待 RAG-Agent 接入**：`server.py` 的异步索引分支改 `enqueue_index`、新增
`/api/kb/{id}/index/status`、启动调 `recover_index_statuses()`、
health 加 `cached_consistency_report()`（详细代码在 INBOX 16:25 条）。

---

### 15:10（11-13） | RAG-Agent | `run_dev.ps1` 改为按目录名推导端口 | Claude Code 指出的合并隐患，已修

Claude Code 在 14:55 条记录了一个隐患：`run_dev.ps1` 是被 git 跟踪的 per-worktree 文件，
每个 worktree 需要的端口不同，每次合并都会在 8011/8012 之间来回翻转并产生冲突
（这次合入后主目录变成了 8011）。

**选方案 1（按目录名推导端口）而不是方案 2（加 .gitignore）**，理由：

- `.gitignore` 方案会导致每个新 worktree 都要手动补文件。这正是之前踩过的坑——
  `app/config.json` 被忽略，新建 worktree 里服务直接 `FileNotFoundError` 起不来。
- 按目录名推导是一份文件适配所有 worktree，新目录开箱即用，且永不冲突。

**改动**（`run_dev.ps1`，三个 worktree 同一份内容）：

```
project\rag        → 8000
project\rag-agent  → 8011
project\rag-claude → 8012
其它/未知目录      → 8013
```

另外加了两点：

- 落地在生产端口 8000 时打印黄色警告
- 端口被占用时直接报错并提示换端口，不让 uvicorn 自己抛异常

**已提交到三个分支**：main `77fa6d3`、agent/phase1-limit `a7280a2`、claude/rerank-tune `02c0f6f`。
PowerShell 语法检查通过。

---

### 15:10（11-13） | RAG-Agent | 核实「用户 1000 perms.kb=false」：已不存在 | 实测确认，无需处理

Claude Code 在 14:40 和 14:55 两条里都提到：用户 `1000` 的 `perms.kb=false`，
它对自己拥有的库提问仍会 403，建议管理员确认是否漏配。

**实测结果：这条已不再成立。**

```
1000 = u_56709aa0dc  perms = {'kb': True, 'defense': True}
拥有: etf2.0 (272 块)、鑫元基金项目 (56426 块)
```

用 `1000 / xhy200326` 登录 8000 验证：

- 登录返回 `perms = {"kb":true,"defense":true}`
- `/api/kb` 返回 3 个库：业务手册（被分享，只读）、etf2.0（自己的）、鑫元基金项目（自己的）
- **没有 403**

中间有人把 `perms.kb` 打开了（不是我改的，我这边只做过读取）。
Claude Code 当时的观察是对的，只是那个时点的状态已经变了。

---

### 14:55（11-13） | Claude Code | `docs/agents/CHANGELOG.md`（仅记录） | 阶段 1 已合入 main 并在 8000 复验 | 合并后回归

- main `44e1066`（含 `agent/phase1-limit`）；8000 已重启，`/api/health` 返回限流配置
  （全局 20 / 每用户 2 / 每 IP 60），4 个知识库 BM25 预热完成。
- eval 复跑 **4/6**：xy-01 仍是 `O-order` 拼写问题；xy-03 本轮未提 JDK。
  单独复跑 xy-03 **两次均 PASS**（两次都命中 `jdk`），确认是模型/改写波动，不是阶段 1 回归。
  阶段 1 只改了流式 ask、health、warmup，非流式 `kb_ask` 链路未动。
- 已知隐患：`run_dev.ps1` 是 tracked 的 per-worktree 文件，合并时会在 8011/8012 之间来回改
  （这次合入后主目录变成 8011）。建议改成按目录名推导端口，或加入 `.gitignore` 各 worktree 本地保留。

---

### 14:40（11-13） | Claude Code | `docs/agents/CHANGELOG.md`（仅记录） | 合并后 eval 验收 5/6，唯一 FAIL 是关键词拼写 | 记录合并后的真实验收状态

- main `6615f10`（含 `claude/rerank-tune`）已在 8000 启动，4 个知识库 BM25 预热完成。
- `eval/run_eval.py` 用管理员账号跑（评测集取自 `rag-agent/eval/xinyuan_cases.json`）：
  **5/6 PASS**。xy-01 FAIL 仅因答案写的是 `O-order`，用例只认 `OORDER/oorder` 子串；
  `kb.tokenize` 本来就把 `o-order` 归一为 `oorder`，**不是检索退化**。
  其余 5 条 PASS，每条 8 条引用。
- 用户 `1000` 的 `perms.kb=false`，用它跑一律 403；本次用管理员账号，**未改任何权限**。
- 建议 eval 用例给 xy-01 的同义词组补 `O-order`（`eval/` 归属 RAG-Agent，未擅自改）。

---

### 14:25（11-13） | RAG-Agent | 阶段 1「止血」完成：三层限流 + 健康检查异步化 | 50 并发下用户要干等 127 秒才拿到首字，过载时应当明确拒绝而不是排队

**依据**：`docs/ai-fde/09-并发重构方案.md` 阶段 1、`03-目标架构与数据权限.md:129-130`

**`app/server.py` 改动**

1. **三层限流**（`_ASK_LIMIT_CFG`）：全局同时 20 个 / 每用户 2 个 / 每 IP 每分钟 60 次（滑动窗口）；超限一律**立刻返回 429 + `Retry-After: 5`**，不排队
2. **`_acquire_ask_slot()`**：三层依次判，任何一层不过直接抛 `RateLimited`
3. **`kb_ask_stream` 改 `async def`**：限流需要事件循环；内部逻辑仍走线程池（阶段 2 再改）
4. **`/api/health` 与 `/api/warmup` 改 `async def`**：不再排在线程池后面
5. **名额泄漏自愈**：`_slot_gc()` + 后台线程每 30 秒回收，超时 180 秒强制释放
6. **配置化**：`app/config.json` 的 `ask_limits` 段可覆盖，默认值写死在代码里

**过程中踩到的三个坑（都已修）**

| 坑 | 现象 | 修法 |
|---|---|---|
| `async with` 包住 `return StreamingResponse` | 5 并发全部放行，等于没限流 | 流是懒执行的，名额在开始前就被释放了 |
| 同步生成器 `finally` 不保证执行 | 客户端断开后名额永久占住 | 超时兜底回收（180 秒）+ 后台线程 |
| `asyncio.Semaphore` 泄漏无法修复 | 计数永远回不来，只能重启 | 换成整数计数器 + 锁，回收时能直接修正 |

**实测结果**（mock 参数与基线一致：tokens=100 / delay=0.02 / ttft=0.3，鑫元库 18 问）

| 并发 | 基线首字 P50 | 每用户限 2 | 全局限 20 |
|---|---|---|---|
| 10 | 24.7s | 2/10 放行，**8.0s** | 10/10，35.9s |
| 30 | 66.0s | 2/30，**8.5s** | 20/30，61.3s |
| 50 | 127.4s | 2/50，**8.5s** | 20/50，64.9s |

- **被拒的请求立刻 429**，不再等 127 秒——这是阶段 1 的核心价值
- 放行的请求仍然慢（35-65 秒），那是阶段 2（异步化）要解决的
- `/api/health` 空闲 8-11ms（基线 100 并发时 1985ms）
- 注：这批数据是在合并 Claude Code 的 BM25 倒排索引**之前**测的，合并后放行请求的耗时应该更低，待阶段 2 一起复测

**评测**（`eval/run_eval.py`）：**5/6**，与 11-05 基线一致

- xy-01 稳定 FAIL（缺 OORDER），历史遗留，改动前就失败
- xy-03 波动（有时提 JDK 有时不提），模型输出不确定性，非本次改动引起
- 连跑 3 轮：4/6 → 5/6 → 5/6，基线值 5/6

**顺手修的两个会导致数据丢失的问题**

1. **`.gitignore` 误伤评测集**：`eval/*.json` 把 `xinyuan_cases.json` 也忽略了，导致它从未进过 git。改成 `eval/results/`（只忽略结果，保留案例）
2. **`_load_ask_limits()` 从未被调用**：配置项形同虚设。已在模块加载时调用

**恢复的两个丢失文件**（从历史结果反推）

- `eval/xinyuan_cases.json`：从 `eval/results/20261105_135909.json` 恢复 6 条案例
- `eval/baseline.json`：从 `tools/loadtest/results-baseline.json` 恢复 18 条压测问题

**`eval/run_eval.py`**：新增 `RAG_EVAL_BASE` / `RAG_EVAL_USER` / `RAG_EVAL_PASS` 环境变量，可在 worktree 上打 8011 测试，不用改代码也不用动生产。

**服务状态**：改动只在 `rag-agent` worktree（分支 `agent/phase1-limit`），**8000 生产服务未重启、未受影响**。合并前请人在主目录 `git merge` 并跑回归。

### 17:20（11-12） | RAG-Agent | 建立 git worktree 并行开发环境 | 人和 Claude Code 要我同时开发但互不影响，靠文件归属表已经不够

**为什么要做**：今天定的重构方案要改 `server.py`、`llm.py`、`kb.py`，跨了归属边界。
光靠 AGENTS.md 的约定防不住"同时改不同文件但逻辑互相依赖"。

**目录布局**（`git worktree add`，共享同一份 `.git`）：

| 目录 | 分支 | 端口 | 谁用 |
|---|---|---|---|
| `project\rag\` | `main` | 8000 | 人 |
| `project\rag-agent\` | `agent/phase1-limit` | 8011 | RAG-Agent |
| `project\rag-claude\` | `claude/rerank-tune` | 8012 | Claude Code |

**数据共享方案**：`app\data`（975MB）、`models`（373MB）、`index`（65MB）
用 Windows Junction 指向主目录，避免复制 1.4GB 且保证三边数据一致。
实测 worktree 里 `indexer.load_registry()` 能读到全部 4 个知识库。

**踩到的坑**：`app\config.json` 被 `.gitignore` 忽略，worktree 里没有，
导致 `llm.load_config()` 直接 `FileNotFoundError`。已单独复制到两个 worktree；
以后主目录改了 config 要手动同步（已写进文档）。

**新增文件**：

| 文件 | 内容 |
|---|---|
| `docs\agents\WORKTREE-并行开发.md` | 完整指南：建/删 worktree、合并、冲突处理、故障排查 |
| `rag-agent\run_dev.ps1`、`rag-claude\run_dev.ps1` | 各自专用的启动脚本（端口隔离） |

**改动的共用文件**：
- `AGENTS.md` 第七节：新增「并行开发用 git worktree」小节；端口表补 8011/8012
- `CLAUDE.md`：顶部加警示，明确 Claude Code 的工作目录是 `rag-claude`

**验证实测**：

- `rag-agent` worktree 起在 8011，`/api/health` 200，读到 4 个知识库（含鑫元 56426 块）
- 同时 8000 生产服务 PID 10232 正常，两者互不影响
- 验证后已停掉 8011 测试实例

**交接**：合并前两边各自 `git commit`，由人在主目录 `git merge`。
冲突处理原则：「只改自己负责的文件；两边都要改同一文件时，先合一个，另一个基于新 main 改」。
---

### 13:35（11-13） | Claude Code | `app/kb.py`、`app/embeddings.py`、`app/rerank.py` | BM25 倒排索引、向量 mmap（兼容重建）、重排并发调参 | 落实 `09-并发重构方案` 中分给 Claude Code 的三项，数据依据见 INBOX 13:35 条

**`app/kb.py` — BM25 倒排索引（公共 API 完全不变）**

- `BM25.build()` 从已有 `tf_list` 构建内存 CSR 倒排：`term -> [start,end)` 切
  `_post_idx(int32)` / `_post_tf(float32)`，查询只遍历命中词的 posting。
- 路径加权（文件名词重叠、逐字命中、权威/降权、版本权重）改为按 **270 个去重路径** 算一次，
  再按 `_path_ids` 向量化；`name_hit` 不再每次重建 5.6 万条 path set。
- 建完释放 `tf_list`（实测 5.6 万块约 330MB），倒排本身约 24MB。
- `query/name_hits/search/hybrid_search` 签名与返回结构不变，RAG-Agent 无需改调用。
- 运算顺序刻意与旧实现保持一致，实测 **12 条查询 top-20 逐位相同、分数 max diff = 0.0**；
  查询耗时 75–172ms → 1.6–7.0ms（20–49 倍）。

**`app/embeddings.py` — 向量 mmap**

- `load_vectors` 改为 `np.load(..., mmap_mode="r")`，不再常驻 100MB+ 全量数组；
  `search_vectors` 每查询「打开 → matmul → 关闭」，meta 按 mtime 缓存。
- 原因：Windows 上已打开的 memmap 会让 `os.replace` 失败（实测 WinError 5）。
  为避免 `ensure_vectors` / 手动 `build_vectors.py` 重建被阻塞，改成不长期持有映射。
- 每 kb 一把写者优先读写锁：查询可并行，替换 `vectors.npy` 时独占；
  `save_vectors` 的 replace 加 6×0.2s 重试，覆盖跨进程手动重建的毫秒窗口。
- 实测：8 读线程 + 15 次并发重建，946 次查询 0 错误；替换后立即可读到新向量。

**`app/rerank.py` — 并发与排队超时**

- `RERANK_CONCURRENCY 2 → 1`；`RERANK_SLOT_TIMEOUT 120 → 10s`（过载快速降级为 BM25 排序）。
- 16 vCPU 实测：默认线程+并发1 = 0.30–0.32 job/s；并发2 = 0.28 且单请求耗时翻倍；
  `intra_op_num_threads=2` 的方案实测更差（0.19），故未采用。

**未做 / 待确认**

- `eval/run_eval.py` 未跑：脚本 hardcode `BASE=http://127.0.0.1:8000`（主服务），
  且会写共享数据。需人给一个窗口，用临时副本指向 8012 再跑。
- 「候选数 24→16」收益最大（吞吐 +50%），但改在 `hybrid_search`（RAG-Agent 查询侧），
  已在 INBOX 建议，未擅自改。

---

### 17:05（11-12） | RAG-Agent | 新增 `docs/ai-fde/09-并发重构方案.md`；清理 5 个死代码脚本 + 1 个空目录 | 人要求把后续重构方案落盘到工作目录，并清理废案以免干扰其它 agent

**新增方案文档**

`docs/ai-fde/09-并发重构方案.md`，约 380 行，分四个阶段：

| 阶段 | 内容 | 预估 | 验收 |
|---|---|---|---|
| 1 止血 | 三层限流（全局 20 / 每用户 2 / 每 IP 60次分）+ 429 + `Retry-After`；`/api/health` 改 async | 1 天 | 50 并发 P95 173s → <10s |
| 2 异步化 | `llm.py` 新增 `chat_async`/`stream_async` + 共享 `AsyncClient`；`kb_ask_stream` 改 `async def`；`hybrid_search` 走 `run_in_threadpool`；断开检测 | 2-3 天 | 50 并发首字 P50 127s → <10s |
| 3 解耦 | 新增 `app/backend.py`：`RetrievalBackend` 接口 + `LocalBM25Backend`/`MockBackend`，feature flag 切换 | 3-5 天 | eval 6 条不变 |
| 4 状态外置 | Redis/Postgres，多 worker 前置条件 | **暂不做** | 需服务器 |

方案依据：`docs/ai-fde/07-技术笔记-并发与RAG踩坑.md`（13 条实战结论）、
`03-目标架构与数据权限.md` 第 7 节、压测基线 `tools/loadtest/results-baseline.json`。

同时补充了今天本机直连真实网关的实测（文档里之前没有）：

| 并发 | p50 | 结果 |
|---|---|---|
| 8 | 2.0s | 全成功 |
| 30 | 2.0s | **3 条 429**（个人限流规则触发）|

结论：网关个人级限流是硬约束，代码优化绕不过，只能限流 + Key 池。

**清理废案**

删前都做过精确引用检查（`Select-String` 全仓搜索，排除 `app/data`、`__pycache__`、`.git`），
6 个文件全部**零引用**：

| 文件 | 大小 | 原用途 |
|---|---|---|
| `_concur.py` | 2.0 KB | 11-06 并发试验脚本，已被 `tools/loadtest/` 取代 |
| `_judge_test.py` | 3.7 KB | 答辩评分调试 |
| `_judge_vision.py` | 3.7 KB | 答辩视觉调试 |
| `_label.py` | 2.1 KB | 标注脚本（只被自己引用 `_label.json`） |
| `_verify_coach.py` | 6.1 KB | 答辩教练验证 |
| `_pip_test/` | 空目录 | 11-06 pip 测试残留 |

**未动的**：`health.log`、`server.out.log`、`server.err.log`（`.gitignore` 已忽略、未被跟踪，
且服务正在写入）；`__pycache__/`；`.dsh-office-*/`（工具目录）。

**INBOX 留言**：向 Claude Code 提出三件需配合的事（详见 `docs/agents/INBOX.md` 顶部）：
`kb.hybrid_search` 倒排索引、`RERANK_CONCURRENCY` 调参、向量 `mmap_mode="r"`。

**服务状态**：8000 端口 PID 10232 运行中，本次清理未触碰任何运行中文件的依赖。

### 11:14（11-12） | RAG-Agent | `docs/价值观星力量-案例素材.md`（新增） | 新增价值观宣传案例素材：给同事的钉钉回复话术、按官方卡片结构写的约 1300 字案例稿（案例背景 / 案例故事四轮 / 结果 / 价值观秘籍）、60 秒短片提纲、对外口径确认清单 | 人获得工程总部「AI 应用先锋奖」，价值观星力量同事来征集「创新成长」案例故事，需要能直接改用的稿件；客户统一脱敏为「某基金公司」

### 17:19（11-11） | Claude Code | AGENTS.md、docs/ai-fde/README.md | 记录 GitHub DNS/IP 绕过方法，并添加上脱敏作品集仓库链接 | github.com 解析到的 IP 443 超时、api.github.com 可达；用 http.curloptResolve 临时绕过，避免后续 agent 重复排查

### 17:08（11-11） | Claude Code | AGENTS.md、CLAUDE.md | 将 WSL2+Docker Engine、端口分工、本地开发、上服务器、公开作品集约定写入共享规则 | 让 RAG-Agent 和后续会话共享同一套开发/发布规则，避免 Docker Desktop 许可问题和误推客户资料

### 16:24（11-11） | Claude Code | docs/ai-fde/poc/install-docker-wsl.md、wslconfig.example、tools/local_dev/README.md | 增加 Windows 本机 WSL2+Docker Engine 安装步骤和资源限制模板 | 当前机器未装 WSL 且 shell 非管理员，Docker 需人工提权安装；优先 Docker Engine 规避 Docker Desktop 许可问题

### 16:23（11-11） | Claude Code | tools/local_dev/** | 增加本地演示的 Dockerfile、Compose 和迁移说明 | 本机与服务器统一容器化验证；Docker 未安装前仍可宿主机直接跑

### 16:19（11-11） | Claude Code | tools/local_dev/**、.gitignore | 新增本地假 RAG + 异步 BFF 演示（不依赖 Docker） | 服务器未就绪前先在本机验证适配层、SSE、引用和限流；公开作品集仓库因 GitHub 连接超时暂未推送

### 16:08（11-11） | Claude Code | docs/ai-fde/**、tools/ragflow_poc/** | 新增 FDE 全流程交付资料、RAGFlow PoC 验收/部署模板和环境自检脚本 | 为 100 并发改造、平台化选型和 FDE 成长沉淀可复用资产；压测基线显示单进程 100 并发 p95 315s，需外部检索引擎

### 15:39（11-11） | RAG-Agent | 没有改代码，只重新通读了现状（供后续接手）

人让"熟悉一下项目，我跟 claudecode 改了不少"。这一条只记读到的结论，避免下次又从零开始。

**跑着的东西**（本机实测）

- `app/server.py` PID 18104，监听 0.0.0.0:8000，`/api/health` 200，
  `/api/warmup` 4/4 预热完成，`/api/defense/stat` 记录 326 条、手册库 `20261108_174939_69c8b5` 在。
- Cloudflare 隧道在线（watchdog 14:15 拉起、14:17 在线）。
- 4 个知识库：业务手册 1834 块 / etf2.0 272 / 星桥演示 19 / 某基金公司 56426。
- 账号 3 个：管理员、某基金公司(1000)、演示账号 demo。

**没跑的**

- `tools/loadtest/` 压测是**在 8010 端口的测试实例**上跑的（mock 模型，不写 qa_log、不调视觉），
  结论只代表"线程池/队列"的瓶颈，不代表真实网关。基线数值见该目录 `results-baseline.json`。

**核对后确认没问题的**

- 本题的新需求（练习/答辩两模式、题数、选知识库、知识库多选只读共享）在后端和前端都在位：
  `defense_api.MODES/ALLOWED_N/new_session`、`server.py` 的 `/api/kb/{id}/share` 与
  `_KB_READONLY_PATHS`、`defense.html` 的模式卡/题数/kbSel/锁定提示、`index.html` 的分配弹窗。
- 答辩模块 5 个文件 `py_compile` 通过，`server.py` AST 通过。
- 评分折算公式复核：3 题时 raw 5/58/50 → 1.7/19.3/16.7，总分 37.7/100，与 10:34 条目记录一致。

**发现的不一致（只登记，未改）**

1. **CHANGELOG 11:12 条与代码不一致**：该条写"3 题报告满分 30（实测 8/30）"，
   但同一文件 10:34 条和现行代码 `defense_coach.PER_QUESTION=100` 都是**百分制**，
   `full` 固定 100。11:12 那条应是 10:34 改之前的旧记录。
2. **`AGENTS.md` / `CLAUDE.md` 的最后更新是 10-30**，落后于实际：
   - 文件归属表没有 `app/defense*.py`、`app/static/defense.html`、`app/users.py`、`tools/`；
   - "绝对不要碰"一节没提 11-04 之后新增的 `app/data/_imgcap`、`app/data/defense`；
   - 当前状态快照还写着"某资管公司待重建"，而 `registry.json` 里已经没有这个库了。
   建议下一次动这两个文件的人顺手更新。

### 10:15（11-11） | Claude Code | 阶段小结（无代码改动）

- **服务状态**：隐藏进程运行 `python -u app/server.py`，`/api/health` 200，预热 4/4；
  公网 `https://rag.example.com/` 可访问，Cloudflare 隧道在线。
- **本轮已登记改动**：见下方 09:59 至 11-09 各条（入口权限、答辩返回权限、参考答案预生成、
  百分制评分、结果页解析报告、多图联系表、出题重试、预热进度、图片索引缓存、隧道修复等）。
- **可选待办（未开工）**：手册截图早于入口权限弹窗，可择机重拍；多并发部署改造
  （异步 SSE、Redis/DB、共享存储、外部检索、Key 池）仅为评估建议，未改代码。
- 后续进度仍以本文件顶部条目为准。

### 09:59（11-11） | Claude Code | 修答辩页「返回问答」绕过知识库权限

人反馈：有答辩权限但没有知识库权限时，答辩页的「返回问答」能直接进主界面。

- `app/static/defense.html`：新增 `backToQa()`，先读 `/api/me` 的 `perms.kb`；
  有知识库权限才设 `rag.homePicked=1` 直达问答，没有就清掉标记回入口选择页。
  顶部「← 返回问答」和报告页「回到项目问答」都改用它。
- `app/static/index.html`：从入口选「模拟答辩」时不再设 `rag.homePicked=1`，
  由答辩页返回时按权限决定。
- 实测：
  - 只开答辩：返回后 URL `/`，入口弹窗显示，知识库卡片置灰；
  - 知识库+答辩：返回后直达问答，不再弹入口；
  - 无 JS 错误，测试账号已清理。

### 15:08（11-10） | Claude Code | 入口权限管理：知识库 / 模拟答辩，默认置灰

人要求：登录后显示两个入口，有权限才亮，默认置灰；管理员能管权限；
开通模拟答辩默认给 O32 业务知识库权限。

**用户模型**
- `app/users.py` 新增 `perms: {"kb": bool, "defense": bool}`、`perms_of()`、`set_perms()`；
  新账号默认都 false；老数据没有 `perms` 字段时默认 true（兼容）；`ensure_bootstrap`
  的旧主人账号显式 true。`_public()` 统一返回 perms。

**接口权限**
- 新增 `_require_defense` / `_require_kb_perm`，管理员直通。
- `/defense` 无答辩权限返回 403 页面；`/api/defense/upload|start|answer|reveal|report|analysis`
  全部 403；`/api/kb/{id}/ask` 与 `ask/stream` 无知识库权限 403。
- `PATCH /api/users/{uid}` 支持 `perms`；开通 defense 时自动把用户加入
  「业务手册（O32/资管培训）」只读共享（`_grant_manual_kb`）。

**界面**
- 登录后弹出「知识库 / 模拟答辩」两个入口卡片；无权限 opacity 0.45、点击提示；
  两权限都没有时提示联系管理员。
- 顶部「🎓 答辩演练」无权限时置灰不可点；知识库输入框也会禁用。
- 用户管理每行新增「知识库 / 模拟答辩」勾选框，管理员可随时开关；
  新建用户表单也有两个勾选框，默认都不勾。
- 实测：无权限 ask/defense 均 403；授权 defense 后页面 200 且手册自动共享；
  授权 kb 后 ask 200；入口卡片 opacity 0.45 → 1；测试账号已清理。
- 手册 2.4 / 10.2 / FAQ 同步；docx/PDF 已重新生成。

### 11:29（11-10） | Claude Code | 看参考答案改为后台预生成，点开直接复用

人问：出题时模型应该已经掌握材料，为什么看答案还要重新生成一遍。

- **`app/defense_api.py`**：`make_question()` 出完题后立即在后台线程调用
  `prefetch_explain()`，把标准答案/依据缓存到 `sess["explain"][index]`；
  `explain()` 发现该题还在生成时会 `Event.wait()`，等这次任务完成直接复用，
  **不会重复调模型**。
- 题目一出现就开始预生成，用户阅读/作答期间通常已算好；如果点得快，页面显示
  「已等待 N 秒」，等的是同一个后台任务。
- 单元验证（mock reveal 1 秒）：`explain()` 等 0.9 秒后拿到预生成答案；无重复调用。
- 手册 6.3 补充说明；docx/PDF 已重新生成。
- 服务已重启。

### 10:47（11-10） | Claude Code | 隐藏知识库源路径，顶部/侧栏只显示文件/块数

人反馈顶部那串 `E:\doc\...\交接材料` 把页面挤满。

- `app/static/index.html`：`select()` 顶部元信息改为「文件 X · 块 Y」；
  `renderKbs()` 侧栏改为「文件 X / 块 Y」；纯上传型才显示「纯上传型」。
- 源路径只保留在 `title` 悬停提示里，不再占排版。
- 实测顶部/侧栏均无 `E:\`，无 JS 错误。

### 10:34（11-10） | Claude Code | 预热/上传/等待进度 + 百分制评分 + 结果页解析报告

按人要求一次补齐：

**启动预热**
- `app/server.py` 新增 `_WARMUP` 状态 + `GET /api/warmup`（免登录），
  预热顺序业务手册优先；`preload_kbs` 逐库更新 done/current，失败记 errors。
- `app/kb.py` 增加每库建索引锁：预热和用户首次提问同时到达时只建一次，后到者等结果。
- `app/static/index.html` 顶部新增预热横幅：显示 `x/y` 和当前库，ready 后自动隐藏。
  实测 3/4 → 4/4 用时约 39 秒，横幅自动消失。

**答辩前端**
- PPT 上传改成 XHR，显示真实上传百分比；上传完成后切到“正在解析”。
- 出题/看参考答案显示“已等待 N 秒”，让用户知道不是卡死。
- 看参考答案失败时保留已有重试按钮；出题/提交失败的重试按钮已在前一条。

**评分改成百分制**
- 模型每题按 0-100 评分，报告按题数折算：3 题每题约 33.3，总分固定 100。
- 实测 3 题：raw 5/58/50 → 贡献 1.7/19.3/16.7，总分 **37.7/100**；等级按 100 分制。
- 逐题得分颜色阈值改为相对每题满分（80%/50%）。

**解析报告放到最后**
- 单题「看参考答案」不再点评“你刚才的回答”，只给标准答案、依据、关键得分点。
- 报告页新增「📝 生成解析报告」按钮，调 `POST /api/defense/analysis`，
  用整场问答一次生成逐题诊断 + 总体评价，结果缓存在会话里。
- 实测 3 题解析 26.6 秒，3 条逐题诊断正常。
- 手册 6.3 同步更新，docx/PDF 已重新生成。

### 09:56（11-10） | Claude Code | 答辩多图页：拼成带编号联系表再喂模型

人要求：单图用单图，多图拼成一张给模型；预处理要快但不影响内容。

- **`app/defense_coach.py`**：
  - 单图：去透明底 → 最长边 1200 → JPEG 85；
  - 多图：每张先缩放，拼成 2×2 / 2×3 联系表，带 1/2/3 编号，整体最长边 1600，JPEG 85；
  - 同页重复图去重、多图时丢 <40px 的小图标；单页最多 6 张。
  - 出题提示里会告诉模型“第 N 页有几张图，已按编号拼成一张，可指明图几”。
- **`app/defense_api.py`**：追问时 `focus_page` 取上一道题涉及的页，不再拿 PPT 前几页的图。
- 实测：
  - 3 图页联系表 1568×1228、base64 约 170KB；单图约 41KB；
  - 用 3 图页出题 22.7 秒，模型准确引用「第13页图3的日志」提问，说明编号可读；
  - 服务已重启，健康检查 200。

### 17:16（11-09） | Claude Code | 答辩出题加速 + 失败重试按钮

人问“没人在用怎么排队”，并要失败后能点重试。实测：

- 普通 `llm.chat` 1~5 秒，说明**不是网关排队**；
- 同一份 PPT 首题原图直传 + 长上下文时曾耗时 **114.6 秒**并返回空；
- `_ppt_image_blocks` 原来把原图（最大 4MB）直接塞给视觉模型。

改动：

- **`app/defense_coach.py`**：图片先缩到最长边 1200、转 JPEG（85），出题只带 1 张图。
  实测同题出题降到 **19.4 秒**，图片 base64 从原图降到约 13KB。
- **`app/static/defense.html`**：
  - 开始演练失败时显示「↻ 重试出题」按钮，点击重新调用 `startDefense()`，不用重新上传 PPT；
  - 答题后生成下一题失败时，回答自动回填到输入框，并显示「↻ 重试提交」按钮；
  - 两个按钮都用 Playwright mock 失败场景验证过，重试后能正常出下一题。
- **`app/defense_api.py`**：失败文案改为“模型思考超时或网关波动”，不再写“公共 Key 排队”。
- **手册 6.3**：补充两种失败的重试说明；docx/PDF 已重新生成。

### 17:06（11-09） | Claude Code | 修答辩出题“网关返回空内容”；手册补内网/VPN 地址

同事在 VPN 地址上答辩出题报「⚠ 生成问题失败（大模型没返回内容）」。

- **`app/llm.py`**：`chat()` 原来只对异常重试，网关 200 但 `content` 为空时直接返回空串。
  现在空响应也会重试 3 次；若 `stop_reason=max_tokens`，下一次自动把 max_tokens 翻倍（上限 8192）；
  最终失败会在 `server.err.log` 打出 stop_reason。
- **`app/defense_api.py`**：出题失败文案改为提示“公共 Key 在排队或网关波动，重试/填自己的 Key”。
- 复现：用同事那份 `O32总结-马意帆.pptx`（35 页）连续出题，均能正常返回；
  其中一次首题耗时 **114.6 秒**，说明当前公共 Key 时段网关确实很慢，建议填个人 Key。
- **手册**：2.1 增加访问地址表——办公室 `192.0.2.200:8000`、公司 VPN
  `192.0.2.164:8000`、外网域名、Tailscale 可选；重新生成 docx/PDF（18 页）。

### 16:39（11-09） | Claude Code | 图片扫描改按文档增量缓存（`imgindex.scan_images`）

人指出：`scan_images` 每次都解包全部文档算图片哈希，没变化也在重复扫。

- **`app/imgindex.py`**：`image_index.json` 新增 `doc_images`：
  `{文档相对路径: {sig, complete, images:[{name, hash, bytes}]}}`；
  `scan_images` 用「文件大小 + 纳秒 mtime + SCAN_VERSION」做单文档签名，
  签名没变直接复用清单，**不再解包**；只重扫新增/修改/上次没取完整的文档。
- `build` 把已加载的 `data` 传给 `scan_images`，复用同一份索引，避免重复读 15MB JSON。
- 实测业务手册（1225 张唯一图）：
  - 第一次全量扫描 **6.96s**；
  - 第二次 **0.13s**（复用 36 份文档，重扫 0 份）；
  - 旧逻辑每次全量扫描（此前冷态实测 66s）。
- 没改 `chunks.jsonl` / 向量库；`image_index.json` 增加约 160KB 文档清单。

### 16:25（11-09） | Claude Code | 排查「登入都卡」：公网走 Cloudflare LAX；轮询 8s→30s

实测：

- 本地 `127.0.0.1:8000` 登录页 37ms、`/api/auth/status` 7.5ms；
- 公网 `https://rag.example.com` 的 TTFB 1.3~2.1s、整页 3~5s；
  Cloudflare `cdn-cgi/trace` 显示 `colo=LAX`（洛杉矶），下行约 **20KB/s**；
- 内网 `192.0.2.200:8000` 7ms，Tailscale `100.64.0.10:8000` 31ms；
- 服务端 CPU 18%，没有图片索引任务在跑，不是后端慢。

原因：

1. 公网请求绕到 Cloudflare 洛杉矶，延迟/带宽都差；登录页 gzip 后 51KB，
   在 20KB/s 下光下载就要 2.5s，点登录再等一次往返。
2. 每个打开的前端标签页每 **8 秒**轮询一次 `/api/kb`，日志里能看到多 IP 高频轮询；
   多人同时在线会挤占同一条慢链路。
3. 有人通过公网上传 28MB 答辩 PPT 时，会把上行占满，其他人登录会更卡。

处理：

- `app/static/index.html` 的 `KB_POLL_MS` 从 8000 → **30000**，减少无效轮询；
- 建议办公室/公司网络优先用内网地址 `http://192.0.2.200:8000`，
  装了 Tailscale 的同事可用 `http://100.64.0.10:8000`；外网/手机再用 Cloudflare 域名。
- 大文件上传建议走内网/Tailscale，避免占满公网隧道。

### 14:27（11-09） | Claude Code | 修隧道守护每 2 分钟闪一下黑框（tasklist 子进程）

- 原因：`watchdog.py` 以 `pythonw` 无控制台运行，`tunnel_alive()` 调 `tasklist` 时
  没加 `CREATE_NO_WINDOW`，Windows 每 120 秒会新建一个控制台窗口 → 闪烁小黑框。
- 修复：加 `_NO_WINDOW = CREATE_NO_WINDOW`，`subprocess.run(["tasklist"...])` 带上；
  守护已重启（pythonw PID 54540），日志正常「守护进程启动 / 隧道状态: 在线」。
- 另两个启动项不是“每 2 分钟”的来源：
  - `RAG-cloudflared.bat`（启动文件夹）：只在登录时开一个 cmd 窗口等 90 秒；
  - `RAGServer_autostart.cmd`（RAGServer.lnk，最小化）：常驻一个最小化的 RAG-Server 窗口。
  如果也想消掉，可改成 VBS 隐藏启动，需要时再说。

### 13:29（11-09） | Claude Code | 新账号自动共享「业务手册」；Key 申请改到 demoproduct 平台

人要求：新账号默认能看到「业务手册（O32/资管培训）」；Key 申请说明改成
`http://demoproduct.examplecorp.com/ai_market/API`，具体注册见公司文档。

- **`app/server.py`**：新增 `_share_default_kb(user)`，在 `/api/register` 自助注册和
  `/api/users` 管理员建号后调用；只给普通用户，跳过库 owner/admin，失败只打日志不影响注册。
  加锁避免并发注册互相覆盖 shared_with。
- **`app/static/index.html`**：我的 Key 弹窗改为 demoproduct 平台申请入口
  （`http://demoproduct.examplecorp.com/ai_market/API`，新窗口打开），文案改为
  「只支持 demoproduct 平台签发的 Key」；验证失败提示同步。
- **手册**：2.4 改为「新账号自动获得业务手册只读权限」；3.2 改为 demoproduct 平台地址 +
  具体注册见公司文档；FAQ Q2/Q4 同步；第一章补充「新项目交接」场景和与 U+ 的分工
  （U+ 公司级 / 本系统项目级，可放环境信息、进度、经验，配合使用）；
  重新生成 docx + PDF（18 页 / 10 图 / 目录已更新）。
- 验证：自助注册新账号 → `/api/kb` 自动返回业务手册（readonly=true）；管理员建号同样生效；
  测试账号已删除、分享已收回。服务已重启（server.py 变更）。

### 12:45（11-09） | Claude Code | 操作手册升级为「应届生版 v2.0」（从注册 + 个人 Key 开始）

人要求：开放给应届生用，手册要从注册开始写，并说明大模型 Key 只有约 2 路并发、
用的人多了要各自申请 Key。

- 重写 `docs/项目智能问答助手-操作手册.md`（10 节 / 372 行）：
  - 二、注册：公司邮箱（默认只允许 `@examplecorp.com`）、密码至少 6 位、验证码/白名单两种模式
  - 三、**个人 Key（重点）**：公共 Key 约 2 路并发 → 申请 UIS 个人 Key → 填写/测试/保存；
    写清「只认公司网关 Key，外部厂商 Key 无效」和常见 401/429 处理
  - 四~八：主界面、提问/引用/追问、答辩演练、写内容、手机端
  - 九：应届生常见问题；十：给导师/管理员的分享、注册、运维附录
- 新增 10 张截图到 `docs/images/manual-v2/`：注册、登录、我的 Key、主界面、回答、引用、
  答辩演练、写内容、手机端、分配权限（临时应届生演示账号，已清理）
- 更新 `tools/md_to_docx.py` 的封面/页眉为「应届生使用手册 v2.0 · 2026-11-09」，
  重新生成 `docs/项目智能问答助手-操作手册.docx`（18 页，10 图，目录页码已用 Word 更新）
- 顺手修 `#llmBadge { white-space:nowrap }`：路径较长时模型名不再竖排
- 临时账号 `demo_newgrad` 与业务手册分享已删除/收回，users.json 恢复为
  `1000 / admin / demo` 三个账号

### 12:11（11-09） | Claude Code | 合并「分配归属」到多选权限，两个入口保持一致

人反馈：侧栏的「👤 分配归属」是单选，和「📤 分配知识库权限」的多选不一致。

改法（按人确认的方案）：**👤 合并为多选只读权限**。

- `app/static/index.html`：侧栏按钮由 `👤` 改为 `📤`，title「分配知识库权限（多选只读）」，
  点击调用 `openShare(kb.id)`，直接打开同一个多选弹窗，并预选当前知识库。
- `openShare(kbId)` 增加可选参数：优先用传入的库，其次当前选中的库，最后第一个。
- 删除前端单归属弹窗及 `openKbOwner/saveKbOwner/closeKbOwner`；
  知识库归属仍保留单人（编辑权仍归原 owner），只是不再暴露单独的单选入口。
- 后端 `/api/kb/{id}/owner` 保留，脚本仍可调用，不影响数据。

验证：4 个知识库都有 📤 入口；点第 1 个库预选正确；勾选 2 人显示
「已选 2 人（点「保存分配」生效）」；页面无 JS 错误。

### 11:32（11-09） | Claude Code | 修外网 530：重启 Cloudflare 隧道 + 修好守护自启 VBS

**症状**：`https://rag.example.com/` 返回 Cloudflare **530**（origin 不在线）；
本地 `127.0.0.1:8000/api/health` 正常，说明只是隧道断了。

**处理**：

- 确认 DNS 正常（解析到 Cloudflare IP），代理 `127.0.0.1:7890` 在监听，
  但 `cloudflared.exe` 进程不存在。
- 用代理环境重新拉起隧道（`tools/cloudflared/cloudflared.exe --config config.yml tunnel run`），
  当前 PID 34604；公网首页和 `/api/health` 均恢复 200。

**根因（守护为什么没救回来）**：

启动文件夹里的 `RAG-tunnel-watchdog.vbs` 有两个问题：

1. 文件不是 Unicode，中文路径被写成 `E:\doc\???????????\...`，根本找不到 `watchdog.py`；
2. `ws.Run` 那一行引号拼接错误（多了一个引号），`cscript` 直接报「语句未结束」。

所以 watchdog 从来没被成功拉起，cloudflared 夜里退出后外网就一直 530。

**修复**：

- VBS 改写为 UTF-16 LE，路径用 `Chr(34)` 拼接引号；
- 用 `cscript` 实测退出码 0，成功拉起 `pythonw watchdog.py`（当前 PID 51232），
  日志已记录「守护进程启动 / 隧道状态: 在线」。

**遗留建议**：

- `app/config.json` 的 `health.public_url` 还是旧的 Tailscale 地址
  `desktop-upqltd0.tail22c770.ts.net`，健康检查探不到 Cloudflare 域名。
  建议改成 `https://rag.example.com/`（共用配置，需先在 INBOX 报备）。
- 可用 `tasklist | findstr "cloudflared pythonw"` 快速确认隧道和守护都在。

### 11:20（11-09） | Claude Code | 修 OCR 通道（RGB→BGR）+ 窄条图补白（`ocr_extract.py`）

回 RAG-Agent 11-05 INBOX 时核对出来的：

- `ocr_pdf()` 用 PyMuPDF 拿的是 **RGB**，`ocr_docx_images()` 用 PIL 也是 **RGB**，
  但 `ocr_array()` 按 OpenCV 惯例要求 **BGR**，之前一直直接喂进去。
  已统一转 `img[:, :, ::-1]`（灰度先扩成三通道）。
- 新增 `_pad_narrow()`：宽高比低于 0.4 的窄条截图（如单行输入框）上下补白再 OCR。
  RAG-Agent 实测 1271×105 原图 0 字、补白后 47 字，放大 2~8 倍无效。
- 只改 `ocr_extract.py`，没动 `chunks.jsonl` / 向量；**没有重建索引**，下次重抽时生效。

### 11:12（11-09） | Claude Code | 答辩演练收尾：补齐权限分配前端 + 修 3 个 UI bug + 7 项界面验收通过

人交办「答辩演练收尾」清单。服务原先没在跑，已用隐藏窗口持久启动
（PID 26752，冷启动已完成，健康检查 200）。

**本轮涉及的功能（补登记）**

- **模式**：`defense_api.MODES` = `practice` / `exam`。练习答完当前题就能看答案；
  答辩要全部答完才看，`explain()` 在 exam 且未 done 时返回 `locked`（服务端校验，
  前端藏按钮不算数）。
- **题数**：`ALLOWED_N=(3,5,10,15,20)`，`new_session(..., total=...)` 落库；
  `/api/defense/start` 接收 `mode/total/kb_id`。
- **选库**：start 未传 `kb_id` 时用 `defense_coach.manual_kb_id()`
  （业务手册 `20261108_174939_69c8b5`）。
- **共享权限**：`app/users.py` 的 `shared_with` 白名单 + `can_read_kb()`；
  `/api/kb/{id}/share` 仅管理员可调用；普通用户只读，写接口返回 403；
  `server.py::_KB_READONLY_PATHS` 是只读接口白名单，新接口默认不放行。

**补前端（`app/static/index.html`）**

- 「📤 分配知识库权限」原来只有按钮和弹窗 HTML，**四个 JS 函数根本没实现**
  （`openShare/loadShareUsers/saveShare/shareSelAll`），点了直接 ReferenceError。
  已补齐：知识库下拉（带已分享人数）、用户多选、全选/清空、保存后刷新、只读说明。
- 修 `initAuth()`：带有效登录态刷新/返回主页面时不再停在登录遮罩
  （11-04 INBOX 报过的 bug，这次验收又撞到）。

**修答辩页（`app/static/defense.html`）**

- **最后一题没有「看参考答案」按钮**：`d.done` 分支直接出报告，最后一题的
  `revealRow` 没被填充。现在出报告前先给当前题补按钮。
- 知识库下拉段数一直是 0：字段名写成了 `k.chunks`，实际是 `k.chunk_count`。

**验收（`_ui_verify.py`，按交接要求已删除）**

Chrome headless 全流程，7 项全 PASS：

1. 主页面点「🎓 答辩演练」→ 新标签页打开、`sessionStorage` 拿到 token、不要求重新登录
2. 上传 PPT 后出现 2 个模式、题数 3/5/10/15/20、知识库默认「业务手册」且 id 正确
3. 练习模式答完第 1 题立刻出现「看参考答案」；3 条依据全部「✓ 手册原文可核对」
4. 答辩模式答完第 1 题显示锁定提示、无答案按钮；3 题答完后锁定提示全部变成按钮（3 个）
5. 3 题报告满分 30（实测 8/30），最后一题也能看答案
6. 管理员分配权限：多选某基金公司 → 保存成功 → 服务端记录正确
7. 普通用户 1000 能看到共享的「业务手册」，改名请求返回 403；测试后已收回分享

页面无 JS 错误。后端 `_t_share.py` 回归也全 PASS（分享/只读/403/收回）。

**清理**：按交接删除 `_t_*.py`、`_ui_*.py`、`shot_*.png`；`app/data/defense` 下的
测试上传 PPT 未动（属于数据目录，避免误删）。

### 21:10（11-08） | RAG-Agent | 新增「答辩演练」模块（新人上传答辩 PPT，AI 扮演评委连问 10 题，答完出报告）

人提的需求（原话）：「之前的那个答题的需求你开始做」「答错了或者学生想看正确答案也要有路径给他们答案，
并且还要给出依据，文件依据或者什么别的」。

**新增文件（都归 RAG-Agent，本次新建）**

| 文件 | 作用 |
|---|---|
| `app/defense.py` | 答辩记录库：导入 326 条历史答辩（问题/回答/导师点评），落 `data/defense/` |
| `app/defense_search.py` | 记录库上的 BM25 检索（字段加权：问题×3、点评×2、标签×2、回答×1） |
| `app/defense_coach.py` | 出题、追问、打分、**看参考答案+依据** 四个核心能力 |
| `app/defense_ppt.py` | 答辩 PPT 专用解析：按页提文字 + 抽图片，落到 `data/defense/ppt/<id>/` |
| `app/defense_api.py` | 演练会话管理（内存，6h 过期，上限 200 个） |
| `app/static/defense.html` | `/defense` 页面 |
| `tools/import_defense.py` | 合并两份 Excel 答辩记录进库 |
| `tools/build_manual_kb.py` | 建「业务手册（O32/资管培训）」知识库（41 份文档 / 1834 块） |

**`app/server.py`**：新增 `/defense` 页面与 6 个 `/api/defense/*` 接口；
`_run_blocking()` 把出题/评分丢线程池（一次十几秒，阻塞事件循环会把整站卡住）；
`/favicon.ico` 返回 204 消掉噪声 404。
**`app/static/index.html`**：顶栏加「🎓 答辩演练」入口。

**出题用三份数据**

1. 历史答辩库里**真评委问过的问题**（326 条，含导师点评）——定风格
2. 业务手册知识库（41 份 O32/资管培训文档）——定事实、给依据
3. 新人自己的答辩 PPT（文字 + 图片，模型能看见图）——定内容

**「看参考答案」给三样东西**：标准答案、依据（优先引手册原文）、历史里别人同类问题的答法与点评。
每条依据自动核对：文件名或原文片段能在检索结果里对上才标「✓ 手册原文可核对」，否则标「⚠ 未在手册中核对到」。
实测 12 条依据全部核实，没出现编造。

**踩过的坑（记下来免得重犯）**

- 追问会卡死在同一个点：试过关键词重叠、名词指纹、话题指纹，全不稳（同一页问不同方面，措辞差得远）。
  最后改成**按 PPT 页码计数**，同页问满 2 次强制换页，稳定生效。没页码时用实词重叠率兜底。
- 评分 JSON 被 `max_tokens`(1024) 截断，整份评分作废。评分调用单独放大到 3000，并加一次重试。
- 学员复读同一段话时继续追问没意义，加了 `_is_repeating()`（字符集相似度 ≥0.75）直接换题。
- PPT 解析用的是临时文件名，报告里显示成 `defense_xxxx.pptx`；解析完把 `meta.json` 改回用户原名。

**验证**：界面端到端跑通（上传 58 页 PPT → 10 题 → 看答案 3 条依据全核实 → 报告 12/100）。
打分端用真实历史数据测过：导师点评挑错的回答 42%，整体偏正面的 52%，档次能拉开。
低分偏多是测试脚本在复读/敷衍作答，不是打分器坏。

---

### 17:20（11-08） | RAG-Agent | 配图改页内弹层 + 修引用框重复 + Key 提示补公司网关说明

人提了三条（原话）：
1. 「对话中输出的图片，点击不要开启一个新的网页，而是像之前图片那样，展示出来，有个关闭的按钮关闭」
2. 「大模型key只支持公司的key要写清楚噢」
3. 「还有重试或者别的，有的时候下面会有2个引用窗口，旧的没消」

**① 配图点击不再开新窗口**

原来 `openFig()` / `previewAskImg()` 都是 `window.open('', '_blank')` 写一段 HTML 出去。
点几张图就开一堆标签页，回来看详情还要一个个关。

改成页内弹层 `#figViewer`：黑底铺满、图居中、右上角 × 关闭按钮、
底部留一个「在新窗口打开原图」的次要入口（有些图要放大看细节）。
关闭方式三种都行：点按钮、点背景、按 Esc。点图片本身不关（防止误触）。

**顺手修了一个隐患**：配图原来的 `onclick="openFig('${u}','${tip}')"` 把 url 和图片摘要
直接拼进 JS 字面量。图片摘要是模型生成的，**只要带一个单引号就会把属性截断**，点击直接失效。
现在改放 `data-img` / `data-title`，用事件委托接。
注意 url **不能**过 `esc()`——`esc` 会把 `&` 变成 `&amp;`，把 `?path=...&name=...` 弄坏。

**② 引用框重复（旧的不消失）——真 bug**

`renderRefs()` 每次 `box.appendChild(newDiv)`，从不清理。而一次回答里
`sources` 和 `answer` **两个事件都会带来源**，于是同一轮就出两个「引用 N 个片段」；
缓存命中路径更明显（`sources` + `answer` 都带）。点「重试」再来一遍，又堆一层。

修法：新增 `upsertChatBlock(node, owner)`，在 `.msg` 上打 `data-owner` 标记，
同一条回答的旧块就地换内容，历史消息的引用框不受影响。
另外把插入位置从「`#chat` 末尾」改成「紧跟这条回答的气泡」，顺序也对了。
（原来重试后引用框会跑到最新提问的下面去。）

**③ Key 只能填公司网关签发的**

弹窗里写清楚：**只支持公司大模型网关（UIS）签发的 Key，OpenAI / DeepSeek 官网 / 通义等
外部厂商的 Key 填进来无效**，地址从 `/api/my/llm` 返回的 `gateway` 字段动态填（前端不写死）。
验证失败时的提示也补上「常见原因：填了外部厂商的 Key」。

**实测**（Playwright + 真实 Chrome，管理员登录，先清答案缓存）

```
第一个问题        引用框 1 个
点「↻ 重试」后    引用框 1 个     ← 修之前会变 2 个
再问一个新问题    引用框 2 个，data-owner = ['o1','o2']   ← 分属两条回答，互不干扰
点配图            标签页 1 -> 1  ✅ 没开新窗口
                  弹层显示 ✅  关闭按钮 ✅  点关闭后隐藏 ✅
JS 错误           无
```

**已知未修（不是本次范围）**：**答案缓存命中时不推配图**。
`kb_ask_stream` L1964 缓存命中直接 `return`，不会跑 `bind_to_answer`，
所以同一个问题第二次问就没有图了。缓存键只有问题文本，当时是故意的（带图的问题本就不走缓存），
但"重复问就丢图"体验不一致。要修得把 figures 一起存进缓存。**已记在这里，等排期。**

---

### 16:15（11-06） | RAG-Agent | 每个用户可用自己的大模型 Key（解决网关排队）

人提：公司网关**每个 key 只有约 2 路并发**（见本文件「15:05」条实测），
大家都挤在 `config.json` 那一把 key 上互相排队。解法是**让同事各用自己的 key**，
各走各的通道。人确认的几条：只用默认 key 时界面要提示"你在排队、建议申请自己的"；
**全局大模型设置仅管理员可见**；**保存前必须验证 key 可用，验证不过不许存**。

**新增文件**

| 文件 | 作用 |
|---|---|
| `app/userllm.py` | 用户 key 的加密存取。`app/data/user_llm.json` + `.user_llm_secret` |
| `docs/需求方案-AI答辩质检.md` | （前一条建的，与本条无关） |

**改动**

| 文件 | 改了什么 |
|---|---|
| `app/llm.py` | `llm_settings` / `chat` / `stream` 加 `user_key` 参数，用户 key 优先于全局 key；新增 `verify_key()` 保存前验证，`_explain_err()` 把网关错误翻成 401/429/404/超时 人话 |
| `app/server.py` | 新增 `GET/POST /api/my/llm`、`POST /api/my/llm/verify`；新增 `_user_llm_key()` / `_cfg_with_user_key()`；`kb_ask` / `kb_ask_stream` 全部 LLM 调用带上用户 key（含 planner 改写拆解、fallback）；**401/timeout/429 的报错文案按"用自己的 key / 公共 key"分开** |
| `app/static/index.html` | 新增「🔑 我的大模型 Key」弹窗和顶部 `keyBadge` 徽标；「大模型设置」加 `id=llmCfgItem` 仅管理员可见 |
| `app/config.json` | 未改 |

**顺手修的安全漏洞**：`GET/POST /api/config/llm` 原来**完全没有权限校验**，
任何登录用户都能把全公司的 key 改掉。现在两者都要 `_require_admin`。

**为什么用标准库自己实现加密**：环境里没有 `cryptography`（实测 `ModuleNotFoundError`）。
用 HMAC-SHA256 计数器模式生成密钥流异或明文 + HMAC 完整性校验，格式 `c:base64`。
密钥单独存 `.user_llm_secret`（32 字节，chmod 600），和密文分开。
**加密失败直接抛异常，绝不降级成明文落盘。**

**实测**（走真实 HTTP + 真实登录）

```
初始状态           has_own=False（用公共 key）
假 key 验证        200 「key 无效或无权限（401/403）」
假 key 保存        400 被拒，has_own 仍为 False（没有落盘）
公共 key 提问      393 字正常
写入坏 key 后提问  「⚠️ 你自己填的大模型 Key 鉴权失败（401/403）…点右上角 🔑 我的 Key 换一把」
                   ← 证明确实改用了用户 key，没有偷偷回落公共 key
清除后提问         255 字正常（delta 流式 + done）
普通用户改全局配置 403「仅管理员可操作」
管理员读全局配置   200 has_key=True
加密往返           一致；密文不含明文片段；篡改后解密返回空串
```

**踩过的坑**：测试脚本超时被 kill 时 `clear_key` 没跑到，
坏 key 残留在 `user_llm.json` 里，导致下一轮"公共 key 也报 401"假象。
下次测这类有状态的功能，**清理要放在 `finally` 里**。

**给同事的用法**：登录后点右上角「👥 公共 Key」→ 粘贴自己的 key →「测试并保存」。
验证不过不保存。想改回去点「改回使用公共 Key」。

---

### 15:40（11-06） | RAG-Agent | 新需求立项：AI 辅助答辩质检（方案已定，代码未动）

人提了一个新需求：用历史答辩记录（评委问题 + 学员回答 + 导师点评）训练新人，
让 AI 模仿评委多轮提问并自动评分。**明确要求：先出方案、不动系统。**

方案写在 **`docs/需求方案-AI答辩质检.md`**（独立文档，内容较长不重复在这里）。
要点摘录：

- **新建独立数据类型** `app/data/defense/`，与现有知识库并存、互不干扰（人已确认）。
  不进 `chunks.jsonl`（结构不能改 + 答辩记录是结构化的，拆块会丢问题/回答/点评的关联）。
- 人**每月手动发一次**数据，先不做自动化。
- 业务知识/用户手册**按现有方法建知识库**，不改代码。
- 评分标准**由人找领导要**，不让 AI 凭空定。
- **唯一可能动核心检索的地方**：若要「答辩记录 + 业务手册」联合检索，得改 `kb.py`
  的单库检索。已在方案里标红等确认。

**风险记录**：网关约 2 路并发（见本文件「15:05」条），一次演练要 3~5 轮对话，
多人同时练会排队；换服务器解决不了。方案里建议做异步任务队列。

**下一步**：等人发数据 + 给评分标准。拿到先跑「第 0 步可行性验证」
（3 条真实记录让模型追问+打分，和导师点评对比），**准了再写业务代码**。

---

### 15:30（11-06） | RAG-Agent | 注册流程：修掉 3 个坑 + 加引导

人提：「创建用户整个流程，用户自己可以完成吗，引导做的怎么样」→ 查出来做得不行，人让改。

**发现的问题（按严重程度）**：

1. **注册后刷新页面就掉线（真 bug）**。`/api/register` 只 `set_cookie`，
   **没有把 token 回给前端**，而 `/api/login` 是回的。前端靠
   `sessionStorage['rag.token']` + `X-RAG-Token` 头工作，注册这条路拿不到 token，
   刷新即掉登录态，新用户会以为没注册成功。→ 补上 `"token": tk`。
2. **登录页写「用邀请码注册」，但系统根本没要求邀请码**（`register_require_code` 文件不存在）。
   同事会去找一个不存在的邀请码。→ 改成「注册新账号」。
3. **验证码那一行默认可见，但当前没配 SMTP**。用户点了「获取验证码」才知道系统没开邮件验证，
   输入框留在那儿不知道该不该填。原来只有点过之后才隐藏（`sendCode()` 里判断 `mode==='whitelist'`）。
   → 新增公开接口 `GET /api/register/config`，打开注册表单时就问一次，不配 SMTP 直接隐藏整行。

**顺带加的引导**：

- 注册页显示「用 @examplecorp.com 邮箱注册，邮箱就是账号」——省得同事拿 QQ 邮箱来试。
- 注册成功后插入一个三步上手提示（新建知识库 → 拖文档 → 提问），3 分钟后自动收起。
- 补 `AUTH_FREE`：新接口 `/api/register/config` 必须免登录，否则前端拿不到配置（第一次实测就 401，已修）。

**实测**（走真实 HTTP，已清理测试账号）：

```
GET  /api/register/config  -> 200  mode=whitelist domains=[examplecorp.com] require_code=False
POST /api/register         -> 200  ok=True，返回 token（长度 88）
GET  /api/me  (带 token)    -> 200  身份正确
GET  /api/kb  (新用户)      -> 知识库数量 0（符合预期）
POST /api/register (qq邮箱) -> 400  「只允许 @examplecorp.com 的邮箱注册」
```

**当前注册门槛**：不要邀请码、不发验证码，只要 `@examplecorp.com` 邮箱 + 6 位以上密码。
同事打开登录页点「注册新账号」即可自助完成。

---

### 15:05（11-06） | RAG-Agent | 实测记录：LLM 网关并发能力只有约 2 路

人提：「大家上传文件…大模型网关不允许并行吗」/「这个记录一下，网关只能并行 2 个，
后续优化需要我找领导反馈」。

**实测方法**：用 `llm.stream()` 同一句话（"用一句话说明什么是数据库索引"）连发 5 个请求。

**结果**：

| 请求 | 首字延迟 | 总耗时 | 状态 |
|---|---|---|---|
| 基线（单个） | 4.3s | 4.3s | OK |
| 并发 #1 | 0.8s | 0.9s | OK |
| 并发 #5 | 0.5s | 0.7s | OK |
| 并发 #2 | 18.1s | 18.7s | OK |
| 并发 #3 | 17.9s | 18.1s | OK |
| 并发 #4 | 17.9s | 18.1s | OK |

**结论**：

- **不会报错、不会限流**，5 个请求全部成功。
- 但延迟分布两极分化：**2 个立刻返回（<1s），3 个卡 18 秒**。
- 推断网关内部约 **2 个并发通道**，第 3 个起排队。
- 单个请求变慢 2.6 倍；串行需 56.5s，并发实际 18.7s（提速 3.0x，说明确实在并行，只是通道少）。

**对架构的影响（重要）**：

这是**网关侧的瓶颈，不是本机资源问题**。实测本机：服务内存 1.8GB / 31.3GB，
CPU 累计 4 分钟，数据 1.2GB。**换服务器、加内存都解决不了并发慢的问题。**

需要改善只能走网关侧（找领导反馈扩容），或者应用侧做排队提示。

**数据构成实测**（顺带记录，人问「文件不大吗」）：

某基金库 698.9MB = 图片 486.5MB（70%）+ 向量 110.2MB + chunks.jsonl 65.6MB
+ EMF/WMF 18.3MB + 元数据 18.3MB。**真正是索引的只有 65MB 文本 + 110MB 向量。**

---

### 14:05（11-05） | RAG-Agent | 答案配图：图片语义绑定（意思绑定 + 位置微调）

人提：「我想要的是**意思绑定**」/「ocr 为什么污染检索，答案很可能就在图里」/
「图可以缩小化展示，多个图是图集，跟在对应答案的段落后」/
「也可以依赖一点点（位置），权重给低一点」。

**新增 `app/imgindex.py`**（RAG-Agent 自有文件，只读 `ocr_extract`/`imgserve`/`docimg`/`llm`，
不碰 `embeddings.py`/`indexer.py`/`chunks.jsonl`）。离线建图索引，写
`app/data/<kb_id>/image_index.json`，与 `chunks.jsonl`、`vectors.npy` 完全隔离。

**本轮实测修掉的 5 个 bug（全部是跑起来才暴露的）**

1. **图片数量算错**：真实是 **4239 张**，不是之前以为的 1039。
   1039 只是 `chunks.jsonl` 里的占位标记数（`_marker_line` 每份文档最多标 4 张）。
2. **重复图极多**：按内容 md5 去重后 **1640 张**（重复率 61%）。
   同一手册的 4 个版本共享 375/368/271/270 张一模一样的图，去重省掉 2/3 工作量。
3. **OCR 通道顺序错**：`ocr_extract.ocr_array` 要 **BGR**，之前喂 PIL 的 RGB。
4. **窄条图 OCR 全返回 0 字**：文档里大量只截一行的图（如 1271x105 的输入框）。
   **放大 2~8 倍都没用**，上下**补白边**恢复宽高比后立刻正常（0 字 → 47 字）。
5. **`max_tokens=300` 被 thinking 吃光**：模型会先吐思维链再给答案，300 只够思考，
   `text` 块为空 —— 请求明明返回 **200** 却解析不到内容。我一度误判成"网关限流 429"
   （429 确实偶发出现），差点换成激进重试策略。提到 **1200** 后成功率 100%。

**索引结果**：1640 张 / 有内容 1541 / 无内容 99（logo、加载动画、装饰插画，判定正确）/
有摘要 1640（100%）/ 待处理 0 / 文件 3.0 MB。
kind 分布：截图 1173、配置 215、图标 85、表格 45、架构图 34、流程图 31、示意图 23、其他 23、照片 11。

**意思绑定怎么做**：答案按空行/编号拆段 → 每段与候选图摘要算余弦相似度（bge 512 维）→
超过 0.42 才挂，同段最多 3 张、整体最多 8 张。只拿本次命中文档里的图当候选。
匹配文本 = 摘要 + 关键词 + 类型（**不用 OCR 全文**，太长会把相似度稀释）。

**位置信号：只对 pptx 生效，且只在分数接近时打破平局**

- 实测 **docx 的位置标记是假的**：138 份含图 docx 里 **0 份**存在多种标记组合，
  91 份所有块标记完全相同，全是 `[image1..4.png]` —— 那是 `_marker_line` 无脑贴的
  "本文档前 4 张图"，与正文无关。**用了会误导，直接跳过 docx/xlsx。**
- **pptx 是真的**：18 块里有 17 种不同组合，按页标记。共 49 份 pptx 有可信位置。
- 一开始直接加 0.25 分，结果把语义 0.701 的「智能交易平台物理架构图」挤到第二，
  换成语义只有 0.564 的「COP系统架构图」排第一（它也在那个块里）——粒度太粗。
- 改成 `_POS_TIE = 0.06`：**只在语义分差 ≤0.06 时位置命中者优先**，语义差距大时完全不干扰。
  实测 `image9`（0.635，位置命中）只在追平 0.665 时上浮，0.701 的图稳居第一。

**`app/server.py`**
- 新增 `_figures_enabled()`：环境变量 `RAG_FIGURES=1` 或 `config.json` 的
  `"figures": {"enabled": true}`。**默认关闭**，关着时行为与以前完全一致。
- `/ask/stream` 在 `done` 之前多发一个 `figures` 事件：
  `{"blocks":[{para_index, para, images:[{hash,path,name,summary,kind,sim,pos}]}]}`。
  失败只打日志，绝不影响回答。

**`app/config.json`**：新增 `figures.enabled = true`（备份在 `config.json.bak-figures`）。
按约定这是共用文件，本次为功能开关，只加一个键、不动其他键值。

**`app/static/index.html`**
- `figures` 事件 → `attachFigures()` 按段落文字前缀找 DOM 块，把缩略图集插到对应段落后
  （`.figstrip`，132×88，`object-fit:contain`）→ 点击 `openFig()` 看大图。
- 没写进 `md()` 里，避免插入 HTML 破坏 markdown 渲染。

**端到端实测**（重启服务 + 打开开关后，账号 1000）

| 问题 | 耗时 | 答案 | 配图 | 效果 |
|---|---|---|---|---|
| O-order 部署时服务器怎么添加 | 28.8s | 744 字 | 8 张 | 段2「在天鉴中添加应用服务器」→「红框和序号标出进入计算资源管理-服务器并点击+添加」0.741 |
| 发布物怎么上传 | 26.5s | 835 字 | 8 张 | 段1「应用服务→发布物管理→发布物→添加」→「发布物添加弹窗，提示上传符合规范的ZIP包」0.746 |

图片接口 `/api/kb/{kb_id}/img` 200，62–98KB，`image/png`。

**踩坑记录（给后来人）**：这个服务的 SSE **不发 `event:` 行**，事件类型在 JSON 的
`type` 字段里。测试脚本若按 `event: xxx` 解析会一行都匹配不上，看起来像"服务没返回"。

**性能：图向量预计算，每问省 3 秒**

第一版每次回答都把 300 张候选图的摘要现场编码，**3.6 秒/问**——比检索本身还慢。
实测定位：`candidate_images` 0.001s、答案段落编码 0.03s，**瓶颈全在 `embed_texts(300条)` = 3.5 秒**。
图片是静态的，没必要每次重算：

- 新增 `build_vectors()` + `imgindex.py --vectors`：把每张图的摘要文本编码后存进索引的
  `vec` 字段。1640 张批量跑完只要 **18 秒**。
- `bind_to_answer` 优先取预存向量，缺的才实时编码。
- **3.6 秒 → 39 毫秒**（第 2 次起），提速约 90 倍。
- 代价：索引 3.0 MB → 14.6 MB（存了 1640×512 个 float）。值得。
- 顺带给 `load_index`/`_pos_index` 加了按 `mtime+size` 的缓存（`_pos_index` 要扫 5 万多行
  `chunks.jsonl`）。索引重建后自动失效，不用重启服务。

**索引更新后记得重跑** `python app/imgindex.py <kb_id> --vectors`，否则新图会退回实时编码。

**阈值收紧了（人确认「可以」）—— 但没用一刀切，改成"绝对下限 + 相对窗口"**

先用 6 条真实问题、168 个「段落-图」组合标定了一遍，发现**固定阈值怎么做都不对**：

- 固定 0.66：`「发布物怎么上传」第4段` 最高分只有 0.614，整段被误杀（那段其实有图）。
- 固定 0.42：强段落会被塞进一堆 0.5x 的跑题图。
- 根因：**同一条回答里各段最高分差异极大**。实测「O-order 添加服务器」各段最高
  0.736/0.751/0.788/0.845/0.742，而「发布物」是 0.644/**0.858**/0.780/0.614/0.655。

所以改成两条：

- `_ABS_FLOOR = 0.60`：绝对下限，再弱也不要（0.42 太松，实测 0.6x 已明显跑题）。
- `_REL_WINDOW = 0.10`：每段只收「本段最高分 - 0.10」以内的图，自动适配段落强弱。

标定数据（同一问题连跑 4 次、34 个段落）：最高分 min 0.559 / 中位 0.680 / max 0.776，
阈值 0.60 时 30/34 段能挂上图，被滤掉的 4 段（0.559~0.595）确实都是弱相关。

**顺手修掉的两个问题**

1. **总额度按段落数自适应**。原来固定 `max_total=8`，段落一多前面的段就吃光额度。
   实测 9 段时后 6 段全空，明明有 0.7+ 的高分图却一张挂不上。
   现在 `cap = min(max(8, 段数*2), 12)`，每段最多 2 张（原来 3 张，太多变图片墙）。
2. **文档没变就不重扫**。`scan_images` 要解包全部文档算图片哈希，1640 张的库空跑一次
   37 秒。新增 `docs_fingerprint()`：拿 `chunks.jsonl` 的 mtime+size 当指纹存进索引
   （`docs_fp` 字段），没变就直接跳过。

**自动触发（人要的）**

`POST /api/kb/{kb_id}/index` 重建完成后，后台线程 `_refresh_image_index()` 会自动：
① 判断文档是否变化（没变直接跳过）→ ② 增量跑 `imgindex.build()`（只处理新图）
→ ③ 有新图就补算 `build_vectors()`。加了 `threading.Lock` 防止同一库重复起任务，
失败只打日志不影响问答主流程。**以后加新文档不用再手工跑 `--vectors` 了。**

**排查记录：一次"0 张图"的假警报**

有个问题偶发出现 0 张图。查下来不是阈值问题 —— 每次都是那次回答本身的分段结果不同，
有的答案会拆出 9 段且前几段就耗尽额度。加了日志 `[figures] 未挂图 kb=... 答案N字 命中文档N份`
方便以后定位。连跑 4 次验证，每次都正常出图。

**还有一个小尾巴**：1640 张里有 88 张没有 OCR（其中 76 张是无内容的装饰图，
另外 12 张有摘要但没 OCR 文字），不影响检索和展示。

---

### 10:05（11-05） | RAG-Agent | 方案 C：提问时可以贴图（Ctrl+V / 选文件）

人提：「方案 C — 提问贴图，这个可以做」。要求：**不限张数**、图集点击展开、
历史对话只带文字不带图、当前这一轮该有图就有图。

**先说一个之前的误解**：我以为图片已经 OCR 过了、可以直接绑定。
查了 `index_docs.py` L126-133 才发现——**OCR 只在文档完全没有文字层时才触发**
（整份扫描件）。正常手册 docx 走 `if txt.strip(): return _join_with_images(...)`，
里面的图**从来没被读过**，`_marker_line` 只给每份文档前 4 张图留个 `[image1.png]` 占位符。
全库 434 块含图片标记、共 1039 处，图片内容全是空白的。

**这次做的是方案 C（提问贴图），和图片索引是两件独立的事。**

**后端**（`app/server.py`）：
- `AskIn` 加 `images: list = []`，格式 `[{"name","data":dataURL}]`，与 `NoteIn` 一致
- 新增 `_user_image_blocks()`：dataURL → Anthropic 原生 image 块。只收
  `data:image/{png,jpeg,gif,webp}`，总量超 12MB 截断并打日志，解析失败的跳过
- `_build_ask_context()` 加 `images` 参数：图块放在文字前，历史轮次**仍只传文字**
- 新增 `_IMG_HINT` system 提示：先看懂图、文档只作补充、看不清要直说、敏感信息按原文给出
- **只贴图不打字**给了默认问题，不再报「问题不能为空」
- **有图时跳过缓存读和写**（缓存键只有问题文本，同一句话配不同图会拿到上次答案）
- 有图时跳过寒暄/泛问两条短路

**前端**（`app/static/index.html`）：
- 输入栏加 🖼 按钮 + 隐藏 file input；输入框上方新增 `#askImgs` 缩略图条
- `compressImage()`：canvas 缩到宽 ≤1400、JPEG q=0.82，体积约降到 1/3
- `addAskImages()`：预算 11MB（给服务端 12MB 留余量），超了提示「有几张没加上」
- 粘贴监听只拦 `#q` 上的图片粘贴，纯文本照常
- 缩略图 `object-fit:contain`（cover 会把报错截图中段切掉，认不出是哪张）
- 发送后清空输入区；用户气泡里保留缩略图
- 切库 / 清空上文时一并清掉待发图

**实测**：

| 用例 | 结果 |
|---|---|
| 贴图 + 文字问题 | 15.6s，准确读出 `ERROR 1045`、`'demo'@'192.0.2.41'`、`192.0.2.55:6379`，并主动说明「文档中未找到相关信息」 |
| 只发图不发文字 | 21.5s，正常分析，不再被拒 |
| 浏览器端到端 | 13s，回答里标注「以图为准」，输入区自动清空、气泡保留缩略图 |
| 12 张图 | 9.3s，全部识别 |

模型侧先测过：网关只接受 Anthropic 原生 `image` 块，**不接受** OpenAI 的
`image_url` 格式（会报 `Input should be a valid string`）。

`py_compile` 与 `node --check` 均通过。

### 15:10 | RAG-Agent | 支持同一浏览器多标签页登录不同账号（token 改走 sessionStorage + 请求头）

人提：「可以一个浏览器开 2 个用户吗」→ 讨论后确认要改。

**原来的限制**：身份只存在 cookie `rag_token` 里，而 cookie 是整个浏览器共享的，
第二个标签页登录会把第一个顶掉（浏览器行为，不是 bug）。

**改法**：

1. **后端 `_token_of` 改成请求头优先**：`X-RAG-Token` 优先于 cookie。
   这是关键 —— 不改的话，哪怕前端带了正确 token，服务端还是会用共享 cookie 里的那个身份。
2. **登录响应多回一个 `token` 字段**（原来只写 HttpOnly cookie，JS 读不到），
   前端拿到后存进 `sessionStorage`（每个标签页独立）。
3. **前端新增 `withAuth(opt)`**，把本标签页 token 合进请求头；
   **所有 8 处 `fetch` 全部改用它**（流式提问、索引触发、列表轮询、登出等）。
   校验脚本会检查"未带 token 的 fetch 数 = 0"。
4. **`/api/logout` 不再删 cookie**：cookie 是共享的，删了会把其他标签页一起踢下线。
   本标签页登出只需清自己的 sessionStorage。
5. `purgeLocalAuth()` 同样只清 sessionStorage，不再动 cookie。
6. `initAuth()` 探测身份时也带 token，刷新页面不会串成另一个账号。

**端到端实测**（同一无头浏览器开两个标签页，**共享同一个 cookie**）：

| | 标签页 A | 标签页 B |
|---|---|---|
| 登录 | admin | 1000（A 登录后 B 再登录，故意制造覆盖） |
| 界面身份 | 👤 管理员（管理员） | 👤 某基金公司 |
| 看到的知识库 | etf2.0 / 演示库 / 某基金公司项目（3 个） | etf2.0 / 某基金公司项目（2 个） |
| **后端实际判定** | `is_admin=True`，3 个库 | `is_admin=False`，2 个库 |

B 登录之后回到 A，**A 依然是管理员身份、依然看到 3 个库**，没有被顶掉。
`node --check` 与 `py_compile` 均通过。

**行为变化（要告诉使用者）**：
- 关掉标签页 = 该标签页登出，重开需重新登录（sessionStorage 的生命周期）
- 新开标签页会继承 cookie 里最近一次登录的账号，符合直觉
- 老会话（只有 cookie 没有 sessionStorage）仍然可用，自动兼容

### 14:55 | RAG-Agent | 知识库列表自动同步：管理员转移后对方免刷新即时可见

人提：「能不能不要点刷新……管理员把库归另一个人了，他的界面应该直接出来」。

**诊断**：除了索引过程中那一小段轮询，前端**没有任何后台刷新**，
知识库列表只在页面加载或自己动手时才更新。所以对方必须手动刷新/重开才看得到。

**做法**（`app/static/index.html`，纯前端）：

1. **轻量轮询**：`startKbPoll()` 每 8 秒拉一次 `/api/kb`。三个约束避免把服务打满：
   - `document.hidden` 时不发请求（切走的标签页不刷）
   - 正在上传/建索引（`uploadBusy`）或归属弹窗开着时不打扰
   - 列表**没变化就不重绘** —— 新增 `kbsFingerprint()`，比较
     `id/name/status/chunk_count/file_count/owner/owner_name/mine` 的指纹，
     指纹相同直接跳过 `renderKbs()`，避免列表闪烁和鼠标悬停态丢失
2. **切回标签页立刻核对**：`visibilitychange` 监听，从别的窗口切回来时
   马上 `loadKbs()` 一次并重启轮询，不用等下一个 8 秒周期
3. **`cache:'no-store'`**：确保每次拿到服务端最新归属，不被浏览器缓存挡住
4. **当前库被转走时自动收尾**：`loadKbs()` 里发现 `cur` 已不在列表里，
   自动清空选中态、禁用输入框，并提示「你正在看的知识库已被管理员转给其他用户，已自动关闭」
5. 登出/登录失败走 `purgeLocalAuth()`，顺带 `stopKbPoll()` 并把指纹重置

**端到端实测**（无头 Edge 开着 1000 的页面不动，另用接口模拟管理员操作）：

| 步骤 | 结果 |
|---|---|
| 1000 页面初始 | `['某基金公司项目']` |
| 管理员把 etf2.0 转给 1000（页面不做任何操作） | **第 5 秒**自动变成 `['etf2.0', '某基金公司项目']` |
| 管理员把 etf2.0 收回（页面仍不动） | **第 8 秒**自动变回 `['某基金公司项目']` |

全程没有点刷新、没有重开页面。`node --check` 通过。

### 14:40 | RAG-Agent | 新增「管理员分配知识库归属」+ 修登录失败残留旧身份的安全隐患

人提两件事：①「管理员可以控制知识库归谁，给谁开放」；②「我创建库后，所有库都展示在
某基金公司那个账号下了，有安全风险」。

**先说第 ②：查清了，不是越权，但真有一个安全坑。**

跑遍三个账号实测，数据隔离本身是好的：

| 账号 | 能列出的库 |
|---|---|
| `1000` | 某基金公司项目、项目3（自己的） |
| `demo` | 星桥交易平台（演示库）（自己的） |
| `admin` | 全部 3 个 |

`1000` 访问别人的库，`/api/kb/{id}`、`/debug_search`、`/notes`、`/file/download`
**全部 403**。拦截点在 `auth_gate` 中间件（`server.py` L98-104），统一校验归属，
不会漏接口。

**真正的坑**：cookie `rag_token` 是整域名共享的，而登录失败时**不清旧 token**。
于是「先登 admin → 想切到 1000 → 但 1000 那次登录失败（密码错/网络抖）」
浏览器里 admin 的身份仍然有效，左侧照样列出全部库 —— 看起来就像「1000 看到了别人的库」。
实测复现：失败登录后仍 `is_admin=True`、列出全部库。

**修法（两端都堵）**：
- 后端 `login()` 失败分支加 `resp.delete_cookie(...)`，密码错就顺手把旧 token 清掉
- 前端 `doLogin()` 失败分支调新增的 `hardLogoutLocal()`，并抽出 `purgeLocalAuth()`
  统一清 `AUTH.user` / `window.__isAdmin` / `kbs` / `cur` / cookie / 界面
- `doLogout()` 也改用 `purgeLocalAuth()`，保证退出干净

**第 ①：新增归属转移。**

- 后端 `POST /api/kb/{kb_id}/owner`（仅管理员），body `{"owner": "<用户id>"}`，
  传空串 = 收回到管理员自己名下。目标用户不存在返回 400，库不存在 404。
- 前端知识库列表每个库 hover 出 **👤** 按钮（仅管理员可见），点开弹窗选目标用户，
  确认后立即转移。
- 弹窗里写清了规则：转给某人后**只有他和管理员**能访问，原主人立刻失去访问权。

**实测（etf2.0 来回转三次）**：

| 操作 | 1000 | demo | admin |
|---|---|---|---|
| 转移前（admin 名下） | 403 | 403 | 200 |
| 转给 1000 | **200 + 列表出现** | 403 | 200（管理员始终可访问） |
| 转给 demo | **403 + 列表消失** | **200 + 列表出现** | — |
| 收回给 admin | 403 | **403 + 列表消失** | 200 |

权限切换是**即时**的，不需要重新登录。普通用户尝试转移被拦（403），
非法目标用户返回「目标用户不存在」。

**顺带**：`项目3` 这个空库（0 文件 0 块）已不在注册表里，应该是人在界面上删的，
没有数据损失。诊断用的临时库也都清理干净了。

### 14:20 | RAG-Agent | 管理员「运行总览」弹窗重排：改标签页 + 字号放大

人反馈：「排版有点问题，有的布局太小了完全看不清」。

**根因**：上一版把「运行总览」和「问答统计」硬塞进同一个 940px 弹窗里，
两排卡片 + 6 个列表区块竖着堆，每块被压到 150px 高，字号只有 11.5~12.5px，
弹窗 `max-height:80vh` 一超就整页滚动 —— 确实看不清。

**改法**（`app/static/index.html`，纯前端，刷新即生效）：

1. **拆成 3 个标签页**：「系统与知识库 / 问答统计 / 明细」，每页只放自己的内容。
   新增 `switchQaTab()` + `.qtabs/.qtab` 样式，默认停在总览页。
2. **字号全面上调**：列表 12.5→13.5px、明细 14px、卡片数字 19→22px、
   标题 12.5→13.5px、状态行 12→13px；列表项内边距 6→9px，行高 1.5。
3. **弹窗加宽** 940→1080px。
4. **滚动条改成悬停才显示**（`scrollbar-color`/`::-webkit-scrollbar-thumb`），
   之前那条灰竖条一直杵在列表右侧很碍眼。
5. **明细页吃满高度** `max-height:max(260px, calc(72vh - 190px))`，
   之前底部被硬截断只露半行。
6. `.qalist .qd` 加 `white-space:nowrap`，右侧统计列不再被挤成两行。

**顺带修**：`ovLine` 原来是 JS 动态 `createElement` 插进去的，每次刷新都先删再插；
现在改成 HTML 里固定元素，JS 只填 `innerHTML`。

**验证**：用 `tools/shot_cdp.py` 同款 Edge+CDP 机制截图 3 页，逐页目检 ——
4 张大卡片、知识库列表（某基金 56,426 块 / 演示库 19 块 / 项目3 未建索引）、
高频问题双栏、明细 11 条一屏，字全部清晰可读。`node --check` 通过。

### 13:40 | RAG-Agent | 六项改造：闲聊短路 / 泛问反问 / 超时 90s / 答案长度 / OCR / 评测集 20 条 / 管理员总览

人一次交办 6 件事，全部落地并逐项实测。

**1. 闲聊短路**（`app/server.py`）
`_CHITCHAT_RE` + `_is_chitchat()`，命中就分档回一句（谢谢/再见/好的/你好各不同，
不是一律回「你好」），**不检索、不调模型**。`/ask` 与 `/ask/stream` 口径一致。
实测「你好」「谢谢」「在吗？」从 12s 降到 **0.7~1.0s**。

**2. 泛问先反问**（`app/server.py`）
`_GENERIC_ASK` + `_is_generic_ask()`：只问「密码」「账号」「数据库地址」这种孤立名词时，
不检索，直接反问「哪个系统 / 哪个环境 / 要账号还是密码」，并给出提问示范。
实测「密码」从 22.4s/831 字（当时把整套口令铺出来了）降到 **0.8s 一句反问**。
具体问题（如「demo的密码是多少」「测试环境的数据库账号密码」）**不会被误拦**。

**3. 超时 180 → 90 秒**（`app/config.json`，已提前在 INBOX 报备）
`llm.total_timeout` 90，`read_timeout` 保持 30。改前备份 `config.json.bak-20261104-132019`。

**4. 答案长度可选**（`server.py` + `index.html`）
`AskIn.answer_len`（brief/normal/detail）+ `_LEN_HINT` 注入 system 提示词；
输入框旁新增「📏 简答/标准/详细」循环按钮，状态记在 localStorage。
实测同一问题：brief **114 字**一段话 / normal **452 字**分点 —— 区别明显。

**5. OCR：根因是依赖没装，不是没代码**
`ocr_extract.py` 早在 10-13 就写好了，`index_docs.extract_pdf` / `extract_docx`
也已有兜底调用，但 `rapidocr_onnxruntime` 一直没装，`_engine()` 抛 ImportError 被
`except` 静默吞掉 → 扫描件全部抽出空文本。装上 `rapidocr_onnxruntime 1.2.3`
（连带 `opencv-python` / `Shapely` / `pyclipper`）后：

- 单图识别：中文、IP、账号密码全对，**1.2 秒/张**
- 端到端：造了一页无文本层 PDF，`extract_pdf` 正确抽出「运维巡检记录 / 203.0.113.50:3306 / Redis / 扩容」

**6. 评测集 6 → 20 条**（`eval/xinyuan_cases_v2.json`，**草稿，等人审**）
原 6 条保留原 id，新增 xy-07~xy-20：项目批次/里程碑/周报进展、HIX 数据流、
安装包清单、端口、回滚、数据库账号、测试 vs 生产、登录排查、卡顿排查、日志目录，
外加 3 条**边界题**（xy-19 天气、xy-20 薪资）验证不瞎编。
`run_eval.py` 同步支持 `max_len`（防止该短答的长篇大论），并修好登录方式
（原来还用已作废的 key，会 401）。

**7. 管理员总览**（`GET /api/admin/overview` + 问答统计弹窗顶部区块）
一屏看：运行时长、大模型/OCR/超时/模型名各状态、账号数、今日问答数与活跃人数、
今日均耗时、失败兜底数、知识库列表（文件数/块数/向量数/归属/索引中/待重载）。
同时修了 registry 字段名（是 `chunk_count`/`file_count`，不是 `stats.total_chunks`，
否则显示 None）。

**重要更正**：之前我在 CHANGELOG 说「`_notes` 里明文口令所有登录用户都能搜到」——**错了**。
实测 `demo` 账号访问某基金库 `/api/kb|ask|debug_search|notes` 四条路径**全部 403**，
越权拦截是好的。真实暴露面只是「能访问该库的人」（当前 1000 与 admin）。已同步更正 INBOX。

### 12:50 | RAG-Agent | 新增对外演示账号 `demo` + 虚构演示库（`demo/demo_kb_docs/`）

人要求：给一个能给别人试用的测试账号，内容要有点东西但**没有安全合规风险**。

做法：

- 新增 `demo/demo_kb_docs/` 7 篇 markdown（约 14KB），全部是虚构资料：
  公司「示例金融科技」、系统「星桥交易平台 SBTP」、IP 一律用 RFC 5737 文档网段
  （192.0.2.x / 198.51.100.x / 203.0.113.x）、账号密码都是 `Demo#xxx` 这种一眼假的
- 建账号 `demo`（角色 user，名字「演示账号（可对外）」），只拥有 1 个演示库
- 建库「星桥交易平台（演示库）」`20261104_124155_58e87b`，7 文件 / 19 块
- 内容覆盖：项目背景、系统架构、环境与部署、上线流程、FAQ、新手上手、账号速查

写文档时踩到的一个真问题（顺带记下）：**表格版账号速查表召回很差**——表格行会被切碎，
"测试环境的数据库账号密码"问出来，排第一的居然是无关的 FAQ 块。把表格改成
「每个环境一段话、句子自带关键词」之后，同一问题正确块进入前 4。演示库文档最好按散文写。

**注意**：这台机器现在连的是家里的 Wi-Fi，`demoproduct-uis.examplecorp.com` 是内网域名，
公网 DNS 解析不到，所以此刻生成不了答案（检索、引用、rerank 都正常，只有生成这一步走不通）。
回到公司网络即可，代码没问题。

### 10:55 | RAG-Agent | 注册改邮箱（域名白名单 + 验证码待 SMTP）；1000 密码改 xhy200326

人要求：`a1` 改成 `xhy200326`，注册改成"邮箱验证码"。先问过要不要 SMTP，人说没有，
所以按"邮箱白名单"落地，同时把验证码链路完整写好，等有 SMTP 直接生效。

改动：

- **账号**：`1000` 密码 `a1` → `xhy200326`（`users.set_password`，旧密码已失效）
- **新增 `app/mailer.py`**（RAG-Agent 自建）：SMTP 发信 + 内存验证码（6 位、10 分钟、
  错 5 次作废）。配置放 `app/data/smtp.json`；**没配就是 `configured()==False`**，
  注册自动退化成邮箱域名白名单
- **`app/server.py`**：
  - `POST /api/register/send_code`（免登录，已加进 `AUTH_FREE`）：发码；没配 SMTP 时
    返回 `{"mode":"whitelist"}`，前端据此隐藏验证码行
  - `POST /api/register`：邮箱即账号（`@` 已加入账号允许字符），先校验邮箱格式 + 域名
    白名单（默认 `examplecorp.com`，存 `app/data/email_domains.txt`）；配了 SMTP 才校验验证码；
    密码至少 6 位
  - 管理员：`GET/POST /api/register/smtp` 配置发件服务器、`GET/POST /api/register/domains`
    改允许域名
  - `users.create_account` 字符白名单加 `@`（原来会把邮箱判成非法账号）
- **`app/static/index.html`**：注册卡改为「公司邮箱 + [获取验证码] + 密码」；
  点获取时若服务端是白名单模式，自动收起验证码行并提示，不误导

实测：

```
[1] 1000 / xhy200326 -> 200        [1b] 1000 / a1 -> 401
[2] 发码 examplecorp.com -> mode=whitelist（没配 SMTP）
[3] 发码 qq.com      -> 400 只允许 @examplecorp.com
[4] 邮箱注册         -> 200，可见库 0（隔离正常）
[5] 重复注册         -> 400 这个账号已经被占用了
[6] 邮箱登录         -> 200，大小写不敏感
```

**待办**：有 SMTP 后填 `app/data/smtp.json`（或 POST /api/register/smtp），验证码即生效，
不用改代码。

### 11:20 | Claude Code | 新增操作手册 + 界面截图工具（`docs/`、`tools/`）

人要求写一份面向交付/实施人员的操作文档，含问题背景、解决的问题、操作指南（配截图）、
后续完善与推广方向。

- 新增 `docs/项目智能问答助手-操作手册.md`：十节，含项目背景、解决的痛点、登录/选库/提问/
  引用/追问/兜底/知识库维护/管理员功能/FAQ/推广路线与运维命令。
- 新增 `docs/项目智能问答助手-操作手册.docx`：Word 版（A4、封面、自动目录、页眉页脚、
  10 张配图、表格排版），已用本机 Word 更新目录页码并核对版面。
- 新增 `tools/md_to_docx.py`：Markdown → Word 转换脚本（不依赖 pandoc/docx-js，
  用 python-docx + Pillow 生成），后续手册更新可直接重跑。
- 新增 `docs/images/manual/*.png`：10 张真实界面截图（登录、主界面、回答、引用、内置文档、
  写内容、新建知识库、未命中兜底、手机端）。截图基于临时演示库，不含客户资料。
- 新增 `tools/shot_setup.py` / `tools/shot_cdp.py`：用邀请码建临时演示库 + Edge 无头模式
  CDP 截图，方便以后界面改版后重拍。
- 截图完成后已删除临时账号 `demo_shot` 和演示库 `20261104_102250_ae7d7e`，
  `users.json` / `registry.json` 已恢复原状；`app/data/` 其他内容未动。
- 本次未修改 `app/embeddings.py`、`app/indexer.py`、`build_vectors.py` 的逻辑。

另：截图时发现「带有效登录态刷新页面，登录遮罩不消失」的前端 bug，已写入 INBOX 交办
RAG-Agent（未改对方文件）。同时回复了对方 10-30 的四个问题（建向量触发时机、文件清单、
caption 链路、`kb.py` 接口变化）。

### 10:20 | RAG-Agent | 登录改账号密码 + 自助注册（邀请码）；某基金库账号 1000 / a1

人要求：把"输一串 key"改成正经的登录注册，按用户区分；并指定原来的某基金公司那个账号为
`1000`，密码 `a1`。

改动：

- **`app/users.py`**：新增 `username` 字段与 `find_by_username / verify_password /
  create_account / set_password / set_username`。账号唯一（不分大小写），只允许
  字母数字和 `_ - .`；密码沿用原来的 pbkdf2(salt) 存储，**不存明文**
- **`app/server.py`**：
  - `LoginIn` 增加 `username`；有账号走账号+密码，没账号仍可用一串 key 登录（兼容脚本）
  - 新增 `RegisterIn` + `POST /api/register`（要邀请码）、`GET/POST /api/register/code`
    （管理员看/换邀请码）
  - 邀请码存在 `app/data/register_code.txt`，首次自动生成 6 位随机值。
    **公网可达 + 库里全是客户账号密码，所以不敢开放无门槛注册**
  - `AUTH_FREE` 增加 `/api/register`（否则中间件先拦成 401）
  - `/api/users` 创建支持 `username/password`；`PATCH` 支持 `password`（重置）和 `username`（改名）
- **`app/static/index.html`**：登录卡改成「账号 + 密码」，下面挂「用邀请码注册」，
  注册成功即登录；用户管理弹窗改为账号/姓名/角色/密码，列表显示 `@账号`，
  按钮从「重置 key」改成「重置密码」，顶部显示并可轮换注册邀请码
- **账号迁移**（一次性，脚本内联执行，备份 `app/data/_accounts_backup_20261104-100846.json`）：
  - 某基金公司：账号 `1000` / 密码 `a1`（角色 user，名下 1 个知识库）
  - 管理员：账号 `admin` / 密码 `Admin49d07c66`

实测：

```
[1] 1000/a1        -> ok 某基金公司 user，能看到 1 个库
[2] 1000/错密码     -> 401 账号或密码不对
[3] admin 登录      -> ok，用户列表 [('1000','user'), ('admin','admin')]
[4] 错邀请码注册    -> 403 邀请码不对
[5] 正确邀请码注册  -> ok，自动登录，能看到的库：0（隔离正常）
[6] 重复账号注册    -> 400 这个账号已经被占用了
```

**提醒**：`a1` 这种两位密码在公网等于没有，演示完建议在「用户管理」里重置。

### 15:40 | RAG-Agent | 知识库没命中不再自动联网/自动编，改成先问用户

人要求：库里没有就别自作主张跳网搜或拿模型知识顶上，先问一句，用户点了再去；
用户说不用，就直接告诉他没找到。

一共有**四处**会触发兜底，全部改成"先问"：

| 位置 | 触发条件 |
|---|---|
| `kb_ask` 检索无结果 | 检索结果为空或低于 `min_hits` |
| `kb_ask` 答完判定没找到 | 模型回答命中 `_NOT_FOUND_MARKS` |
| `kb_ask_stream` 检索无结果 | 同上 |
| `kb_ask_stream` 答完判定没找到 | 同上（这是实际最常走到的：库里有片段但答不出） |

改动：

- **`app/fallback.py`**：`load_fb_cfg()` 增加 `ask_first`（**代码里默认 True**，没动 `config.json`）；
  新增 `ask_first(cfg)`、`offer_options(cfg)`；`prepare_fallback(q, mode)` 支持
  `auto / web / llm`，`answer_fallback(q, mode)` 同步
- **`app/server.py`**：`AskIn` 增加 `fallback_mode`（`""` / `web` / `llm` / `none`）；
  四处兜底前先发 SSE `offer` 事件（问题原文 + 两个来源选项 + 「不用了」+ 免责说明），
  用户选了才真跑；`none` 直接回"没找到"
- **`app/static/index.html`**：新增 `offer` 事件渲染——知识库答案正常显示，下面挂一个选择框
  （🌐 联网搜一下 / 🧠 用大模型自己的知识回答 / 不用了）；点来源才发第二次请求
  （`ask(q, msg, mode)` 带上 `fallback_mode`）；点「不用了」纯本地处理，不发请求

**开关**：在 `app/config.json` 的 `fallback` 里加 `"ask_first": false` 就退回老的自动兜底
（默认不开，没改配置）。这一条只影响行为默认值，不需要动共用配置。

实测：

```
[1] 未命中提问 9.0s   事件=[status…sources…offer]，选项=[联网搜一下, 用大模型自己的知识回答, 不用了]
                       是否自动调用模型：False        ← 关键：不再自动跑
[2] 点「大模型知识」17.4s  label=大模型知识，正常出答案
[3] 点「不用了」前端本地替换文案，不发请求
```

踩的坑：`fb_mode == "none"` 排在 `if fb_mode not in ("web","llm") and ask_first` 后面，
"none" 也满足那个条件，于是反复弹选项。把 none 加进排除列表才对。

另：验证时撞上一次网关 207 秒超时（`total_timeout=180` 生效，回了"回答未完"），
与本次改动无关，但说明网关偶发卡顿还在。

### 15:20 | RAG-Agent | 真凶找到了：问题里带着「开启上下文，」四个字

上一篇的"索引还在加载"只解释了一部分。翻 `qa_log.jsonl` 才看清人实际发的是：

```
q: 开启上下文，那demo对应的uat数据库账号呢     hits=8 top_score=0.0  答案=未找到
```

**「开启上下文，」被当成问题的一部分送进了检索**。这几个字是通用词，把"那demo对应的uat数据库账号呢"
的权重稀释掉了 —— 同一句话去掉前缀后，那篇笔记排第 1（rerank 2.329，第二名 1.037）；
带着前缀就掉出前 8，于是回答"片段中未找到"。

改动（两层，前后端都做）：

- **`app/server.py`** 新增 `_strip_cmd()` + `_CMD_RE`，剥掉问题开头的
  `开启/打开/启用/关闭/关掉/清空/重置/不用` + `上下文/上文/对话/会话` + 后续标点，
  最多连剥两条（应对"开启上下文，清空上文，…"）。`/ask` 与 `/ask/stream` 都过一遍。
  整句只有指令时返回「上下文开关在输入框上方的「💬 上下文」按钮，直接点就行」
- **`app/static/index.html`** `ask()` 里同样识别这个前缀，并且**真的执行它**
  （开/关 `ctxOn`、更新按钮、提示一行），然后把前缀从问题里去掉再发

踩的坑：第一版在 `gen()` 里写 `q = _strip_cmd(q)`，导致 `q` 变成 gen 的局部变量，
上面的 `if not q` 直接 `UnboundLocalError`，整个 SSE 流 0.6 秒空返回。
改成在 handler 层算好 `q` / `cmd_only`，`gen()` 只读不改。

实测（重启后，冷启动所以第一条 135 秒）：

```
问：开启上下文，那demo对应的uat数据库账号呢
  引用 #1 _notes/20261103-144807-仿真环境信息.md
  答：demo 对应的 UAT 数据库账号是 demo/demo，192.0.2.38:1521，实例名 uatgz[1]
问：关闭上下文
  0.0s →「上下文开关在输入框上方的「💬 上下文」按钮，直接点就行。」
```

顺带把答案缓存清了（内存缓存，重启也会清），避免那条错误答案被秒回。

### 15:05 | RAG-Agent | 修"刚加的笔记搜不到"—— 其实是索引还在后台加载

人用「✍️ 写内容」加了一篇《仿真环境信息》笔记，紧接着问「那demo对应的uat账号呢」，
回答是"片段中没有明确标注为 UAT 的 demo 账号"。

查下来**笔记早就进库了**：`_notes/20261103-144807-仿真环境信息.md`，10 个块，
`chunks.jsonl` 14:51:59 写入、`vectors_meta.json` 14:53:30 补完。重新检索：

```
纯 BM25  top10：没有它（被一堆 OTRADE 部署手册压住）
混合+重排 top8：第 1 名，rerank 2.329（第 2 名才 1.037）
```

再走一遍真实接口，答案已经对了（8 秒）：

> demo 对应的 UAT 数据库账号是 **demo/demo**，连接信息为：数据库 `192.0.2.38:1521`，
> 实例名 `uatgz`[1]。另有一个 demo 账号 `demo/Qtrade_Amc_2045`，但它属于生产环境，不是 UAT[3]。

**真正的原因**：`kb.load_kb_indexes()` 在文件已更新时会**先返回旧索引**，新索引在后台线程加载，
5.6 万块约 100 秒。人保存完立刻提问，正好落在这个窗口里 → 系统用旧数据回答，
用户看到的就是"我明明加了，怎么搜不到"。

改动：

- **`app/kb.py`** 新增 `state(kb_id)`：返回 `{stale, loading}`，判断内存索引是否落后于 `chunks.jsonl`
- **`app/server.py`**：`/ask/stream` 开头取一次 `kb.state()`，把 `stale` 塞进 `done` 事件
  （缓存路径和正常路径都塞）
- **`app/static/index.html`**：`done` 里拿 `stale`，回答下面追加一条黄色提示
  「⏳ 索引刚更新，正在后台加载（约 1–2 分钟），本条回答可能还没包含最新加入的文档」

验证：直接构造三种缓存状态调 `kb.state()` ——
`一致 → stale False`、`磁盘新 120 秒 → stale True`、`无缓存 → stale False`，
`node --check` 与 `py_compile` 均通过。**后端改了，已重启。**

给使用者的结论：加完文档别立刻问，等 1–2 分钟；看到那条黄色提示就是还在加载，不是没进库。

### 14:45 | RAG-Agent | 演示模式（不走答案缓存）+ 换个回答强制现算

人反馈：演示时同一个问题问第二遍会命中缓存、瞬间弹出一样的答案，"显得很假"。

根因是 `app/cache.py` 的答案缓存（同问题 24 小时内秒回同一份答案），
缓存命中时 SSE 还会发一条 `⚡ 命中缓存, 秒回`。缓存本身是收益，但演示场景下是负资产。

改动（三处）：

- **`app/server.py`**：`AskIn` 增加 `no_cache: bool`。为 true 时既不查缓存也不写缓存。
  `/ask/stream` 的缓存读写各加一个 `not body.no_cache` 条件
- **`app/static/index.html`**：
  - 「⚙ 更多」新增 `🧪 演示模式（不走缓存）` 开关（状态存 `localStorage.rag.nocache`）；
    打开后每次提问都带 `no_cache:true`
  - 「⚙ 更多」新增 `🧹 清空答案缓存`，调新接口
  - `ask()` 请求体带 `no_cache: isRetry || demoMode()` —— **「↻ 换个回答」天然绕缓存**，
    不用开演示模式也能每次真跑
- **新增接口**：`POST /api/cache/clear`（管理员，可按 kb_id 清）、`GET /api/cache/stats`
- 缓存命中的文案从 `⚡ 命中缓存, 秒回` 改成 `⚡ 这个问题刚问过，直接给你上次的结果`（更中性，也别装成是刚算的）

实测（重启后）：

```
[1] 第一次问（无缓存）: 100.2s  cached=False  label=知识库          ← 冷启动加载 BM25
[2] 原样再问一次:        0.1s   cached=True   label=知识库（缓存）
[3] 带 no_cache:        15.5s  cached=False  label=知识库           ← 换个回答走这条
[4] 管理员清缓存: {'ok': True, 'cleared': 1}
```

普通 key 调 `/api/cache/clear` 返回 403「需要管理员权限」，符合预期。

**后端改了，已重启。**

### 20:15 | RAG-Agent | 正常回答也能「↻ 换个回答」（原地重抽，不堆重复提问）

人要求"正常回答的也可以加上重试，等于可以觉得不太对就再抽卡"。

改法（`app/static/index.html`）：

- 重答按钮**不再只在失败/降级时出现**，任何回答下面都有；文案按情况分两种：
  正常回答 `↻ 换个回答`，失败/降级/截断仍是 `↻ 重试`
- `ask(retryQ, reuseNode)` 增加第二个参数：传了就在**原气泡里原地重绘**，
  不再 `addMsg('user')` 和新建回答气泡，聊天记录里不会堆一排同样的问题
- 控件行加 `flex-wrap`，手机上一行放不下时不会挤

顺手把「使用文档 → 四、怎么提问」那段说明改了：原来只写出错点重试，
现在写清楚"觉得答偏了就点换个回答原地再抽一次"。

验证：mock 掉 `/ask/stream` 返回 SSE（每次回答文本递增），脚本里调一次 `ask()` 再点按钮：

```
第 1 次提问后：请求 1 次，提问气泡 1 个，正文=回答#1
「换个回答」按钮 1 个，可见=block
点完之后：请求 2 次，提问气泡 1 个，正文=回答#2
结论：原地重答生效，没重复堆提问
```

`node --check` 通过与 CSS 括号平衡都复核过。**纯前端，刷新即可。**

### 19:55 | RAG-Agent | 修「↻ 重试」点了没反应

人反馈出错后点重试没任何反应。原因很直接：

```js
retryBtn.onclick = () => ask();   // 旧代码
async function ask(){
  const q = $('#q').value.trim();  // 读输入框
  if (!q || !cur) return;          // 输入框早被清空 → 直接 return，静默不动
  $('#q').value = '';              // 发出去时就清空了
```

提问一发出就把输入框清空，重试按钮再去读输入框自然拿到空字符串，直接 return，
所以既没新请求、也没任何提示。**这个问题从加重试按钮那天起就一直存在。**

改法（`app/static/index.html`）：

- `ask(retryQ)` 增加可选入参，传字符串表示"重发这个已有问题"，不传才读输入框
- `retryBtn.onclick` 改成 `ask(q)`，并把按钮立刻禁用、文案改「↻ 重试中…」，防连点发两遍
- 失败时把问题回填到输入框（`if (!$('#q').value) $('#q').value = q`），想改几个字再发也行

验证方式（可复现，别只看代码）：把页面 DOM 里的 `fetch` 换成强制失败，脚本里调一次
`ask()` 再点重试按钮，断言 `/ask/stream` 被调了 2 次、请求体里的 question 一致。
实测 第一次 1 次 → 点重试后 2 次，输入框回填原问题。`node --check` 也过。

**纯前端，刷新即可，不用重启。**

### 18:40 | RAG-Agent | 手机端排版重做（原来的媒体查询选择器全写错了）

人反馈"手机访问排版很丑，挤来挤去"。查下来不是没做适配，是**做了但没生效**：
`@media (max-width:820px)` 里写的是 `main` / `.topbar` / `.topbar h2` / `.topbar .me-name`，
而实际元素是 `#main` / `#topbar`，整段规则一条都没匹配上，手机上等于完全没有移动端样式。

`app/static/index.html` 改动：

| 位置 | 改了什么 |
|---|---|
| 顶栏 | 选择器改成 `#topbar`；标题一行、操作按钮一行；`.t2`（项目路径）单行省略；`#meName` 隐藏；☰ 绝对定位到标题行左侧；下拉菜单 `right:0` 防切边 |
| 对话区 | `#chat` 内边距 20→10；`.msg` 气泡 `78% → 94%`（480px 下 96%）；`pre` 横向滚动 |
| 表格 | 新增 `.tbl` 滚动容器，`md()` 输出的表格统一包进去，宽表横向滑动不撑破气泡；手机端表格字号 11.5px、内边距 4px 6px，避免每格折成两行 |
| 引用区 | `.refs` 宽度 80%→100%；文件名原来 `word-break:break-all` 会换三四行，改成单行省略（悬停看全）；相关度分数 `(24.6)` 手机上隐藏；缩略图 70→60px |
| 输入栏 | 输入框 `flex:1 1 100%` 独占一行，按钮统一第二行并给 38px 最小高度；`padding-bottom` 加 `safe-area-inset-bottom`（iPhone 底部横条） |
| 弹窗 | `.card` 96vw、`wide` 同宽、`.tabs` 允许换行 |
| 文案 | 输入框 placeholder 手机端缩短成"输入问题，回车发送"（原来那句长的会被截掉半句） |

验证方式：把页面塞进 390px 宽的 iframe，用 Edge headless 截图比对（`--window-size` 在
Windows 下会被抬到约 496px，直接截 390 是假的，别踩）。成品图存 `docs/images/mobile-390.png`。

**纯前端改动，不用重启，刷新即可。**

### 17:40 | RAG-Agent | 修"库里有却答没找到"（文件名逐字命中）+ 引用区图标过滤

人反馈两件事，都在 `app/kb.py` / `app/imgserve.py`。

**1. 问环境信息答"文档中未找到"，但库里有**

原问题：「客户的环境信息是什么，我需要知道测试环境和生产环境，o32，demo，oorder的环境信息。」

查清了不是没索引——《测试环境信息.txt》《仿真环境信息.txt》《UAT环境信息-马磊整理.txt》
都在库里、分块正常、IP 和账号密码都在文本里。问题出在**召回**：

- 库里确实有 873 个含 IP 的块、52 份文档，全是部署手册/配置表/周报
- 原来只有"问题词命中文件名的比例 ≥50%"才加权（`_PATH_HIT_MIN`），
  长问题十几个词，比例永远到不了 50%，所以《测试环境信息.txt》一次都没进过 top15
- 单独用「测试环境信息」查，它稳稳排第 1（50.9 分）——说明是排序被稀释，不是文件缺失

改法（`app/kb.py`）：
- 新增 `_q_phrases()`：把问题切成 4~14 字连续短语
- 新增 `_path_phrase_hits()`：按**字面最长命中短语长度**给文件名加权，
  有效倍数 1.4~3.0；短语必须含中文（否则 `o32，demo，oorder` 去标点后会拼成
  `demooorder`，误命中《demo&oorder&hix系统日常检查.docx》——踩过）
- 新增 `BM25.name_hits()` / `kb.name_hits()`：把这些文档**单独摘出来**，
  挑块时优先"有实料"的（含 IP、含账号/密码/用户名），而不是恰好重复了几个问题词的说明段
- `app/server.py` 的 `_multi_search()` 用 `_with_reserved()` 给它保底 2 个引用位：
  重排用的是原问题，这类"文件名对得上但正文不啰嗦"的文档几乎必被挤掉，所以先留位子

结果：同一问题引用里出现《测试环境信息.txt》《仿真环境信息.txt》，
答案给出 10 个 IP（10.1.130.86/87、10.1.20.213、11.1.40.19/20、192.0.2.75/89、172.31.1.155）
和账号密码说明。**没动 `_ensure_coverage` 那条已经被证伪的思路。**

**2. 引用区显示小红旗/红三角图标，没有参考价值**

周报 xlsx 里有两张 2500×2500 的图，其实是条件格式的小图标（几乎纯色底 + 一个小图），
字节/像素只有 0.007~0.0097（正常截图 0.05~1.0），压出来才 44~60KB。

改法（`app/imgserve.py`）：`doc_images()` 加两道过滤
- 最长边 < 120px → 当图标丢掉
- 大画布（面积 ≥ 100 万像素）且字节/像素 < 0.02 → 当"纯色底+小图标"丢掉
- 新增 `_dims_of()`：一次开 zip 批量取像素尺寸，结果按 `(文档指纹, 图片名)` 缓存；
  解析不出来的一律**保留**（宁可多显示，也别误杀架构图）

实测：周报 xlsx 引用图 2 → **0**；《O-order部署手册.docx》仍是 12 张真截图。

### 16:25 | RAG-Agent | 使用手册按真实用例重写（四、怎么提问 + 五/六 + 背景页）

人要求用两个真实问题做例子，并把手册同步到今天改完的行为。

- **四、怎么提问** 开头直接给两个范例块：
  ① `demo升级的流程是什么样的`（具体流程类，说明角标怎么对引用）
  ② `我是一个刚接手项目的小白，这个项目目前是什么进展，这个项目的内容是什么。`
  （陌生项目摸底类，说明"把处境写进问题"有用、系统会自动拆子查询）
  保留"问具体/用原名/换问法"，但把"一次问一件事"改成有例外——
  原来那条和范例②自相矛盾（②本身就是复合问题）。
- **五、看引用**：补引用区序号徽标与角标的一一对应；补"预览已保留排版和插图"；
  补缩略图点开是页内大图、Esc/点空白关闭。
- **六、接着追问** 改标题为「上下文默认是关的」，写明按钮、开启后最多 3 轮、
  关闭会清空并给提示、状态存 localStorage。
- **十、常见问题** 新增三条：追问被当新问题（上下文没开）、"正在接着写…"是什么、
  右上角小数字是什么。
- **项目背景与说明**：技术栈补图片按需提取/EMF 转 PNG/预览渲染/自动续写/按 key 隔离；
  新增「知识库怎么来」讲文件夹建库与拖拽上传两条路径 + 增量索引；
  架构简览补问题拆解、融合去重、图片缩略图。

### 15:10 | RAG-Agent | 预览保留排版和图片；图片改页内大图；上下文默认关闭

人给的三个反馈，一次改完。

1. **点开图片关不掉**（`app/static/index.html`）—— 引用的缩略图原来是
   `target="_blank"` 直接新开标签页。改成页内浮层 `#lb`：点空白 / ✕ / Esc 都能关，
   点图本身也关。工具条按钮带 `onclick="return lbOpen(...)"`，中键仍可新开标签页。
2. **点开文档看不到图片、排版也没了**（新增 `app/docpreview.py`，改 `app/server.py`）——
   原来预览是 `index_docs.EXTRACTORS[ext](fp)` 抽出纯文本塞 `<pre>`，图片在抽取阶段
   根本不出现在文本里，分段/表格也全丢。现在按文档原结构渲染：
   - docx：按 `body` 顺序走段落/表格；标题按原样式层级出 h2/h3；run 保留粗/斜/下划线；
     `a:blip`(DrawingML) 和 `v:imagedata`(VML) 的图**就地**插回段落位置
   - pptx：一页一节，标题+正文+表格，**组合形状递归展开**（不展开的话组合里的图和字全丢）
   - xlsx：一个 sheet 一张表，首行表头，最多 300 行
   - 图片不内联 base64（79 张图的 docx 会撑出十几 MB HTML），走 `/api/kb/<id>/img`
     按需解压+缓存，EMF/WMF 顺带自动转 PNG
   - 渲染失败自动回退原来的纯文本，不会白屏
   - 实测：`O-order部署手册.docx` 40,993 字 / 14 标题 / 5 表 / 81 图，图 URL 全部 200；
     `智能交易平台.pptx` 73 页全出标题 / 128 图
   - `_rid_name()` 要兼容两套 API：docx 用 `part.related_parts[rid]`，
     pptx 只有 `part.rels[rid].target_part` —— 只写前者的话 pptx 的图一张都取不到
3. **上下文默认关闭**（`app/static/index.html`）—— 新增顶部 `💬 上下文：关/开` 按钮。
   默认关：`history` 传空数组、不记 `chatHistory`。开启后才会带最近 3 轮，
   开关状态存 `localStorage('rag.ctx')`。交付场景里大部分是独立提问，
   默认带上旧对话只会让答案跑偏。

预览页自己也加了同样的页内大图（`.dp img` 可点开，Esc 关）。

## 2026-10-30

### 14:00 | RAG-Agent | 图片标记压到 0.0%，索引重建两轮（`index_docs.py`、`app/indexer.py` 跨归属，人交办）

上一轮加完图片标记后评测从 6/6 掉到 3/6，查出来是标记本身把正文吃了。

- **症状**：462 个块里，图片标记占掉 **29.6%** 的字符（187K 字符里 55K 是 `[image7.png]`），
  还产生了 `[image5.jpeg]` 这种"整块只有 13 个字符、100% 是标记"的垃圾块。
  这些块会被 BM25/向量当正文索引，把真正的答案块挤下去。
- **改法**（`index_docs.py`）：
  1. `chunks()` 里加 `_only_images()`，纯标记块（去掉标记后 < 15 字）直接丢弃
  2. pptx 每页标记**不再单独成行**，追加在该页最后一句文字后面，且每页最多 2 个
  3. docx/xlsx 末尾清单从全部降到 4 个
  4. 新增 `_MAX_MARKS_PER_PAGE=2` / `_MAX_MARKS_DOC=4`
- **改法**（`app/indexer.py`）：`_SIG_VER` → `v2026-10-30-imgmark2`。
  不改这个版本号，增量索引会把"文件没变"当理由全部跳过（踩过：`changed 0`，1 秒"重建"完）
- **结果**：某基金标记 4242 → **1039 个，占正文 0.0%**；某资管 3081 → 57 个（0.1%）。
  块数 56605 → 56416，向量已对齐（56416）
- 评测 3/6 → **5/6**（`xy-01` 仍缺 OORDER，原因见下）

**没解决的那件事**：`xy-01`（"刚接手项目，现在什么进展、内容是什么"）答案里一直缺 OORDER。
查清了不是检索不到——子查询"智能交易平台 系统架构 模块组成"确实召回了架构评审材料，
但 `_multi_search` 合并后**统一用原问题重排**，架构类块按"项目进展"打分天然吃亏：
实测排到第 14、15 名，`_diversify` 只留前 8，双双被切。
试过 `_ensure_coverage()`（给没进前 8 的子查询保留一个代表）—— xy-01 没救回来，
反而把 `xy-03` 顶坏了（4/6），**已回滚**。这条留待后面用"按子查询分别给配额"的方式再试。

### 13:30 | RAG-Agent | EMF/WMF 真正解决（转 PNG）+ 某资管图片修复；新增 `warm_images.py`

上一轮把 emf/wmf 直接过滤掉了。人追问"那怎么解决"，这次真解决了。

**一、EMF/WMF 转 PNG**

全库 116 张 emf + 3 张 wmf。这些恰恰是最有价值的一类（架构图、流程图多存成矢量），
过滤掉等于把最该看的图扔了。实测那张 `image13.emf` 转出来就是完整的系统架构图
（展示层/服务层/应用框架/基础设施，Otrade+Oorder 业务核心全在里面）。

方案是借 Windows 自带的 GDI+：

- `imgserve._emf_to_png()`：写临时 emf → PowerShell 调 `System.Drawing` 渲染成
  PNG（白底、HighQualityBicubic、最大边限制 2400px 防内存爆）→ 读回
- 成本：单张约 1.6 秒（主要是 PowerShell 进程启动），所以**必须缓存**
- 缓存成 `xxx.emf.png`，放在原图缓存同目录；`extract()` 先查 PNG 缓存，
  命中就是 1ms
- 为什么不用 pythonnet：Python 3.14 太新，装不上；Pillow 12.3 的 `Image.OPEN` 里没有 emf

**二、新增 `warm_images.py` 预热脚本**

用户点开引用时现转要等 1.6 秒，所以提前全转好：

```
python warm_images.py            # 全部知识库
python warm_images.py <kb_id>    # 指定库
python warm_images.py --stat     # 只看还有多少没转
```

4 线程并行，153 张一次跑完。已跑过：某基金 118/118、某资管 35/35，全部成功。
转出来的 PNG 最小 1KB、最大 207KB、平均 38KB。

**三、修了个更严重的问题：某资管的图一张都取不出来**

预热时发现某资管 35 张全部失败。根因不在 EMF，在路径解析：

`imgserve.kb_folder()` 对**纯上传型知识库**（`folder` 为空）返回 `None`，
连带的 `doc_full_path()` 也返回 `None` —— 某资管的所有图片（不光是矢量图）根本定位不到。

改成 `kb_folders()` 返回根目录**列表**：源文件夹（支持 `|` 分隔多个）+ `app/data/<kb_id>/upload`。
`doc_full_path()` 依次在每个根下试，并且每个根都做目录逃逸检查。
`kb_folder()` 保留为兼容层的包装。

修完某资管 35/35 转成功，问答返回 32 张真实截图（ETF2.0 实施手册、安装部署手册那些）。

**四、顺手把矢量图排到缩略图末位**

`doc_images()` 的 `_rank` 分三档：png/jpg/jpeg(0) → gif/svg/bmp(1) → emf/wmf(2)。
矢量图虽然能转，但别让它抢占首屏的 4 个名额。

**实测**（服务重启后的稳态）：

| 图片 | 大小 | 耗时 |
|---|---|---|
| EMF 转出的 PNG | 119 KB | 10 ms |
| 普通 JPEG | 1.2 MB | 11 ms |
| 普通 PNG | 244 KB | 9 ms |
| 小 PNG | 5 KB | 9 ms |

注意：服务刚启动后的头几次请求会到 900ms 左右（冷启动 + registry 读取），
不是转换慢，别误判。

**遗留**：某资管是纯上传库，chunks 还是旧索引，块里没有 `[imageN]` 标记，
现在靠 `_build_sources` 的"文档级退回取图"（前 4 张）撑着。要让某资管的图也精准对应
到具体段落，需要对某资管重建一次索引（它的库小，2313 块，很快）。

### 12:40 | RAG-Agent | **跨归属改动（人明确交办）**：`index_docs.py`、`app/indexer.py`、新增 `app/docimg.py`

人明确说"别分工了，就你来"，所以直接动了索引侧。两件事都做了：

**一、抽取时补回图片标记**

新增 `app/docimg.py`，直接解析 OOXML 按排版顺序取图片位置：

- docx：`word/document.xml` 里每个 `<a:blip r:embed>` 按 `<w:p>` 顺序定位，
  再通过 `word/_rels/document.xml.rels` 映射成 `media/imageN.png`；
  页眉页脚（`word/header*.xml`）里的图（公司 logo 常在这）单独收进来
- pptx：逐张 `ppt/slides/slideN.xml` 取图，**按页分组**，121 张图精确到第几页
- xlsx：`xl/media/` 下的图，位置难还原，只在文档末尾列清单

`index_docs.py` 的三处抽取跟着改：

- `extract_pptx()`：**每页文字后**追加该页的 `[imageN.png]`
- `extract_docx()` / `extract_xlsx()`：文档末尾追加图片清单行

效果（重建后实测）：全库 **462 个块**带图片标记、**4242 个标记**、涉及 **152 份文档**，
之前只有 2 块 / 1 份文档。

**二、图片 caption 进检索**

查清了：`_join_with_images()` → `img_caption.load_doc_text(p)` 这条链路**本来就是通的**，
caption 会追加到文档文本末尾。之前没生效是因为全库只有 1 个 sidecar 文件，
且那篇文档没重建过索引。这次重建后 caption 一起进去了。

**三、索引器版本号（关键）**

`app/indexer.py` 的 `_sig()` 加了 `_SIG_VER = "v2026-10-30-imgmark"`。
不加这个，改了抽取逻辑后增量索引会认为文件"没变"直接跳过 —— 重建 0 秒完成、
新内容一条都进不去（这个坑 `_sig` 的注释里原来就记着）。

**四、`_build_sources` 退回文档级取图**

块自己没引用图片时，退回列该文档前 4 张图（`doc_images(..., refs or None, limit=4)`），
用户点开引用至少能看到这份材料里的截图。

**重建结果**：288 文件 → 56605 块（原 56999），271 文件重抽，4 个失败。
块数变化是因为图片标记改变了分块边界。**向量库必须跟着重建**，否则 56999 行的
旧向量会和 56605 个块错位，检索出完全不相干的块 —— 已跑 `build_vectors.py` 补建
（复用 55713、新算 892，34 秒，最终 56605 行，与 chunks 严格一致）。

**五、缩略图的两道过滤**（`app/imgserve.py`）

第一版直接把所有图塞给前端，实测有两个问题，都修了：

- **emf/wmf 浏览器不认**。全库 media 统计：png 3762、jpeg 261、svg 131、emf 116、
  无后缀 102、mp4 8、wmf 3、wdp 1。emf/wmf 是 Windows 矢量图，Chrome/Edge 都不渲染，
  塞进去就是破图。现在 `_BROWSER_OK` 只放 png/jpg/jpeg/gif/bmp/webp/svg。
- **小图没意义**。抽到的 `image5.png` 只有 694 字节，是个图标。现在 `_MIN_IMG_BYTES = 8KB`
  过滤，并且按"png/jpeg 优先 + 大的优先"排序。实测那份 pptx 99 张媒体 → 91 张可展示，
  最小 14KB、平均 88KB。

前端 `<img onerror="this.closest('a').remove()">` 再兜一道，真加载失败就把这张撤掉，
不留破图占位。

`list_doc_media()` 的返回值从 `{名: zip内路径}` 改成 `{名: (路径, 字节数)}`，
`extract()` 跟着改成取 `hit[0]`。

**端到端实测**：问"系统架构是什么样的"→ 回答 2072 字、8 条引用、17 张图，
全部是 30KB–1.5MB 的真实截图（含实施手册里的部署界面、架构评审材料的架构图），
无 emf/wmf、无小图标。

### 12:10 | RAG-Agent | 新增 `app/imgserve.py`；改 `app/server.py`、`app/static/index.html`

**文档内嵌图片可以在前端展示了。**

- 新增 `app/imgserve.py`：把 chunk 里的 `[image1.png]` 映射回原文档 zip 里的
  `word/media/image1.png`（pptx 是 `ppt/media/`、xlsx 是 `xl/media/`），按需解压并缓存到
  `app/data/<kb_id>/images/<文档路径md5>/<图片名>`，带目录逃逸检查。
- `app/server.py`：新增 `/api/kb/{kb_id}/img?path=&name=` 返回图片字节（含
  `Cache-Control: max-age=604800`）；`_build_sources()` 加 `kb_id` 参数，给每个引用片段
  附上 `images` 列表（只确认存在、给 URL，不解压，浏览器加载哪张才解哪张）。
- `app/static/index.html`：引用区每个片段下方显示图片缩略图（高 70px，悬停放大，点击开原图）。

**但数据侧有个硬限制**：全库 56999 个块里，只有 1 份文档（`6. 项目验收/...验收报告-签字版.docx`）
的块文本保留了 `[imageN]` 标记，其余文档（尤其 pptx）抽取时**图片被直接丢弃**，
块里只有 `[幻灯片 N]` 和文字。所以这个能力目前只能对极少数材料生效。

另外发现：`img_index.py` 生成的图片描述写在 sidecar（`app/data/_imgcap/text/<md5>.txt`），
但 `indexer.py` 里 `doc_text_path()` **只出现在 `_sig()` 里用于算签名**，全项目搜不到
读取 sidecar 内容并入 chunk 的代码 —— 也就是说 caption 提取了但进不了检索。
已写进 INBOX 交办 Claude Code（`indexer.py` 归属对方）。

### 11:33 | RAG-Agent | `app/static/index.html` + `app/server.py`

**重写前端 markdown 渲染器 `md()`**，从行级正则替换改成块级解析：

| 语法 | 旧行为 | 新行为 |
|---|---|---|
| `###` 三级标题 | 原样漏出 | `<h4>` |
| 表格 `\| a \| b \|` | 原样漏出 | `<table>` |
| `- ` 列表 | 替换成 `•` 字符（不是列表） | `<ul><li>` |
| `• ` 列表 | 不支持 | `<ul><li>` |
| `1. ` 列表 | 加粗序号 | `<ol><li>` |
| `> ` 引用 | 原样漏出 | `<blockquote>` |
| ` ``` ` 代码块 | 不支持 | `<pre><code>` |
| `---` 分隔线 | 不支持 | `<hr>` |
| `[文字](url)` | 不支持 | `<a>` |

**同时修掉一个隐藏 bug**：`.msg` 上有 `white-space: pre-wrap`，会把 `md()` 生成的 HTML 里
每个换行再显示一次，表格和列表怎么调 CSS 都排不对。改为 `.msg.assistant` 覆盖成 `normal`
（用户消息仍保留 `pre-wrap`），并补齐 table / blockquote / pre / li / hr 的样式。

`app/server.py` 的 `index_page()` 加 `Cache-Control: no-cache`，否则改完前端用户刷新还是旧页面。

用 node 实测过：`node --check` 语法通过；用真实回答（含 `•` 列表、`###`、表格、`>` 引用）
跑 md()，输出 `<ul>`/`<h4>`/`<table>`/`<blockquote>` 全部正确；流式性能 0.06ms/次（渲染 73 次共 5ms），
不需要节流。

原因：用户反馈"就是有的渲染了，有的没有"——实际是只有标题和粗体被渲染，表格、三级标题、
列表全部原样漏出。

### 11:20 | RAG-Agent | 新增 `AGENTS.md` / `CLAUDE.md` / `docs/agents/*`

建立双 agent 协作契约：文件归属表、四条硬规矩（先读 CHANGELOG → 改完登记 → 不跨归属改 → 共用文件先留言）、
禁令（不删 app/data、不改 chunks.jsonl 字段结构）、当前状态快照、验证命令。

原因：两个 agent 同时改一个项目，覆盖不会报错，必须靠约定。

### 11:14 | RAG-Agent | `app/config.json`

`read_timeout` 60 → 30。

原因：xy-01 出现两次异常耗时（195s / 358s），排查发现 `read_timeout` 是**单次读**超时，
网关慢速吐字时每次读间隔都不超 60s，总时长不受控，一路拖到 6 分钟。收到 30s 让慢速响应
更容易被判超时并触发重试。复测同一问题两次均为 25s，确认原现象是网关偶发。

### 11:01 | RAG-Agent | `app/planner.py`

1. `_SYS` 提示词新增第 5、6 条：问「项目内容/介绍/概况」时必须产出"系统架构 模块组成"子查询；
   同时问「进展」和「内容」要拆成不同子查询。
2. 新增 `_PROJECT_INTRO` / `_ARCH_HINT` / `_ARCH_SUB` / `_needs_arch_sub()`，
   在 `plan_queries` 里做**代码兜底**：模型没给架构子查询时手动补一条。
3. `need_split()` 对短问句（<10 字）不再直接返回 False，改看是否是项目认知类问题。

原因：xy-01「我是刚接手的小白，项目进展和内容是什么」答了 3406 字却 **demo/oorder/hix 出现 0 次**
—— 规划器只拆出"功能范围/进度/背景"，系统架构文档压根没被检索。修完后评测 6/6 通过。

### 10:58 | RAG-Agent | `app/server.py`

1. 新增 `_diversify(results, topk, max_per_path=2)`：同一份文件最多占 2 个引用位。
2. `_multi_search` 改为 `ThreadPoolExecutor` 并行召回；rerank 取 `topk*2` 后再做文件去重。

原因：8 个引用位里 4 个来自同一份 OTRADE 部署手册，模型看到的全是一份文档，
系统概述、环境信息这类文件挤不进来。

### 10:50 | RAG-Agent | `app/server.py`

`llm.chat` 失败时的降级文案改了：原来把 8 个片段全文（3000+ 字）糊给用户，
现在给简短说明 + 指向引用区，并**补写 qa_log**（原来这条路径不写日志，失败统计不到）。

原因：xy-04 遇到网关抖动，返回了 3810 字的片段堆砌，用户根本没法读，而且日志里查不到这次失败。

### 10:44 | RAG-Agent | `app/kb.py`

1. **文档版本优先级**：新增 `_core_name` / `_ver_num` / `_doc_sort_key` / `_version_boosts`。
   同一份文档的多个副本（V2023/V2026/v1.2/v1.3、带日期、带"副本/备份"）识别为一族，
   按修改时间（拿不到就用文件名版本号）降序，权重 `1.0 / 0.82 / 0.68`。
2. **文件名命中加权**：`_PATH_HIT_MIN=0.5`、`_PATH_HIT_GAIN=4.0`。
   BM25 里按 `query 词命中文件名的比例` 加权，rerank 之后再补一次。
3. **降权拆分**：`path_boost` 拆成 `_path_weights(path)` 返回 `(权威权重, 降权因子)`，
   `BM25` 新增 `self.suppress` / `self.path_toks`，`add()` 新增 `extra_boost` 参数。

原因：
- 版本降权：同族多版本间旧版内容过期会顶掉新版。最初用 1.0/0.5/0.3 太狠，用户指出
  "旧周报对刚接手的人也有用"，改成 1.0/0.82/0.68 的轻度倾向。
- 文件名加权：问「上线投产配置表清单有哪些内容」，那份文件有 1036 个块，但正文全是
  `Otrade | linux_base | 天鉴平台 | ...` 这种表格行，词频统计看不出相关性，检索不到。
  加权后从"检索不到"变成第 2 名。
- 降权拆分：`suppress` 词表含"清单/台账/进度/周报/会议"，用户明确问这些时反而被压制。

### 10:37 | RAG-Agent | `app/static/index.html`

1. 失败/降级/超时/无输出**全部**显示「↻ 重试」（原来 catch 分支只提示"可重试"但不显示按钮）。
2. 降级回答不再写进多轮对话历史，避免污染后续追问。
3. `.kb .meta` / `#topbar .t2` 单行省略 + `title` 悬停看完整路径。
4. `.btn` 加 `white-space:nowrap`，按钮文字不再折行。
5. 「新会话」按钮改文案「🆕 清空上文（已记 N 轮）」，点击后插入灰字提示；
   换知识库时静默清空（`resetChat(true)`）。

原因：用户反馈"输出不行的时候说点重试，没有重试按钮"、"路径没必要展示完整"、
"很多按钮里面的字分了两行很丑"、"这个按钮点一下就没了"。

### 10:21 | RAG-Agent | `app/config.json` / `app/llm.py` / `app/server.py`

1. `max_tokens` 1500 → 3500。
2. `_stream_anthropic` 捕获 `message_delta.stop_reason`，写入 `meta["stop_reason"]`。
3. `server.py` 检测 `stop_reason == "max_tokens"` 时追加「⚠️ 回答未完」提示。
4. `server.py` `_ASK_SYSTEM` 新增【重要3】：内部系统的账号密码/IP/连接串必须原样给出。

原因：用户截图反馈回答在"• 投资O45："处硬截断，没有任何提示——额度用完静默停止。

### 10:42 | Claude Code | `app/data/*/vectors_meta.json`（两个库）

重建向量索引。某基金 (56999, 512)、某资管 (2313, 512)，均 `complete=True`。

RAG-Agent 核对：向量行数与 `chunks.jsonl` 行数严格一致，字段 `count/dim/model/provider/ts/ids/complete/hashes`
齐全，两库结构一致；实测某资管向量召回正常（sim 0.73）。

### 10:39 | Claude Code | `app/indexer.py`

新增 `schedule_vectors`，建索引后自动触发向量构建。

### 10:38 | Claude Code | `app/embeddings.py` + 新增 `build_vectors.py`

`ensure_vectors`（增量、断点续跑、按内容哈希对齐）、`save_vectors` 原子写、
`complete`/`hashes` 元数据字段；`build_vectors.py` 为手动补建/排障脚本。

RAG-Agent 核对：`chunks.jsonl` 字段结构未变（仍为 `id/path/chunk/text`），
与 `kb.py` 的检索逻辑兼容。

> 以上三条是 RAG-Agent 根据文件时间戳和内容**推断**的，请 Claude Code 本人核实并补充准确描述。

---

### 2026-11-19 | Claude Code | `app/server.py` / `app/static/index.html`

问答气泡在「换个回答」旁新增「🌐 联网搜索回答」「🧠 大模型本地回答」。
`fallback_mode=web/llm` 时后端跳过知识库检索、缓存和引用，直接走联网/模型自身知识；
联网不可用或没搜到时在正文前明确标注并降级为模型知识；来源徽标沿用现有 web/llm 样式。

原因：知识库覆盖不到的问题，需要用户显式选择“不查库”的回答来源。

服务器实测无外网（443 不可达），已把 `/opt/rag/config.json` 的 `fallback.web_search.enabled`
设为 false，前端据此隐藏「联网搜索回答」；web 模式保留，后端立即降级并标注「联网未启用」。
已部署镜像 `rag-bff:20261119-llm-direct`。

### 2026-11-19 | Claude Code | 模块路由 / PPT 分块 / 引用校验

针对“问交易所指令却引用开放式基金/期货，每份 PPT 各抓一点”：

1. 新增 `app/module_router.py`：交易所/开放式基金/期货/固收识别；
   单模块问题在检索后过滤其他模块片段；多模块对比保留并集；
   模块未命中直接澄清，不再用相邻模块硬答。
2. `_multi_search` 在重排/去重前按模块过滤，避免 `_diversify` 把其他模块补进引用位；
   模块问题跳过答案缓存；提示词加入模块约束；生成后做跨模块引用校验。
3. `index_docs.py`：PPT 一页一块，块前缀写 `【幻灯片 N】【模块：X】【标题：…】`；
   去掉 PPT 色板 RGB 噪声；`indexer.CHUNKER_VERSION=2` 强制重解析旧块。
4. 新增 `eval/module_cases.json` + `eval/run_module_eval.py`，检查引用来源模块精度。
5. 业务手册库 `20261108_174939_69c8b5` 已重建；模块对抗评测 6/6，
   交易所问题引用属于交易所模块。已部署镜像 `rag-bff:20261119-module-route2`。

### 2026-11-19 | Claude Code | 用户手册定向检索 / 全库重建

用户反馈：`o32交易所指令怎么下单` 的正确答案在已上传的
`基金投资管理系统O3.2用户手册V1.7(02.07.020)(1).pdf` 里，应回答
`【指令管理】→【交易所指令】→<个股指令>`，但系统说没找到。

根因有两个：

1. 综合用户手册同时覆盖股票/债券/基金/回购，被单模块过滤当成“多模块歧义页”丢掉；
2. 普通检索 top-k 里只有手册的“指令管理/组合维护”页，真正 3.3.1 个股指令页
   被培训 PPT、系统参数表挤掉。

修复：

- `module_router` 增加手册豁免：路径含“用户手册/操作手册/使用手册”等时，
  只要命中目标模块就保留，不因同时提到其他模块丢弃；
- `_diversify` 对手册允许最多 4 个引用位；
- 新增“用户手册定向检索”：模块 + 操作类问题追加
  `模块 用户手册 操作说明 步骤 原问题` 子查询，手册页优先放进引用；
- 业务手册库重新验证：答案命中 `基金投资管理系统O3.2用户手册V1.7...pdf`，
  给出 `【指令管理】→【交易所指令】→<个股指令>`；
- 模块评测增加 `must_include_any` 内容检查（不只看引用模块），修正综合手册
  被误算“错误引用”的口径；模块评测 6/6。

已部署 `rag-bff:20261119-manual-fix2`。
7 个知识库全部重建为 `chunker=2`（业务手册 2823 块；其余 6 个库 272/537/1554/1755/427/174 块），
状态 done、向量 ready。

### 2026-11-20 | Claude Code | 真实网关多人并发测试

3 个可用账号（管理员 + 2 个普通用户）各 2 路，共 6 并发，分别使用业务手册、
鑫元灾备部署、风控 5 三个库，真实 LLM 网关（非 mock）：

- 6/6 HTTP 200，0 个 429（正好 global_max=6）；
- 首字 p50 **82.1s**、p95 **145.0s**；总耗时 p50 95.9s、p95 146.9s；
- 结束后 `/api/health` 活跃名额 0、global_free 6；
- 与 mock LLM 压测（global 6、20 路突发 p95 25.9s）对比，真实网关约 2 个通道，
  上游排队是主要瓶颈；应用侧准入/释放正常。

结论：当前 6 路并发可用，但真实网关突发下首字 p95 约 2.4 分钟；
要压到 1 分钟内，只能降低放行、使用个人 Key（每 Key 约 2 路）或扩容网关。

### 2026-11-20 | Claude Code | 申请入口链接 / 网关连通性

- 服务器（宿主 + 容器）可访问 `lightcode-uis.hundsun.com:8080`：
  DNS 172.28.11.248 / 172.28.9.141，TCP 8080 OK；容器内 `/v1/models` 200，
  模型 `deepseek-flash`、`deepseek-v4-pro`；OpenAI `/v1/chat/completions`
  和 Anthropic `/v1/messages` 均返回 200（使用用户提供的 BYOK Key 验证）。
- 注意：应用当前全局模型是 `hs-deepseek-flash-latest`，该 BYOK Key 不支持这个
  模型名（422），支持的是 `deepseek-flash` / `deepseek-v4-pro`；直接把 Key 填进
  「我的 Key」会因模型不匹配失败。若要接个人 Key，需要加“个人模型选择”或切换全局模型。
- 已把「我的 Key」申请入口改为 LightCode UIS 控制台
  `http://lightcode.hundsun.com/ai_market/API`，并加入公司申请文档链接；
  全局网关占位改为 `http://lightcode-uis.hundsun.com:8080`；操作手册同步。
- 已部署 `rag-bff:20261120-links`。

### 2026-11-20 | Claude Code | 个人 Key 自定义模型

- 「我的 Key」增加自由文本“模型”框，可选填（不填用全局模型）；
  模型随用户存 `data/user_llm.json`，与 key 同一条记录。
- `llm.llm_settings` 支持 `user_model`；`_cfg_with_user_key` 在用户有 key 时
  把自定义模型合进 cfg，主问答、答辩、planner、fallback 统一使用。
- 管理员全局模型设置只写 `config.json`，**不会覆盖**用户自己的
  `user_llm.json` 模型；全局设置弹窗加了说明。
- 新增 `/api/my/llm/model`：已保存 Key 的用户只改模型、不重新粘贴 Key，
  保存前用已存 Key 验证。
- 用用户提供的 BYOK Key 实测：`deepseek-flash`、`deepseek-v4-pro` 验证
  返回 200「key 可用」；全局模型 `hs-deepseek-flash-latest` 对该 Key 不可用。
- 操作手册补充模型填写、只换模型、422 排查。
- 已部署 `rag-bff:20261120-user-model2`。

### 2026-11-20 | Claude Code | 全局 Key 切换为外部 BYOK / 10 路并发

- 按人要求，把其 LightCode UIS BYOK Key 设为**全局公共 Key**；全局模型切到该
  Key 支持的 `deepseek-flash`。Key 不写入文档/日志，配置仍为 600，
  备份在 `/opt/rag/backup/20261120-public-key/config.json`。
- 容器确认读到新 model/key，`/api/health` 200；无个人 Key 的账号正常提问，
  返回 8 条引用、2937 字，响应字段 model=`deepseek-flash`。
- 直连公司网关 10 路并发（`/v1/messages`，deepseek-flash，短请求）：
  **10/10 HTTP 200**，墙钟 0.8s，单请求 p50 0.46s / p95 0.76s；未观察到
  10 路并发下的模型/Key 硬限制。
- 应用层仍受 `global_max=6`、`per_user_max=2` 限制：10 个人同时走应用会有
  4 个 429；这是准入控制，不是模型限制。要放行 10 路需调高 global_max。
- 模型名限制仍在：该 Key 支持 `deepseek-flash`、`deepseek-v4-pro`；
  `hs-deepseek-flash-latest` 不支持。

完整 10 人并发（真实 RAG，非短请求）：

- 临时 `global_max=10`，创建 10 个临时账号、共享业务手册库，每人一个真实问题；
- **10/10 HTTP 200，0 个 429**；来源均 7–8 条；9 条完整结束、1 条返回部分内容；
- TTFT p50 **127.3s** / p95 **178.6s**；总耗时 p50 134.7s / p95 180.9s；墙钟 181s；
- 模型/Key 没有硬 429，但真实网关排队使 p95 约 3 分钟；应用 10 路准入本身正常；
- 跑完已删除 10 个临时账号、恢复业务手册分享（sugw）；按人要求随后
  **保留 `global_max=10`**，验证 users=4、share 原状、health 200。
- 30 人容量评估：不要把 global_max 直接调到 30；按 10 路实测 p95 179s 外推，
  固定上游容量下 30 路约 3 波、p95 约 9 分钟。30 人场景应增加网关通道/
  个人 Key、降低单次回答成本，或做有界排队+等待提示。

### 2026-11-20 | Claude Code | 有界排队 + 等待提示

- 全局名额满时不再立刻 429，而是进入内存有界 FIFO：
  `ask_limits.queue_max=50`、`queue_timeout=600s`（默认）。
- 排队请求通过 SSE 推送 `queue` 事件：位置/前面人数/预计等待（按回答耗时
  EWMA 估算）；前端显示“排队第 1 位…”或“前面还有 N 人，预计约 X 秒”，
  出队后自动继续原问答流。
- 单用户第 3 个问题仍按 `per_user_max=2` 直接 429；队列满/等待超时才 429。
- 队列与名额释放、客户端断开清理、超时回收都在同一套 slot 逻辑里；
  `ask_limits_state` 增加 `queue_len` / `avg_answer_seconds`。
- 实测（临时 global_max=1，管理员 + sugw 两个真实用户）：
  第一人 79s 完成；第二人收到 39 次排队状态（120→112s），随后自动开始，
  总 106s 返回答案。已恢复 global_max=10。
- 已部署 `rag-bff:20261120-queue2`。

### 2026-11-20 | Claude Code | 界面使用文档重写

- 「使用文档 → 用户手册」整块重写，覆盖：3 分钟快速开始、注册/登录、
  知识库选择/新建/上传/索引/共享、提问/引用/预览/上下文/回答长度/排队、
  模拟答辩（两种模式/题数/推荐库/报告）、管理员用户管理与系统参数、
  个人 Key/模型、常见问题与使用注意。
- 「项目背景与说明」中过时的“访问口令/key 隔离”改为账号密码 + token +
  用户/共享隔离。
- 已部署 `rag-bff:20261120-docs`，页面已验证新章节存在。

### 2026-11-22 | Claude Code | 培训材料：知识库问答与模拟答辩拆成两个独立场景

- 培训 PPT 与现场演示脚本不再把“知识库 → 模拟答辩”讲成一条流水线：
  场景 A = 项目交接/日常查资料；场景 B = 培训考核/导师提问/自测。
  入口、权限、产出分别讲；成功标准与会后行动也按 A/B 分开。
- 场景 B 的现场素材改用真实《谢涵宇-转正答辩.pptx》（26 页），放在
  `training/demo/`；虚构版 `答辩演示-智能交易平台.pptx` 保留作对外备用。
  脚本中注明真实 PPT 含客户名，仅限内网培训使用。
- 已重新生成 `training/项目智能问答助手-使用培训.pptx` / `.pdf`（21 页）。
- 简易使用说明与「使用文档 → 用户手册」各加一段“两个独立场景”说明。
  **index.html 本次改动尚未重新构建镜像**（线上仍为 `20261120-docs`）。
- 培训 PPT 加入真实界面截图：登录、入口选择、新建知识库、问答+引用、
  原文高亮预览、答辩诊断、用户/权限分配、答辩上传页。截图账号与邀请码已脱敏；
  原始截图放在 `training/assets/`。
- PPT 第 19 页起改为「附录 1/2/3」（成功标准、FAQ、会后行动），备注明确
  “不现场讲”；演示到 Demo B 结束即进入答疑。PDF 已同步更新。

### 2026-11-24 | Claude Code | SQL 文件名索引 + 文件夹内 zip 自动解压 + 使用提示弹窗

- `index_docs.py`：`.sql` 加入 EXTRACTORS，只返回「SQL 脚本文件：<文件名>」，
  不读脚本内容；SQL 走 `_plain_chunks`，避免短块被过滤。需要重建索引才生效。
- `app/indexer.py`：索引时自动扫描源目录/上传目录里的 `.zip`，安全解压到
  `upload/.unzipped/<zip名-hash>/`，内部支持的文档并入索引；支持最多 3 层嵌套；
  限制 2 万文件/2GB/单文件 500MB/压缩比 150，拒绝密码、路径穿越、符号链接；
  zip 未变时复用上次解压；源目录不改动；zip 内文件预览走 upload 根。
- `app/static/index.html`：进入知识库弹出「实验推广阶段」提示：性能有限、并发会排队、
  回答不好请点踩写原因；提供“不再显示”勾选（按用户 localStorage 记忆）和关闭按钮。
  用户手册补充 zip/SQL 说明。
- 以上仅改本地代码，**未部署、未重启、未重建索引**；生效需要新镜像 + 重建相关库。

### 2026-11-25 | Claude Code | 答辩难度 + 关键词直练（未部署前记录）

- 答辩新增「出题难度」：基础（默认）/ 标准 / 挑战。基础按“实施工程师带新人”视角：
  第一题不问 PPT 某页细节，业务只问概念/皮毛，实施操作可正常问步骤/菜单/配置/结果核对；
  基础难度同一话题最多追 1 次（标准/挑战仍为 2 次）。
- 新增「关键词直练」：不上传 PPT，输入学习关键词，后端在用户有答辩权限的知识库里
  用 BM25 自动匹配（也可手选库），取命中片段作为学习材料，再走导师提问/追问/诊断。
- 接口新增 `POST /api/defense/start_keywords`；`/api/defense/start` 增加 `difficulty`。
- `app/static/defense.html` 增加 PPT/关键词入口切换、难度按钮、关键词面板。
- 用户手册/简易说明补充难度与关键词模式。

### 2026-11-25 | Claude Code | 已部署：答辩难度 + 关键词直练 + SQL/ZIP + 提示弹窗

- 镜像：`rag-bff:20261125-def-kw`（首版）→ `rag-bff:20261125-def-kw2`（修正自动匹配/基础难度）。
- 切换前 `/api/health` active=0、queue=0；切换后 health 200，容器 healthy。
- 生产验证：
  - 进入知识库弹窗正常显示「实验推广阶段 / 性能有限 / 点踩写原因」，可勾选不再显示；
  - 关键词直练「交易所指令 下单 审核 分发」自动匹配业务手册，基础难度第一题是
    “分发在哪个菜单、分发前维护什么”，不再一上来问深层业务；
  - PPT 模式基础难度第一题是“装完从哪里确认 5 个版本包都装上了”，属实施核对。
- 自动匹配规则：关键词命中库名优先；业务词优先业务手册；再做按库规模归一化的
  BM25 排序（避免大项目库永远抢赢）。
- 回滚：`/opt/rag/.env` 改回 `RAG_TAG=20261120-docs`（或 `20261125-def-kw`），
  `docker-compose -f compose.yml up -d --pull never`；数据卷不动。
- 备份：`/opt/rag/backup/20261125-def-kw/`、`/opt/rag/backup/20261125-def-kw2/`。
- SQL 文件名索引 / 文件夹内 zip 自动解压已随镜像上线，但**存量库要重建索引**才生效；
  未主动重建，避免占用生产。
- 2026-11-25 补丁：答辩页难度按钮下增加小字说明（基础=实施工程师带新人/业务皮毛；
  标准=流程+业务适度追问；挑战=多链路/异常/边界），PPT 与关键词入口各有一份。
  已部署 `rag-bff:20261125-def-kw3`，切换前 active=0、切换后 health 200。
- 2026-11-26 修复：增量索引会把“旧版已跳过、现在已支持”的 SQL 文件当成未变跳过
  （签名在、但旧块为空）。改为：SQL 文件无旧块时强制补解析。已部署
  `rag-bff:20261126-sql-backfill`；etm 库重建后 added=48、total_chunks 3141→3189，
  SQL 文件名可检索（如“交易异常监控开通脚本.sql”）。生产扫描未发现 zip 文件。

### 2026-11-26 | Claude Code | 答辩评分口径放宽（待部署）

- `app/defense_coach.py`：`_SCORE_SYSTEM` 改为新人标准——方向对、大致说到点子上
  → 60-74 分起步；部分正确 40-59；关键词沾边 25-45；20 分以下仅限不会/敷衍/空答。
  评为“不合格”的回答要在 comment/gaps 点出缺什么，而不是靠压分体现。
  历史导师认为不行的回答上限 85；与手册矛盾才扣到 60 以下。
- `_ANALYSIS_SYSTEM` / `_DIAG_SYSTEM`：解析先肯定答对的部分，再点到具体缺漏。
- 本地冒烟（3 题）：raw 70/72/45，折算 23.3/24.0/15.0，总分 62.3「合格」，
  第 3 题含糊回答明显低于前两题，档次仍能拉开。
