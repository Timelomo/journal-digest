# 部署指南（GitHub Actions）

预计用时 15–20 分钟。全程只需要浏览器；第 2 步可选用命令行。

---

## 第 0 步：准备账号与 API key

至少准备 **一个** 大模型 API key。三家都配置时，按 `config.yaml` 中的顺序使用，前一家失败会自动切换到下一家。

| 提供方 | 获取地址 | Secret 名称 | 备注 |
|---|---|---|---|
| Claude | https://console.anthropic.com → API Keys | `ANTHROPIC_API_KEY` | 需绑卡充值 |
| OpenAI | https://platform.openai.com/api-keys | `OPENAI_API_KEY` | 需绑卡充值 |
| DeepSeek | https://platform.deepseek.com → API keys | `DEEPSEEK_API_KEY` | 支持支付宝/微信充值，价格最低 |

> 提示：key 只在创建时显示一次，请先复制到安全的地方。

推荐的免费 key：

- **OpenAlex**：在 https://openalex.org 注册后，到 Settings → API 复制 key，填入 `OPENALEX_API_KEY`。用于补全摘要，免费额度提高 10 倍。

可选：

- **Elsevier**：在 https://dev.elsevier.com 用学校邮箱注册，填入 `ELSEVIER_API_KEY`。用于补全 EJOR、C&OR 等期刊的摘要。
- **Semantic Scholar**：在 https://www.semanticscholar.org/product/api 申请，填入 `S2_API_KEY`。

---

## 第 1 步：创建 GitHub 仓库

1. 登录 https://github.com（没有账号先注册）。
2. 点右上角 **+** → **New repository**。
3. Repository name 填 `or-journal-digest`，选择 **Private**（私有仓库每月有 2000 分钟免费 Actions 额度，本项目每次运行约 2–5 分钟）。
4. **不要**勾选 "Add a README"，直接点 **Create repository**。

---

## 第 2 步：上传代码

### 方式 A：命令行（推荐）

```bash
unzip or-journal-digest.zip
cd or-journal-digest
git init -b main
git add .
git commit -m "init"
git remote add origin https://github.com/<你的用户名>/or-journal-digest.git
git push -u origin main
```

### 方式 B：网页上传（或者使用桌面端Github）

1. 在新仓库页面点 **uploading an existing file**。
2. 把解压后文件夹里的**所有内容**拖进去，然后点 **Commit changes**。
3. ⚠️ **检查 `.github` 文件夹是否已上传**。macOS 和 Windows 默认隐藏以 `.` 开头的文件夹，拖拽时很容易漏掉。如果仓库里看不到 `.github/workflows/digest.yml`：
   - 点 **Add file → Create new file**；
   - 文件名一栏输入 `.github/workflows/digest.yml`（输入 `/` 会自动创建目录）；
   - 把本地 `digest.yml` 的内容粘贴进去，点 **Commit changes**。

---

## 第 3 步：修改配置 `config.yaml`

可以在 GitHub 网页上点开 `config.yaml`，再点右上角的 ✏️ 编辑：

1. `contact_email`：改成你的邮箱。
2. `journals`：增删期刊。`enabled: false` 表示暂停追踪该期刊。
3. `research_interests`：已按“鲁棒优化 / 两阶段随机规划 / 深度强化学习”写好，可以继续细化。
4. `llm.providers`：调整顺序即调整优先级；模型名称可按需修改（见文件内注释）。

改完后点 **Commit changes**。

---

## 第 4 步：添加 Secrets

进入仓库的 **Settings → Secrets and variables → Actions → New repository secret**，逐个添加：

| Name | 是否必需 | 示例 |
|---|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | 至少一个 | `sk-...` |
| `OPENALEX_API_KEY` | 推荐 | |
| `ELSEVIER_API_KEY`、`S2_API_KEY` | 可选 | |
| `SMTP_HOST` | 发邮件才需要 | `smtp.gmail.com` / `smtp.qq.com` / `smtp.163.com` |
| `SMTP_PORT` | 发邮件才需要 | `465` |
| `SMTP_USER` | 发邮件才需要 | 发件邮箱地址 |
| `SMTP_PASSWORD` | 发邮件才需要 | **应用专用密码或授权码，不是登录密码** |
| `MAIL_TO` | 发邮件才需要 | 收件地址，多个地址用英文逗号分隔 |

