[English](README.md) | **简体中文**

# MailRadar(邮件自动化与信息监控 Agent)

**一个自托管的邮件自动化与信息监控 Agent。** 它把你的邮箱和一张结构化联系人表联动起来:自动归档来信、回写发送/回复状态、管理 IMAP 草稿箱、把通知分发到 **ntfy** 和 **微信**,并按计划驱动无头 LLM 的「雷达」任务。

MailRadar 围绕一条原则构建:**agent 负责准备,人负责决定。** 它绝不代替你发送邮件或消息——它的产出止步于一个草稿箱或一条通知,每一个不可逆的步骤都等人来拍板。

```
邮箱 (IMAP)  ──►  联系人表 (CSV)  ──►  状态回写
      │                    │
      │                    └──►  草稿箱(只追加、按主题去重)
      │
      └──►  雷达(无头 LLM)  ──►  去重  ──►  ntfy / 微信通知
```

## 为什么做这个

大多数「邮件助手」要么全自动(迟早发出你不是那个意思的东西),要么全程手动。MailRadar 走中间路线:它做那些繁琐、可逆的活——归档、关联、跟踪、监控——然后交给你一个干净的草稿箱和一份摘要。发送按钮始终在你手里。

## 功能

- **邮箱 ↔ 联系人关联。** 任何带邮箱列的 CSV 都能变成一张活的联系人表。来信按地址匹配,归档到 `archive/<联系人>/<年月>/`;`sent` / `replied` / `status` 列就地回写。
- **增量、幂等的同步。** 按文件夹做 UID 断点,重跑只碰新邮件;`--full` 全量重扫也不会重复归档。
- **草稿箱管理。** 解析本地 markdown 草稿并追加到 IMAP 草稿箱,按(主题,收件人)去重,可安全重复运行。
- **信息雷达。** 一个无头 LLM 任务按计划扫描监控清单,按内容哈希去重,只推送真正新增的条目。
- **多渠道通知。** ntfy 做可靠的手机推送;微信则通过 [OpenClaw](https://docs.openclaw.ai) 网关投递。
- **人机协同是设计出来的。** 不自动发送、不自动删你的数据;破坏性操作默认 dry-run。

## 快速开始

```bash
git clone <你的仓库> && cd MailRadar
pip install -e .

cp config.example.yaml config.yaml   # 编辑 imap 与 contacts
cp .env.example .env                 # 设置 MAILRADAR_IMAP_PASSWORD

# 把示例联系人表放到位
mkdir -p contacts && cp examples/contacts.csv contacts/

python -m mailradar sync --dry-run   # 预览
python -m mailradar sync             # 归档 + 回写
```

## 配置

一切都在 `config.yaml` 里(见 [`config.example.yaml`](config.example.yaml))。IMAP 密码从 `MAILRADAR_IMAP_PASSWORD`(环境变量或 `.env`)读取,绝不写进提交的文件。列名完全可重映射,所以同一套引擎既能用 `sent`,也能用 `已发送` 或 `contacted`。

## 微信投递(OpenClaw)

如果你跑着一个带微信频道的 OpenClaw 网关,MailRadar 可以把通知直接送进你的微信会话:

```yaml
notify:
  channels: ["ntfy", "weixin"]
  weixin:
    enabled: true
    channel: "openclaw-weixin"
    to: "your-user@im.wechat"
```

```bash
python -m mailradar notify "每日摘要" "3 条待跟进" --channels weixin
```

底层是 shell 出去调
`openclaw agent -m <msg> --channel openclaw-weixin --to <user> --deliver`。
反向——在微信里给 MailRadar 下指令——就是把一个 OpenClaw agent 指向 `mailradar` CLI 当工具用。

## 雷达(定时监控)

```bash
# 在 radar/sources.md 里填好监控清单,然后:
radar/run_task.sh scan.md scan.log        # 单次
# 或挂定时——见 deploy/mailradar-radar.{service,timer}
```

提示词(`radar/prompts/scan.md`)刻意保守:只读子代理、按内容哈希去重、「子代理没返回的条目绝不虚构」、只对真正新增的条目发通知。

## 命令行

```
python -m mailradar sync [--full] [--dry-run] [--no-commit]
python -m mailradar push <draft.md>... [--apply] [--sender-name NAME]
python -m mailradar scan-box                 # 草稿箱只读 JSON 快照
python -m mailradar notify "标题" "正文" [--channels ntfy,weixin]
```

## 项目结构

```
mailradar/          # 引擎(config, imap, contacts, sync, drafts, notify, cli)
radar/              # 定时无头 LLM 监控(run_task.sh, prompts, sources.md)
skills/             # 人机协同技能契约(先评估后行动)
deploy/             # sync 与 radar 的 systemd 单元
examples/           # 示例联系人表与草稿
tests/              # 纯逻辑核心的单元测试
```

## 设计说明

- **草稿只追加,默认 dry-run。** `push` 和每条破坏性路径都先预览;`--apply` 才真执行。
- **原子写。** 状态文件和 JSON 都走 tmp+rename,崩溃绝不会留下被读成空的半截文件。
- **通知 best-effort。** 某个渠道失败只记录、不抛异常——通知出问题不会拖垮触发它的同步/雷达任务。
- **配置优于约定。** 路径、列名、文件夹、渠道全是数据而非硬编码,引擎因此保持通用。

## 测试

```bash
python -m unittest discover -s tests   # 或:pytest -q
```

## 许可

[MIT](LICENSE)
