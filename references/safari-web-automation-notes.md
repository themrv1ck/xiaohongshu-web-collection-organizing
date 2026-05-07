# Safari 小红书网页端自动化补充

## 适用场景
当 Chrome 未登录小红书、用户明确要求“用 Safari”，或 Chrome 端自动化前提不满足时，可以在 macOS 上用 Safari + AppleScript `do JavaScript` 继续操作。不要因 SKILL.md 原先写 Chrome 前提就停止；先验证 Safari 是否已登录。

## 稳定探测
- 打开：`https://www.xiaohongshu.com/explore`
- 用户态特征：侧边栏/底部出现“我”，个人主页能打开 `/user/profile/<id>`，页面显示昵称/收藏/专辑。
- 未登录特征：页面文本出现“手机号登录 / 登录后推荐 / 马上登录即可 / 扫码”。

## AppleScript 执行模式
短 JS 可直接：
```bash
osascript -e 'tell application "Safari" to do JavaScript "document.title" in current tab of front window'
```

复杂 JS 不要塞进一行 shell，容易遇到引号/括号转义错误。更稳定的方式：
1. 把 JS 写入临时 `.js` 文件。
2. AppleScript 读取文件内容。
3. `do JavaScript jsCode in current tab of front window`。

示例 Python wrapper：
```python
import subprocess, tempfile, os

def safari_js(js: str) -> str:
    fd, path = tempfile.mkstemp(suffix='.js')
    os.write(fd, js.encode())
    os.close(fd)
    script = f'''set jsCode to read POSIX file "{path}"
tell application "Safari"
  do JavaScript jsCode in current tab of front window
end tell
'''
    res = subprocess.run(['osascript'], input=script, text=True, capture_output=True)
    os.unlink(path)
    if res.returncode:
        raise RuntimeError(res.stderr.strip() or res.stdout.strip())
    return res.stdout.strip()
```

## 创建专辑 UI 路径
1. 进入个人主页 `/user/profile/<id>`。
2. 点击顶层“收藏”tab：`.reds-tab-item.sub-tab-list` 中文本为“收藏”。
3. 点击二级“专辑”tab：`.sub-tab-list .reds-tab-item, .tertiary.left .reds-tab-item` 中文本为“专辑・N”。
4. 点击 `.create-board`。
5. 在 `.reds-modal-open .modal` 内填写：
   - `input.input-content`：专辑标题，例如 `hermes`
   - `textarea.textarea-content`：简介
   - 点击 `.btn.done`
6. 验证页面文本出现 `hermes\n笔记・0` 或 board card 中出现 `hermes`。

## 收藏页/专辑页结构观察
- 一级收藏页会同时展示“笔记・N / 专辑・N / 文件・N”。
- 笔记卡片常用：`.tab-content-item .feeds-container .title`、`.author-wrapper .author`、`.author-wrapper .count`。
- 专辑卡片常用：`.board-card`、`.board-name .title`、`.desc`。

## 注意事项
- Safari 的 JXA `Application("Safari").doJavaScript(...)` 可能不回显；传统 AppleScript `tell application "Safari" to do JavaScript ...` 更可靠。
- 页面可能弹出“广告屏蔽插件”提示；这是 UI 遮挡，不代表未登录。优先点击“我知道了”，必要时先处理遮挡再继续。
- 不要泄露 cookies、xsec_token、signed URL；日志和回复只描述页面状态，不复制敏感参数。
- `.collect-wrapper` 在 Safari 中可触发收藏/取消收藏，但是否出现“加入专辑”banner 需页面实测；若 banner 未出现，不要宣称已入目标专辑，只能记录为待重试。