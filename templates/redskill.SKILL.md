---
name: xiaohongshu-web-collection-organizing
description: "整理用户本人已登录的小红书收藏或点赞内容：先只读采集、分类并展示 dry-run，只有用户明确确认方案后才移动收藏到专辑。不得发布笔记、评论、点赞或取消点赞、关注、私信、删除收藏或自动运营账号。WorkBuddy 使用公开 GitHub Plugin 和独立可见浏览器；其他宿主须在用户同意后安装同一公开仓库的完整 Skill。"
---

# 小红书收藏整理

发布版本：`{{VERSION}}`。

只处理用户本人明确选择的收藏、点赞或专辑整理。默认只读；真实写入前必须展示逐条方案并再次确认。

## 当前安全停机

读取专辑成员、创建专辑和移动笔记已停用。旧实现会探测小红书网页内部模块，该动作与自动化会话进入安全验证错误页高度相关。任何宿主和模型收到整理请求时都必须在打开浏览器前停止，不得调用 WorkBuddy 登录/抓取工具、专辑快照、创建或移动脚本，也不得换用其他私有接口或自定义模块扫描。离线处理既有工件不受影响。

## 禁止范围

- 不发布、编辑或删除笔记。
- 不评论、回复、点赞、取消点赞、关注、取关或发送私信。
- 不自动运营账号，不定时运行，不绕过登录、安全验证或访问限制。
- 不读取系统浏览器 Cookie，不接管用户日常浏览器资料目录。
- 不把 Cookie、token、xsec、签名图片地址或完整 URL query 返回模型、写入报告或发送到第三方。
- 不删除收藏。跨专辑移动必须保持最终收藏状态；任何状态不确定都立即停止。

## 权限说明

只有在需要时申请以下权限，并在申请前说明用途：

1. **GitHub 网络访问**：仅在用户回复“启用”后，从公开仓库 `themrv1ck/xiaohongshu-web-collection-organizing` 安装或更新 `xiaohongshu-organizer@xiaohongshu-skill-marketplace`。
2. **小红书网络访问**：仅在用户同意打开浏览器后访问小红书页面与图片，用于读取用户选择的范围；不得向其他服务上传收藏内容。
3. **本地文件写入**：只写 WorkBuddy 插件配置和本次私有运行目录；中间文件必须限制为当前用户可读写。
4. **账号写入**：仅在用户确认 dry-run 后创建已列明的专辑、把已列明的收藏移动到已列明的目标专辑；不得扩大范围。

## 运行入口

先检查工具集中是否同时存在：

- `xhs_workbuddy_status`
- `xhs_workbuddy_setup`
- `xhs_workbuddy_login`
- `xhs_workbuddy_capture`
- `xhs_workbuddy_prepare`
- `xhs_workbuddy_execute`

六个工具齐全时，必须走 WorkBuddy 路径，不得自行调用 Safari、Arc、Chrome、Edge、CDP、AppleScript、Playwright、Computer Use 或普通终端浏览器脚本。

六个工具不齐全但存在 `WORKBUDDY_CONFIG_DIR` 时，只说明：

> 小红书插件需要一次性启用。它会从公开 GitHub 仓库安装唯一的 `xiaohongshu-organizer`，不会启用其他插件。回复“启用”后安装，随后只需完全退出并重开一次 WorkBuddy。

只有用户明确回复“启用”后，才运行：

```bash
python3 scripts/enable_workbuddy_mcp.py --install-plugin
```

安装器返回 `restart_required=true` 后停止，让用户重开 WorkBuddy并重发原请求。不得声称安装成功，除非六个工具重新出现且 `xhs_workbuddy_status` 返回 `plugin_version={{VERSION}}`。

如果不是 WorkBuddy 且当前包没有完整运行脚本，必须明确停止。只有用户同意后，才能让宿主通过其标准 GitHub Skill 安装器安装公开仓库 `themrv1ck/xiaohongshu-web-collection-organizing`；不得临时拼接自动化代码或静默下载执行文件。

## 整理流程历史合同（当前不可执行）

### 1. 确认范围

让用户选择：

- 收藏
- 点赞
- 收藏和点赞合并去重

未选择前不打开浏览器。

### 2. 确认深度

让用户选择：

- 快速整理：只用标题、正文、标签和作者。
- 轻度整理：增加图文全部图片 OCR，不分析视频。
- 深度整理：图文 OCR、视频语音和完整时轴画面；WorkBuddy 当前没有完整视频证据入口时必须在打开浏览器前停止。

### 3. 只读采集

取得本轮浏览器授权后调用 `xhs_workbuddy_login`，再把它返回的目标页原样传给 `xhs_workbuddy_capture`。

- 浏览器必须可见并使用插件独立资料目录；登录由用户在窗口中完成。
- 每组最多 200 条，非最后一组默认等待 3 分钟。
- 必须核对页面声明总数、唯一笔记数量与连续位置；不完整时保存已读结果并停止。
- 登录异常、安全验证、页面绑定丢失或数量变化时立即停止，不刷新、不绕过、不自动重试。

### 4. 分类与 dry-run

第一次调用 `xhs_workbuddy_prepare` 只读取得真实专辑清单和脱敏分类输入。只使用工具返回的真实笔记分类：

- 优先使用已有专辑。
- 只有真实内容确实需要时才提议新专辑；不使用预设类别。
- 判断不清的条目保持未分类，不能猜测。
- 轻度 OCR 失败时停止，不能静默退回元数据冒充轻度结果。

第二次调用 `xhs_workbuddy_prepare` 生成 dry-run。只有同时满足以下条件，才可展示为可执行方案：

- `mode=dry_run`
- `ready_for_execute=true`
- `blockers=[]`
- `planned_move_count>0`
- 存在非空 `approval_digest`

向用户列明待创建专辑及隐私、每条收藏的当前位置与目标专辑、移动数量上限。此时不得写账号。

### 5. 明确确认后写入

只有用户确认上述完整方案后，才把原 `approval_digest` 传给 `xhs_workbuddy_execute`。不得修改计划、扩大数量或绕过工具直接执行脚本。

任一条写入失败、页面状态不确定或出现安全提示时，立即停止剩余项目并报告已完成、失败和未处理数量。最后只报告实际核验结果，不承诺百分之百成功。

## 输出要求

用简短中文说明：

- 范围与整理深度
- 抓取、分类和待复核数量
- `mode`、`ready_for_execute`、`blockers`
- 计划或实际移动数量
- 是否写入账号
- 失败或未处理项目

不得在输出中包含凭据、完整私有链接、Cookie、token 或签名媒体地址。
