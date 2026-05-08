#!/usr/bin/env python3
import html
import json
import re
import subprocess
import sys
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs


HOST = '127.0.0.1'
PORT = 8765
ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = Path.cwd() / 'webui_runs' / 'latest'
MAX_BODY_BYTES = 20 * 1024 * 1024
SENSITIVE_RE = re.compile(r'(cookie|token|xsec|signed)', re.IGNORECASE)


def ensure_run_dir() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)


def redact_text(value: str) -> str:
    value = re.sub(r'([?&][^=\s]*(?:token|xsec|signed)[^=\s]*=)[^&\s]+', r'\1[redacted]', value, flags=re.IGNORECASE)
    value = re.sub(r'((?:cookie|token|xsec|signed)[^:=\s]*\s*[:=]\s*)[^\s,;}]+', r'\1[redacted]', value, flags=re.IGNORECASE)
    return value


def scrub_json(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SENSITIVE_RE.search(str(key)):
                result[key] = ''
            else:
                result[key] = scrub_json(item)
        return result
    if isinstance(value, list):
        return [scrub_json(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def read_json_text(text: str, label: str):
    text = text.strip()
    if not text:
        return None
    try:
        return scrub_json(json.loads(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f'{label} 不是合法 JSON：{exc.msg}') from exc


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def run_script(script: str, *args: str, timeout: int = 600):
    proc = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / script), *args],
        cwd=str(Path.cwd()),
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        'returncode': proc.returncode,
        'stdout': redact_text(proc.stdout.strip()),
        'stderr': redact_text(proc.stderr.strip()),
    }


def summarize_report(path: Path):
    if not path.exists():
        return {'error': f'找不到 {path}'}
    report = load_json(path)
    statuses = Counter(item.get('status', 'unknown') for item in report.get('processed', []))
    return {
        'mode': report.get('mode'),
        'visible_count': report.get('visible_count'),
        'processed_count': len(report.get('processed', [])),
        'status_counts': dict(sorted(statuses.items())),
        'error_count': len(report.get('errors', [])),
        'missing_boards': report.get('missing_boards', []),
        'report': str(path),
    }


def render_json_block(data) -> str:
    if data is None:
        return ''
    return '<pre>%s</pre>' % html.escape(json.dumps(data, ensure_ascii=False, indent=2))


def render_output(result) -> str:
    if not result:
        return ''
    blocks = []
    if 'message' in result:
        blocks.append('<p><strong>%s</strong></p>' % html.escape(result['message']))
    if result.get('summary') is not None:
        blocks.append(render_json_block(result['summary']))
    if result.get('command') is not None:
        blocks.append('<p class="muted">命令：%s</p>' % html.escape(result['command']))
    for key in ('stdout', 'stderr'):
        if result.get(key):
            blocks.append('<h3>%s</h3><pre>%s</pre>' % (key, html.escape(result[key])))
    if result.get('error'):
        blocks.append('<p class="error">%s</p>' % html.escape(result['error']))
    return '<section class="result">%s</section>' % ''.join(blocks)


def page(result=None) -> str:
    ensure_run_dir()
    run_path = html.escape(str(RUN_DIR))
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>小红书收藏整理 WebUI</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f7f7f4; color: #1f2328; }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 26px; margin: 0 0 8px; }}
    h2 {{ font-size: 18px; margin: 0 0 12px; }}
    h3 {{ font-size: 14px; margin: 12px 0 6px; }}
    section {{ background: #fff; border: 1px solid #d8d8d0; border-radius: 8px; padding: 16px; margin: 14px 0; }}
    textarea {{ width: 100%; min-height: 180px; box-sizing: border-box; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }}
    label {{ display: block; margin: 10px 0 6px; font-weight: 600; }}
    input[type="text"], select {{ padding: 8px; border: 1px solid #bbb; border-radius: 6px; }}
    button {{ padding: 9px 14px; border: 0; border-radius: 6px; background: #1f6feb; color: #fff; cursor: pointer; }}
    button.secondary {{ background: #57606a; }}
    button.danger {{ background: #b42318; }}
    .muted {{ color: #57606a; font-size: 13px; }}
    .error {{ color: #b42318; font-weight: 700; }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #f1f3f5; padding: 12px; border-radius: 6px; }}
  </style>
</head>
<body>
<main>
  <h1>小红书收藏整理 WebUI</h1>
  <p class="muted">本地入口只写入 <code>{run_path}</code>。默认流程只做 dry-run；真实执行必须输入确认文本。</p>
  {render_output(result or {})}

  <section>
    <h2>1. 环境检测</h2>
    <form method="post" action="/env">
      <button type="submit">检测环境</button>
    </form>
  </section>

  <section>
    <h2>2. 上传或粘贴 JSON，然后分类 dry-run</h2>
    <form method="post" action="/classify">
      <label for="visible_file">visible_items JSON 文件</label>
      <input id="visible_file" type="file" accept=".json,application/json">
      <label for="visible_json">visible_items JSON 内容</label>
      <textarea id="visible_json" name="visible_json" required></textarea>

      <label for="inventory_file">可选：existing_boards_inventory JSON 文件</label>
      <input id="inventory_file" type="file" accept=".json,application/json">
      <label for="inventory_json">可选：existing_boards_inventory JSON 内容</label>
      <textarea id="inventory_json" name="inventory_json"></textarea>

      <label><input type="checkbox" name="include_existing_boards" value="1"> 纳入已有专辑内容</label>
      <button type="submit">运行分类 dry-run</button>
    </form>
  </section>

  <section>
    <h2>3. 查看 run_report 摘要 / 生成 retry queue</h2>
    <div class="row">
      <form method="post" action="/summary"><button class="secondary" type="submit">查看摘要</button></form>
      <form method="post" action="/retry"><button class="secondary" type="submit">生成 retry queue</button></form>
    </div>
  </section>

  <section>
    <h2>4. 真实执行</h2>
    <p class="error">真实执行会调用小红书前端移动接口，可能改动你的收藏专辑。先确认 dry-run 报告、目标专辑和排除清单。</p>
    <form method="post" action="/execute">
      <label>浏览器</label>
      <select name="browser">
        <option value="auto">auto</option>
        <option value="chrome">chrome</option>
        <option value="safari">safari</option>
        <option value="playwright">playwright</option>
      </select>
      <label><input type="checkbox" name="execute_ack" value="1"> 我确认要真实移动收藏</label>
      <label for="confirm_text">输入 EXECUTE</label>
      <input id="confirm_text" type="text" name="confirm_text" autocomplete="off">
      <label><input type="checkbox" name="allow_low_confidence" value="1"> 允许 low confidence 条目执行</label>
      <button class="danger" type="submit">执行真实移动</button>
    </form>
  </section>
</main>
<script>
function bindFile(inputId, textareaId) {{
  const input = document.getElementById(inputId);
  const area = document.getElementById(textareaId);
  input.addEventListener('change', () => {{
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {{ area.value = String(reader.result || ''); }};
    reader.readAsText(file);
  }});
}}
bindFile('visible_file', 'visible_json');
bindFile('inventory_file', 'inventory_json');
</script>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond(page())

    def do_POST(self):
        try:
            result = self.handle_post()
        except Exception as exc:
            result = {'error': str(exc)}
        self.respond(page(result))

    def read_form(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError('提交内容太大')
        body = self.rfile.read(length).decode('utf-8')
        return {key: values[-1] for key, values in parse_qs(body, keep_blank_values=True).items()}

    def handle_post(self):
        ensure_run_dir()
        form = self.read_form()
        if self.path == '/env':
            result = run_script('check_environment.py')
            if result['returncode'] == 0 and result['stdout']:
                write_json(RUN_DIR / 'check_environment.json', json.loads(result['stdout']))
                result['summary'] = load_json(RUN_DIR / 'check_environment.json')
            result['message'] = '环境检测完成'
            result['command'] = 'python3 scripts/check_environment.py'
            return result

        if self.path == '/classify':
            visible = read_json_text(form.get('visible_json', ''), 'visible_items')
            if visible is None:
                raise ValueError('必须提供 visible_items JSON')
            write_json(RUN_DIR / 'visible_items.json', visible)

            classify_args = [str(RUN_DIR / 'visible_items.json'), str(RUN_DIR / 'classification.json'), '--skip-ocr']
            inventory = read_json_text(form.get('inventory_json', ''), 'existing_boards_inventory')
            if inventory is not None:
                write_json(RUN_DIR / 'existing_boards_inventory.json', inventory)
                classify_args.extend(['--existing-boards-inventory', str(RUN_DIR / 'existing_boards_inventory.json')])
                if form.get('include_existing_boards') == '1':
                    classify_args.append('--include-existing-boards')

            classify = run_script('classify_items.py', *classify_args)
            if classify['returncode'] != 0:
                classify['message'] = '分类失败'
                return classify
            dry = run_script('run_reassign_batch.py', str(RUN_DIR / 'classification.json'), str(RUN_DIR / 'run_report.json'))
            dry['message'] = '分类 dry-run 完成'
            dry['command'] = 'python3 scripts/classify_items.py --skip-ocr ... && python3 scripts/run_reassign_batch.py ...'
            dry['summary'] = summarize_report(RUN_DIR / 'run_report.json') if dry['returncode'] == 0 else None
            return dry

        if self.path == '/summary':
            return {'message': 'run_report 摘要', 'summary': summarize_report(RUN_DIR / 'run_report.json')}

        if self.path == '/retry':
            result = run_script('build_retry_queue.py', str(RUN_DIR / 'run_report.json'), str(RUN_DIR / 'retry_queue.json'))
            result['message'] = 'retry queue 已生成'
            result['command'] = 'python3 scripts/build_retry_queue.py webui_runs/latest/run_report.json webui_runs/latest/retry_queue.json'
            if result['returncode'] == 0:
                result['summary'] = {'retry_count': len(load_json(RUN_DIR / 'retry_queue.json')), 'retry_queue': str(RUN_DIR / 'retry_queue.json')}
            return result

        if self.path == '/execute':
            if form.get('execute_ack') != '1' or form.get('confirm_text', '').strip() != 'EXECUTE':
                return {'error': '没有勾选确认，或确认文本不是 EXECUTE。不会执行真实移动。'}
            classification = RUN_DIR / 'classification.json'
            if not classification.exists():
                return {'error': '找不到 classification.json。请先运行分类 dry-run。'}
            browser = form.get('browser') or 'auto'
            execute_args = [str(classification), str(RUN_DIR / 'run_report.json'), '--execute', '--browser', browser]
            if form.get('allow_low_confidence') == '1':
                execute_args.append('--allow-low-confidence')
            result = run_script('run_reassign_batch.py', *execute_args, timeout=1800)
            result['message'] = '真实执行已结束'
            result['command'] = 'python3 scripts/run_reassign_batch.py webui_runs/latest/classification.json webui_runs/latest/run_report.json --execute'
            result['summary'] = summarize_report(RUN_DIR / 'run_report.json') if (RUN_DIR / 'run_report.json').exists() else None
            return result

        return {'error': '未知路径'}

    def respond(self, content: str):
        data = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s\n' % (fmt % args))


def main() -> None:
    ensure_run_dir()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'WebUI: http://{HOST}:{PORT}')
    print(f'Output: {RUN_DIR}')
    server.serve_forever()


if __name__ == '__main__':
    main()