邮件相关的五项必须全部填写才会发送。各邮箱的密码获取方式：

- **Gmail**：先开启两步验证，再到 https://myaccount.google.com/apppasswords 生成 16 位应用专用密码。
- **QQ 邮箱**：网页版 → 设置 → 账号 → 开启 “POP3/SMTP 服务”，获取授权码。
- **163 邮箱**：设置 → POP3/SMTP/IMAP → 开启 SMTP，获取授权码。

可选：如果想长期固定优先使用某一家（比如 DeepSeek），在同一页面切到 **Variables** 标签，新建变量 `LLM_PROVIDER`，值填 `deepseek`。

---

## 第 5 步：开启写权限

**Settings → Actions → General → Workflow permissions** → 选择 **Read and write permissions** → **Save**。

（程序需要把数据库和周报提交回仓库，没有写权限时最后一步会报 403。）

---

## 第 6 步：测试运行

进入仓库的 **Actions** 标签。第一次进入时如果出现提示，点 **I understand my workflows, go ahead and enable them**。左侧选择 **OR Journal Digest**，右侧点 **Run workflow**：

1. **先测 API key**：mode 选 `test-llm`，点运行。约 1 分钟后点进运行记录，展开 “Run digest” 查看日志：
   
   ```
   ✓ claude     claude-sonnet-5-5      相关度=5 标签=['分布鲁棒优化', '两阶段/多阶段随机规划']
   - openai     跳过（未设置 OPENAI_API_KEY）
   ✓ deepseek   deepseek-flash         相关度=5 标签=['分布鲁棒优化', ...]
   ```
   出现 ✗ 时，按下面的“常见问题”排查。
2. **正式运行**：mode 选 `run`，点运行。等待 3–5 分钟后：
   
   - 仓库里会出现 `reports/latest.md`，点开即可阅读周报；
   - 如果配置了邮箱，会收到一封周报邮件。

---

## 第 7 步：确认定时任务

默认每周一北京时间 08:30 自动运行。要修改时间，编辑 `.github/workflows/digest.yml`：

```yaml
- cron: '30 0 * * 1'    # 分 时 日 月 周（UTC 时间，北京时间需减 8 小时）
# 每天 08:30：'30 0 * * *'
# 周一和周四：'30 0 * * 1,4'
```

---

## 常见问题

| 现象 | 原因与解决 |
|---|---|
| `test-llm` 显示 `AuthenticationError 401` | key 填错或已失效；Secret 名称必须完全一致（区分大小写） |
| `NotFoundError 404` / `model not found` | 模型名称有误或账号无权访问该模型，修改 `config.yaml` 中的 `model` |
| `RateLimitError 429` / `insufficient_quota` | 余额不足，需要充值；程序会自动切换到下一家 |
| 最后一步 `git push` 报 403 | 没做第 5 步 |
| Actions 页面看不到 workflow | `.github/workflows/digest.yml` 没有上传（见第 2 步方式 B） |
| 运行成功但没有收到邮件 | 邮件 Secret 没有填全；或密码填了登录密码而不是授权码；也请检查垃圾邮件箱 |
| 日志显示 “没有需要推送的新文章” | 正常现象。本周确实没有新文章，或缺摘要的论文还在等待期内 |
| 想补抓过去某段时间的论文 | 手动运行时在 since 中填写日期，如 `2026-09-01` |
| 定时任务没有准点运行 | GitHub 高峰期会延迟几十分钟，属于正常现象 |
| 定时任务被自动停用 | 仓库长时间没有活动时可能被停用，到 Actions 页面重新启用即可 |

## 在本地运行（可选）

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-...        # Windows PowerShell：$env:DEEPSEEK_API_KEY="sk-..."
python -m digest check                # 校验期刊 ISSN
python -m digest test-llm             # 测试大模型 key
python -m digest run --no-llm         # 预览（不调用大模型）
python -m digest run --no-email       # 完整运行
```
