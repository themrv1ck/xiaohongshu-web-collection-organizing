#!/usr/bin/env python3
import json
import gzip
import subprocess
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from analyze_video_transcripts import (  # noqa: E402
    analysis_input_sha256,
    analysis_prompt,
    build_analysis_rows,
    validate_analysis,
)
from extract_visible_items import apply_arc_collection_content_types, arc_js_macos  # noqa: E402
from transcript_quality import validate_transcript_coverage  # noqa: E402
from transcribe_video_items import (  # noqa: E402
    BatchInfrastructureError,
    build_transcript_rows,
    ensure_mimo_audio_input,
    transcribe_audio_files_with_worker,
)
from video_content_common import (  # noqa: E402
    EXPECTED_SHARD_SIZES,
    MIMO_ASR_REQUIRED_FILES,
    MIMO_VL_MODEL_FILES,
    check_mimo_asr_environment,
    check_mimo_vl_environment,
    combine_arc_login_status,
    find_arc_collection_note_context,
    local_capability_preflight,
    redact_sensitive_text,
    resolve_mimo_vl_root,
    transcript_sha256,
    video_content_environment,
)


class VideoContentTests(unittest.TestCase):
    @staticmethod
    def write_pcm_wav(path, samples, *, sample_rate=16000):
        frames = struct.pack(f'<{len(samples)}h', *samples)
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(frames)
        return frames

    @staticmethod
    def read_pcm_wav(path):
        with wave.open(str(path), 'rb') as handle:
            params = handle.getparams()
            frames = handle.readframes(params.nframes)
        return params, frames

    @staticmethod
    def mimo_runtime(tmp_path):
        tokenizer = tmp_path / 'tokenizer'
        tokenizer.mkdir()
        (tokenizer / 'config.json').write_text(
            json.dumps({'nfft': 960, 'sampling_rate': 24000}),
            encoding='utf-8',
        )
        return SimpleNamespace(tokenizer=tokenizer)

    @staticmethod
    def fake_transcript_module():
        class UserVisibleError(RuntimeError):
            pass

        return SimpleNamespace(UserVisibleError=UserVisibleError)

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / args[0]), *args[1:]],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=True,
        )

    def test_codex_provider_checks_codex_only_when_selected(self):
        failed_extractor = {"ok": False, "returncode": 1, "stdout": "", "stderr": "missing model"}
        logged_in_codex = {"ok": True, "returncode": 0, "stdout": "logged in", "stderr": ""}
        with (
            patch('video_content_common.find_video_transcript_extractor_root', return_value=Path('/tmp/extractor')),
            patch('video_content_common.shutil.which', side_effect=lambda name: f'/usr/bin/{name}'),
            patch('video_content_common.importlib.util.find_spec', return_value=object()),
            patch('video_content_common.run_status', side_effect=[failed_extractor, logged_in_codex]),
            patch('video_content_common.platform.system', return_value='Darwin'),
            patch('video_content_common.platform.machine', return_value='arm64'),
        ):
            environment = video_content_environment(
                browser='safari', analysis_provider='codex-cli', visual_analysis=True,
            )

        self.assertEqual(environment['missing'], ['mimo-mlx-runtime-or-model'])
        self.assertEqual(environment['analysis_provider'], 'codex-cli')
        self.assertTrue(environment['codex_login']['checked'])
        self.assertEqual(environment['capabilities']['text_analysis']['provider'], 'codex-cli')
        self.assertTrue(environment['capabilities']['text_analysis']['ready'])
        self.assertTrue(environment['capabilities']['visual_analysis']['ready'])
        self.assertFalse(environment['capabilities']['asr']['ready'])
        self.assertEqual(environment['mimo_install_consent'], 'granted_by_enable_response')
        self.assertTrue(environment['mimo_install_authorized'])
        self.assertTrue(environment['mimo_install_required'])
        self.assertEqual(
            environment['mimo_install_authorized_components'],
            ['mimo-v2.5-asr-mlx'],
        )
        self.assertEqual(environment['missing_requiring_additional_confirmation'], [])
        self.assertFalse(environment['should_ask_user_to_install_video_content'])

    def test_command_provider_does_not_check_codex_and_requires_other_install_consent(self):
        failed_extractor = {"ok": False, "returncode": 1, "stdout": "", "stderr": "not ready"}

        def which(name):
            return '' if name == 'ffmpeg' else f'/usr/bin/{name}'

        with (
            patch('video_content_common.find_video_transcript_extractor_root', return_value=Path('/tmp/extractor')),
            patch('video_content_common.shutil.which', side_effect=which),
            patch('video_content_common.importlib.util.find_spec', return_value=object()),
            patch('video_content_common.run_status', return_value=failed_extractor),
            patch('video_content_common.check_analysis_command', return_value={
                'checked': True, 'ok': True, 'executable': '/usr/bin/claude', 'error': '',
            }),
            patch('video_content_common.platform.system', return_value='Darwin'),
            patch('video_content_common.platform.machine', return_value='arm64'),
        ):
            environment = video_content_environment(
                browser='safari', analysis_provider='command', analysis_command='claude -p',
                visual_analysis=True,
            )

        self.assertEqual(environment['missing'], ['ffmpeg'])
        self.assertNotIn('codex', environment['tools'])
        self.assertFalse(environment['codex_login']['checked'])
        self.assertEqual(environment['capabilities']['text_analysis']['provider'], 'command')
        self.assertTrue(environment['capabilities']['visual_analysis']['ready'])
        self.assertTrue(environment['mimo_install_authorized'])
        self.assertFalse(environment['mimo_install_required'])
        self.assertEqual(environment['missing_requiring_additional_confirmation'], ['ffmpeg'])
        self.assertTrue(environment['should_ask_user_to_install_video_content'])

    def test_mimo_vl_provider_authorizes_both_local_mimo_modules(self):
        failed_extractor = {"ok": False, "returncode": 1, "stdout": "", "stderr": "missing ASR"}
        missing_vl = {
            'checked': True,
            'ready': False,
            'missing': ['mimo-vl-venv-python', 'mimo-vl-official-bf16-model', 'mlx-vlm-0.5.0'],
        }
        with (
            patch('video_content_common.find_video_transcript_extractor_root', return_value=Path('/tmp/extractor')),
            patch('video_content_common.shutil.which', side_effect=lambda name: f'/usr/bin/{name}'),
            patch('video_content_common.importlib.util.find_spec', return_value=object()),
            patch('video_content_common.run_status', return_value=failed_extractor),
            patch('video_content_common.check_mimo_vl_environment', return_value=missing_vl),
            patch('video_content_common.platform.system', return_value='Darwin'),
            patch('video_content_common.platform.machine', return_value='arm64'),
        ):
            environment = video_content_environment(
                browser='safari', analysis_provider='mimo-vl-mlx', visual_analysis=True,
            )

        self.assertEqual(environment['capabilities']['text_analysis']['provider'], 'mimo-vl-mlx')
        self.assertFalse(environment['capabilities']['visual_analysis']['ready'])
        self.assertEqual(environment['missing'], [
            'mimo-mlx-runtime-or-model',
            'mimo-vl-venv-python',
            'mimo-vl-official-bf16-model',
            'mlx-vlm-0.5.0',
        ])
        self.assertTrue(environment['mimo_install_required'])
        self.assertEqual(
            environment['mimo_install_authorized_components'],
            ['mimo-v2.5-asr-mlx', 'mimo-vl-mlx'],
        )
        self.assertEqual(environment['missing_requiring_additional_confirmation'], [])
        self.assertFalse(environment['should_ask_user_to_install_video_content'])
        install = next(step for step in environment['install_steps'] if step['component'] == 'mimo-vl-mlx')
        self.assertIn('16.6 GB', install['size'])
        self.assertIn('17.6 GB', install['hardware'])
        self.assertIn('32 GB', install['hardware'])
        self.assertEqual(install['command'], './scripts/install_mimo_vl_mlx.sh')
        self.assertEqual(install['revision'], '4bfb270765825d2fa059011deb4c96fdd579be6f')

    def test_mimo_vl_environment_requires_exact_version_and_all_official_shards(self):
        for version, ready in (('0.5.0', True), ('0.6.4', False)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                python = root / '.venv' / 'bin' / 'python'
                python.parent.mkdir(parents=True)
                python.write_text('#!/bin/sh\n', encoding='utf-8')
                python.chmod(0o755)
                model = root / 'models' / 'MiMo-VL-7B-RL-2508'
                model.mkdir(parents=True)
                for filename in MIMO_VL_MODEL_FILES:
                    path = model / filename
                    if filename in EXPECTED_SHARD_SIZES:
                        path.touch()
                        with path.open('r+b') as handle:
                            handle.truncate(EXPECTED_SHARD_SIZES[filename])
                    else:
                        path.write_bytes(b'present')
                version_result = {'ok': True, 'returncode': 0, 'stdout': version, 'stderr': ''}
                with (
                    patch('video_content_common.run_status', return_value=version_result),
                    patch('video_content_common.platform.system', return_value='Darwin'),
                    patch('video_content_common.platform.machine', return_value='arm64'),
                ):
                    check = check_mimo_vl_environment(root)

                self.assertEqual(check['ready'], ready)
                self.assertEqual(check['mlx_vlm_version'], version)
                self.assertEqual(check['missing_model_files'], [])
                self.assertEqual(check['wrong_shard_sizes'], {})
                self.assertEqual(check['forbidden_version_detected'], version == '0.6.4')
                if not ready:
                    self.assertIn('mlx-vlm-0.5.0', check['missing'])

    def test_mimo_vl_root_precedence_is_explicit_then_environment_then_default(self):
        with patch.dict('video_content_common.os.environ', {'XHS_MIMO_VL_ROOT': '/from-env'}, clear=True):
            self.assertEqual(resolve_mimo_vl_root('/explicit'), Path('/explicit'))
            self.assertEqual(resolve_mimo_vl_root(), Path('/from-env'))
        with (
            patch.dict('video_content_common.os.environ', {}, clear=True),
            patch('video_content_common.Path.home', return_value=Path('/Users/test')),
        ):
            self.assertEqual(
                resolve_mimo_vl_root(),
                Path('/Users/test/Documents/MiMo-VL-7B-RL-2508'),
            )

    def test_video_environment_command_provider_requires_analysis_command(self):
        with self.assertRaisesRegex(ValueError, 'analysis_command'):
            video_content_environment(browser='safari', analysis_provider='command')

    def test_transcript_only_mode_does_not_claim_visual_capability(self):
        with (
            patch('video_content_common.find_video_transcript_extractor_root', return_value=Path('/tmp/extractor')),
            patch('video_content_common.shutil.which', side_effect=lambda name: f'/usr/bin/{name}'),
            patch('video_content_common.importlib.util.find_spec', return_value=object()),
            patch('video_content_common.run_status', return_value={
                'ok': True, 'returncode': 0, 'stdout': '', 'stderr': '',
            }),
            patch('video_content_common.platform.system', return_value='Darwin'),
            patch('video_content_common.platform.machine', return_value='arm64'),
        ):
            environment = video_content_environment(browser='safari', analysis_provider='codex-cli')

        visual = environment['capabilities']['visual_analysis']
        self.assertFalse(visual['required'])
        self.assertFalse(visual['ready'])
        self.assertEqual(visual['status'], 'not_enabled')
        self.assertEqual(environment['mimo_install_authorized_components'], ['mimo-v2.5-asr-mlx'])

    def test_check_environment_video_content_requires_provider_and_command(self):
        missing_provider = subprocess.run(
            [sys.executable, str(SCRIPTS / 'check_environment.py'), '--video-content', '--browser', 'safari'],
            cwd=str(ROOT), text=True, capture_output=True, check=False,
        )
        self.assertEqual(missing_provider.returncode, 2)
        self.assertIn('--analysis-provider', missing_provider.stderr)

        missing_command = subprocess.run(
            [
                sys.executable, str(SCRIPTS / 'check_environment.py'), '--video-content',
                '--browser', 'safari', '--analysis-provider', 'command',
            ],
            cwd=str(ROOT), text=True, capture_output=True, check=False,
        )
        self.assertEqual(missing_command.returncode, 2)
        self.assertIn('--analysis-command', missing_command.stderr)

    def test_v2_onboarding_orders_startup_scope_ocr_browser_reading_then_video(self):
        skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
        onboarding = (ROOT / 'references' / 'onboarding-v2.md').read_text(encoding='utf-8')

        self.assertIn('## v2.0 新用户入口（唯一入口）', skill)
        self.assertIn('快速启动（按推荐设置）', skill)
        self.assertIn('完整启动（自己逐项设置）', skill)
        self.assertIn('图文 OCR（推荐开启，以提高识别率）', skill)
        self.assertIn('封面和全部内页图片', skill)
        self.assertIn('上海两天一夜路线、餐厅和交通方式', skill)
        self.assertIn('60% 的图文整理场景', skill)
        self.assertIn('每组最多 200 条，暂停 3 分钟', skill)
        self.assertIn('run_config.json', skill)
        self.assertIn('分类方案预览', skill)
        self.assertIn('完成平台处理、回到列表页后回复“继续整理”', skill)
        self.assertIn('每周整理', skill)
        self.assertIn('不能自动创建专辑或移动笔记', skill)
        self.assertIn('不得把视觉分析固定为 Codex CLI', skill)
        self.assertIn('MiMo-V2.5-ASR-MLX` 约 6.6 GB', skill)
        self.assertIn('MiMo-VL-7B-RL-2508` 约 16.6 GB', skill)

        startup_index = skill.index('第一步：请选择启动方式')
        scope_index = skill.index('用户选择启动方式后，才询问范围')
        ocr_index = skill.index('图文 OCR（推荐开启，以提高识别率）')
        browser_index = skill.index('OCR 选择后，取得**本次**对一个已打开')
        batch_index = skill.index('读取前只问一次批次设置')
        distribution_index = skill.index('全部所选列表读取完毕后，显示真实图文、视频、待确认分布')
        self.assertLess(startup_index, scope_index)
        self.assertLess(scope_index, ocr_index)
        self.assertLess(ocr_index, browser_index)
        self.assertLess(browser_index, batch_index)
        self.assertLess(batch_index, distribution_index)

        self.assertIn('读取、分类和移动是三件不同的事', onboarding)
        self.assertIn('每周只整理新出现的内容', onboarding)
        self.assertIn('当次确认一个已登录浏览器', onboarding)
        self.assertIn('不会自动创建专辑或移动笔记', onboarding)
        self.assertIn('不用于伪装成人', onboarding)
        self.assertIn('先检查我是否已有类似功能的 AI 或模型', onboarding)
        self.assertNotIn('Watch' + 'Before', skill)

    def test_full_ocr_check_only_runs_after_opt_in(self):
        disabled = subprocess.run(
            [sys.executable, str(SCRIPTS / 'check_environment.py')],
            cwd=str(ROOT), text=True, capture_output=True, check=True,
        )
        disabled_data = json.loads(disabled.stdout)
        self.assertFalse(disabled_data['ocr_checked'])
        self.assertEqual(disabled_data['ocr_status'], 'not_enabled')
        self.assertFalse(disabled_data['ocr_ready'])
        self.assertFalse(disabled_data['ocr_install_authorized_by_enable_switch'])

        enabled = subprocess.run(
            [sys.executable, str(SCRIPTS / 'check_environment.py'), '--ocr'],
            cwd=str(ROOT), text=True, capture_output=True, check=True,
        )
        enabled_data = json.loads(enabled.stdout)
        self.assertTrue(enabled_data['ocr_checked'])
        self.assertIn(enabled_data['ocr_status'], {'ready', 'missing'})
        self.assertIn(enabled_data['ocr_provider'], {'swift-vision', 'tesseract-chi_sim+eng', 'easyocr', 'none'})
        self.assertIn('estimated_download', enabled_data['ocr_install_size'])
        self.assertFalse(enabled_data['paddleocr_supported'])

    def test_capability_preflight_is_independent_read_only_and_never_authorizes_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / 'check_environment.py'),
                    '--capability-preflight',
                    '--extractor-root', str(root / 'extractor'),
                    '--mimo-asr-root', str(root / 'asr'),
                    '--mimo-vl-root', str(root / 'vl'),
                ],
                cwd=str(ROOT), text=True, capture_output=True, check=True,
            )
            after = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
        data = json.loads(result.stdout)
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual(data['mode'], 'capability_preflight')
        self.assertEqual(data['safety']['browser_access'], 'not_performed')
        self.assertEqual(data['safety']['network_access'], 'not_performed')
        self.assertEqual(data['safety']['installation'], 'not_performed')
        self.assertEqual(data['safety']['model_loading'], 'not_performed')
        self.assertFalse(data['installation_authorized'])
        self.assertIn('先选择快速启动或完整启动', data['next_step'])
        self.assertIn('确认整理范围', data['next_step'])
        self.assertIn(data['capabilities']['ocr']['status'], {'ready', 'missing'})
        self.assertEqual(data['capabilities']['host_visual']['status'], 'unknown')
        self.assertEqual(before, after)

        invalid = subprocess.run(
            [
                sys.executable, str(SCRIPTS / 'check_environment.py'),
                '--capability-preflight', '--browser', 'arc',
            ],
            cwd=str(ROOT), text=True, capture_output=True,
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn('独立只读模式', invalid.stderr)

    def test_static_asr_preflight_checks_documented_files_without_loading_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python = root / '.venv' / 'bin' / 'python'
            python.parent.mkdir(parents=True)
            python.write_text('#!/bin/sh\n', encoding='utf-8')
            python.chmod(0o755)
            (root / 'run_mimo_asr_mlx.py').write_text('', encoding='utf-8')
            for relative in MIMO_ASR_REQUIRED_FILES:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'present')
            with (
                patch('video_content_common.platform.system', return_value='Darwin'),
                patch('video_content_common.platform.machine', return_value='arm64'),
            ):
                result = check_mimo_asr_environment(root)
        self.assertTrue(result['ready'])
        self.assertEqual(result['check_level'], 'static_offline')
        self.assertFalse(result['model_loaded'])
        self.assertEqual(result['missing'], [])

    def test_local_preflight_never_calls_browser_helpers_and_host_capability_is_declaration(self):
        asr = {'ready': True, 'missing': [], 'checked': True}
        visual = {'ready': False, 'missing': ['mimo-vl-official-bf16-model'], 'checked': True}
        with (
            patch('video_content_common.find_video_transcript_extractor_root', return_value=None),
            patch('video_content_common.check_mimo_asr_environment', return_value=asr),
            patch('video_content_common.check_mimo_vl_environment', return_value=visual),
            patch('video_content_common.arc_running') as arc_running_mock,
            patch('video_content_common.arc_login_status') as arc_login_mock,
        ):
            result = local_capability_preflight(
                host_visual_capability='ready', host_visual_name='宿主视觉模型',
            )
        arc_running_mock.assert_not_called()
        arc_login_mock.assert_not_called()
        self.assertEqual(result['mode'], 'local_read_only')
        self.assertFalse(result['policy']['browser_accessed'])
        self.assertFalse(result['policy']['network_accessed'])
        self.assertFalse(result['policy']['software_installed'])
        self.assertFalse(result['policy']['large_model_loaded'])
        self.assertEqual(result['video_visual']['host_visual_ai']['status'], 'declared_ready')
        self.assertEqual(result['video_visual']['host_visual_ai']['source'], 'host_declaration')

    def test_switch_off_preserves_metadata_classifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible.json'
            out = tmp_path / 'classification.json'
            visible.write_text(json.dumps([{
                'id': 'note-image', 'title': '滑雪换刃', 'desc': '', 'tags': ['滑雪'],
                'card_text': '滑雪 单板', 'content_type': 'video',
            }], ensure_ascii=False), encoding='utf-8')
            self.run_script('classify_items.py', '--skip-ocr', str(visible), str(out))
            row = json.loads(out.read_text(encoding='utf-8'))[0]
            self.assertEqual(row['target_board'], '滑雪')
            self.assertEqual(row['classification_basis'], 'metadata_only')
            self.assertEqual(row['ocr_status'], 'skipped')

    def test_video_switch_uses_selected_provider_analysis_not_misleading_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible.json'
            analysis = tmp_path / 'analysis.json'
            out = tmp_path / 'classification.json'
            visible.write_text(json.dumps([{
                'id': 'note-video', 'title': '滑雪固定器', 'desc': '滑雪', 'tags': ['滑雪'],
                'card_text': '滑雪', 'content_type': 'video',
            }], ensure_ascii=False), encoding='utf-8')
            analysis.write_text(json.dumps([{
                'id': 'note-video', 'status': 'success', 'main_topic': '摄影布光',
                'content_summary': '讲解狭小空间的灯光布置', 'target_board': '摄影审美与创作',
                'confidence': 'high', 'reason': ['实际讲话内容是摄影布光'],
                'analysis_basis': 'transcript_only', 'visual_status': 'not_enabled',
                'analysis_provider': 'command', 'analysis_model': 'test-agent',
                'analysis_provider_version': 'json-stdin-stdout-v1',
            }], ensure_ascii=False), encoding='utf-8')
            self.run_script(
                'classify_items.py', '--skip-ocr', str(visible), str(out),
                '--classify-video-by-content', '--video-analysis', str(analysis),
            )
            row = json.loads(out.read_text(encoding='utf-8'))[0]
            self.assertEqual(row['target_board'], '摄影审美与创作')
            self.assertEqual(row['classification_basis'], 'video_content')
            self.assertEqual(row['review_state'], 'video_content_classified')
            self.assertEqual(row['video_analysis_basis'], 'transcript_only')
            self.assertEqual(row['visual_status'], 'not_enabled')
            self.assertEqual(row['analysis_provider'], 'command')
            self.assertEqual(row['ocr_status'], 'skipped')

    def test_visual_requirement_rejects_transcript_only_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible.json'
            analysis = tmp_path / 'analysis.json'
            out = tmp_path / 'classification.json'
            visible.write_text(json.dumps([{
                'id': 'note-video', 'title': '标题', 'content_type': 'video',
            }], ensure_ascii=False), encoding='utf-8')
            analysis.write_text(json.dumps([{
                'id': 'note-video', 'status': 'success', 'main_topic': '主题',
                'content_summary': '摘要', 'target_board': '其他', 'confidence': 'high',
                'reason': ['合格文字稿'], 'analysis_basis': 'transcript_only',
                'visual_status': 'not_enabled',
            }], ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / 'classify_items.py'), '--skip-ocr',
                    str(visible), str(out), '--classify-video-by-content',
                    '--require-visual-analysis', '--video-analysis', str(analysis),
                ],
                cwd=str(ROOT), text=True, capture_output=True, check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn('尚未完成完整时轴画面分析', result.stderr)

    def test_missing_video_analysis_never_falls_back_to_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible.json'
            analysis = tmp_path / 'analysis.json'
            out = tmp_path / 'classification.json'
            visible.write_text(json.dumps([{
                'id': 'note-video', 'title': '滑雪固定器', 'desc': '滑雪', 'tags': ['滑雪'],
                'card_text': '滑雪', 'content_type': 'video',
            }], ensure_ascii=False), encoding='utf-8')
            analysis.write_text('[]', encoding='utf-8')
            self.run_script(
                'classify_items.py', '--skip-ocr', str(visible), str(out),
                '--classify-video-by-content', '--video-analysis', str(analysis),
                '--allow-partial-video-analysis',
            )
            row = json.loads(out.read_text(encoding='utf-8'))[0]
            self.assertEqual(row['target_board'], '')
            self.assertEqual(row['review_state'], 'video_content_unavailable')
            self.assertEqual(row['reason'], ['video_content_unavailable'])

    def test_full_video_classification_rejects_incomplete_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible.json'
            analysis = tmp_path / 'analysis.json'
            out = tmp_path / 'classification.json'
            visible.write_text(json.dumps([{
                'id': 'note-video', 'title': '标题', 'content_type': 'video',
            }], ensure_ascii=False), encoding='utf-8')
            analysis.write_text('[]', encoding='utf-8')
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS / 'classify_items.py'), '--skip-ocr',
                    str(visible), str(out), '--classify-video-by-content',
                    '--video-analysis', str(analysis),
                ],
                cwd=str(ROOT), text=True, capture_output=True, check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn('尚未完成', result.stderr)

    def test_unknown_content_type_requires_review_when_switch_is_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible.json'
            analysis = tmp_path / 'analysis.json'
            out = tmp_path / 'classification.json'
            visible.write_text(json.dumps([{
                'id': 'note-unknown', 'title': '滑雪固定器', 'tags': ['滑雪'], 'content_type': 'unknown',
            }], ensure_ascii=False), encoding='utf-8')
            analysis.write_text('[]', encoding='utf-8')
            self.run_script(
                'classify_items.py', '--skip-ocr', str(visible), str(out),
                '--classify-video-by-content', '--video-analysis', str(analysis),
            )
            row = json.loads(out.read_text(encoding='utf-8'))[0]
            self.assertEqual(row['target_board'], '')
            self.assertEqual(row['review_state'], 'content_type_needs_review')

    def test_transcriber_processes_only_explicit_videos_and_continues_after_failure(self):
        items = [
            {'id': 'image-1', 'content_type': 'image'},
            {'id': 'video-1', 'content_type': 'video'},
            {'id': 'video-2', 'content_type': 'video'},
        ]
        calls = []

        def acquire(item):
            calls.append(item['id'])
            if item['id'] == 'video-1':
                return {'id': item['id'], 'status': 'failed', 'reason_code': 'video_content_unavailable'}
            return {'id': item['id'], 'status': 'success', 'segments': []}

        rows = build_transcript_rows(items, acquire)
        self.assertEqual(calls, ['video-1', 'video-2'])
        self.assertEqual([row['status'] for row in rows], ['failed', 'success'])

    def test_transcriber_can_select_an_exact_video_id(self):
        items = [
            {'id': 'video-1', 'content_type': 'video'},
            {'id': 'video-2', 'content_type': 'video'},
        ]
        calls = []

        rows = build_transcript_rows(
            items,
            lambda item: calls.append(item['id']) or {'id': item['id'], 'status': 'success'},
            video_ids={'video-2'},
        )

        self.assertEqual(calls, ['video-2'])
        self.assertEqual([row['id'] for row in rows], ['video-2'])

    def test_transcriber_resume_preserves_existing_rows_and_only_processes_missing(self):
        items = [
            {'id': 'video-1', 'content_type': 'video'},
            {'id': 'video-2', 'content_type': 'video'},
        ]
        calls = []
        segments = [{'start': 0, 'end': 10, 'text': '已有文字稿'}]
        existing = [{
            'id': 'video-1', 'status': 'success', 'segments': segments,
            'transcript_sha256': transcript_sha256(segments),
            'coverage': {'transcript_quality_passed': True},
        }]

        rows = build_transcript_rows(
            items,
            lambda item: calls.append(item['id']) or {'id': item['id'], 'status': 'success'},
            initial_rows=existing,
        )

        self.assertEqual(calls, ['video-2'])
        self.assertEqual([row['id'] for row in rows], ['video-1', 'video-2'])
        self.assertEqual(rows[0]['segments'][0]['text'], '已有文字稿')

    def test_suspicious_yt_dlp_failure_is_single_item_failure_when_control_probe_succeeds(self):
        items = [
            {'id': 'video-control', 'content_type': 'video'},
            {'id': 'video-current', 'content_type': 'video'},
        ]
        segments = [{'start': 0, 'end': 10, 'text': '已有文字稿'}]
        existing = [{
            'id': 'video-control', 'status': 'success', 'segments': segments,
            'transcript_sha256': transcript_sha256(segments),
            'coverage': {'transcript_quality_passed': True},
        }]
        probe_calls = []

        rows = build_transcript_rows(
            items,
            lambda item: {
                'id': item['id'], 'status': 'failed',
                'reason_code': 'video_content_unavailable',
                'error': 'UserVisibleError: ERROR: No video formats found',
            },
            initial_rows=existing,
            control_probe=lambda item: probe_calls.append(item['id']) or True,
        )

        self.assertEqual(probe_calls, ['video-control'])
        self.assertEqual([row['status'] for row in rows], ['success', 'failed'])

    def test_suspicious_yt_dlp_failure_aborts_before_checkpoint_when_control_probe_fails(self):
        items = [
            {'id': 'video-control', 'content_type': 'video'},
            {'id': 'video-current', 'content_type': 'video'},
        ]
        segments = [{'start': 0, 'end': 10, 'text': '已有文字稿'}]
        existing = [{
            'id': 'video-control', 'status': 'success', 'segments': segments,
            'transcript_sha256': transcript_sha256(segments),
            'coverage': {'transcript_quality_passed': True},
        }]
        checkpoints = []

        with self.assertRaisesRegex(BatchInfrastructureError, '对照视频'):
            build_transcript_rows(
                items,
                lambda item: {
                    'id': item['id'], 'status': 'failed',
                    'reason_code': 'video_content_unavailable',
                    'error': 'UserVisibleError: ERROR: Unable to extract initial state',
                },
                initial_rows=existing,
                control_probe=lambda item: False,
                on_row=lambda rows: checkpoints.append(rows),
            )

        self.assertEqual(checkpoints, [])

    def test_suspicious_yt_dlp_failure_without_valid_control_aborts_before_checkpoint(self):
        items = [{'id': 'video-current', 'content_type': 'video'}]
        checkpoints = []

        with self.assertRaisesRegex(BatchInfrastructureError, '没有有效的成功文字稿'):
            build_transcript_rows(
                items,
                lambda item: {
                    'id': item['id'], 'status': 'failed',
                    'reason_code': 'video_content_unavailable',
                    'error': 'UserVisibleError: ERROR: No video formats found',
                },
                control_probe=lambda item: True,
                on_row=lambda rows: checkpoints.append(rows),
            )

        self.assertEqual(checkpoints, [])

    def test_transcriber_resume_rejects_stale_or_duplicate_rows(self):
        items = [{'id': 'video-1', 'content_type': 'video'}]

        with self.assertRaisesRegex(ValueError, '当前输入'):
            build_transcript_rows(
                items,
                lambda item: {},
                initial_rows=[{'id': 'other-video', 'status': 'success'}],
            )
        with self.assertRaisesRegex(ValueError, '重复'):
            build_transcript_rows(
                items,
                lambda item: {},
                initial_rows=[
                    {'id': 'video-1', 'status': 'failed'},
                    {'id': 'video-1', 'status': 'failed'},
                ],
            )

    def test_transcriber_rejects_unknown_or_unverified_requested_id(self):
        items = [
            {'id': 'image-1', 'content_type': 'image'},
            {'id': 'video-1', 'content_type': 'video'},
        ]

        with self.assertRaisesRegex(ValueError, 'image-1'):
            build_transcript_rows(items, lambda item: {}, video_ids={'image-1'})

    def test_transcriber_cli_requires_explicit_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            visible = tmp_path / 'visible.json'
            visible.write_text('[]', encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / 'transcribe_video_items.py'), str(visible), str(tmp_path / 'out.json')],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn('--browser', result.stderr)

    def test_persistent_worker_treats_explicit_empty_chunk_as_uncovered_not_corrupt(self):
        class UserVisibleError(RuntimeError):
            pass

        class FakeModule:
            @staticmethod
            def audio_duration(path):
                return 30.0

            @staticmethod
            def clean_segment_text(value):
                return ' '.join(str(value or '').split())

            @staticmethod
            def first_present(item, keys):
                for key in keys:
                    if item.get(key) not in (None, ''):
                        return item[key]
                return None

            @staticmethod
            def parse_transcript_json(path, fallback_duration):
                data = json.loads(path.read_text(encoding='utf-8'))
                if not data.get('text'):
                    raise UserVisibleError('empty')
                return [{'start': 0, 'end': fallback_duration, 'text': data['text']}]

        FakeModule.UserVisibleError = UserVisibleError

        class FakeWorker:
            def transcribe(self, audio_path, output_prefix, timeout):
                payload = {'segments': [], 'text': ''} if audio_path.stem == 'silent' else {'text': '有效讲话内容'}
                output_prefix.with_suffix('.json').write_text(json.dumps(payload), encoding='utf-8')
                return {'ok': True}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio_paths = [tmp_path / 'silent.wav', tmp_path / 'speech.wav']
            material, _paths = transcribe_audio_files_with_worker(
                FakeModule, object(), FakeWorker(), audio_paths, tmp_path / 'out', timeout=10,
            )

        self.assertEqual(material['empty_chunk_count'], 1)
        self.assertEqual(len(material['segments']), 1)
        self.assertEqual(material['segments'][0]['start'], 30.0)

    def test_mimo_preflight_merges_70_and_76_sample_tail_without_losing_pcm(self):
        for tail_sample_count in (70, 76):
            with self.subTest(tail_sample_count=tail_sample_count), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                previous = tmp_path / 'part001_chunk00000.wav'
                tail = tmp_path / 'part001_chunk00001.wav'
                previous_frames = self.write_pcm_wav(previous, [101] * 1000)
                tail_frames = self.write_pcm_wav(tail, [202] * tail_sample_count)

                prepared = ensure_mimo_audio_input(
                    self.fake_transcript_module(),
                    self.mimo_runtime(tmp_path),
                    [previous, tail],
                    tmp_path / 'prepared',
                )

                self.assertEqual(len(prepared), 1)
                params, frames = self.read_pcm_wav(prepared[0])
                self.assertEqual(params.framerate, 16000)
                self.assertEqual(params.nchannels, 1)
                self.assertEqual(params.nframes, 1000 + tail_sample_count)
                self.assertEqual(frames, previous_frames + tail_frames)

    def test_mimo_preflight_pads_70_and_76_sample_single_chunk_at_end(self):
        for sample_count in (70, 76):
            with self.subTest(sample_count=sample_count), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                chunk = tmp_path / 'part001_chunk00000.wav'
                original_frames = self.write_pcm_wav(chunk, [303] * sample_count)

                prepared = ensure_mimo_audio_input(
                    self.fake_transcript_module(),
                    self.mimo_runtime(tmp_path),
                    [chunk],
                    tmp_path / 'prepared',
                )

                params, frames = self.read_pcm_wav(prepared[0])
                self.assertEqual(params.nframes, 640)
                self.assertEqual(frames[:len(original_frames)], original_frames)
                self.assertEqual(frames[len(original_frames):], b'\0' * ((640 - sample_count) * 2))

    def test_mimo_preflight_rejects_non_tail_short_chunk_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            short = tmp_path / 'part001_chunk00000.wav'
            following = tmp_path / 'part001_chunk00001.wav'
            self.write_pcm_wav(short, [1] * 70)
            self.write_pcm_wav(following, [2] * 1000)

            with self.assertRaisesRegex(RuntimeError, '非尾部'):
                ensure_mimo_audio_input(
                    self.fake_transcript_module(),
                    self.mimo_runtime(tmp_path),
                    [short, following],
                    tmp_path / 'prepared',
                )

    def test_coverage_gate_rejects_short_transcript(self):
        result = validate_transcript_coverage(
            video_duration=120,
            transcript_source='mimo_audio',
            segments=[{'start': 0, 'end': 10, 'text': '太短'}],
        )
        self.assertFalse(result['transcript_quality_passed'])
        self.assertEqual(result['transcript_quality_reason'], 'coverage_below_threshold')

    def test_short_video_accepts_one_complete_segment(self):
        result = validate_transcript_coverage(
            video_duration=17.484,
            transcript_source='mimo_audio',
            segments=[{'start': 0, 'end': 17.5, 'text': '这是一段完整覆盖短视频内容的有效文字稿，能够清楚说明视频的主要观点、具体做法、适用场景和限制条件，长度也明确超过质量门槛。'}],
        )
        self.assertTrue(result['transcript_quality_passed'])
        self.assertEqual(result['required_segment_count'], 1)

    def test_short_video_rejects_one_small_segment_with_long_text(self):
        result = validate_transcript_coverage(
            video_duration=17.484,
            transcript_source='mimo_audio',
            segments=[{'start': 0, 'end': 2, 'text': '这段文字故意写得很长，但时间轴只覆盖视频开头两秒，因此不能因为字数够多就被当作完整视频文字稿。' * 2}],
        )
        self.assertFalse(result['transcript_quality_passed'])
        self.assertEqual(result['transcript_quality_reason'], 'coverage_below_threshold')

    def test_coverage_uses_union_not_last_timestamp(self):
        result = validate_transcript_coverage(
            video_duration=100,
            transcript_source='mimo_audio',
            segments=[
                {'start': 0, 'end': 5, 'text': '第一小段内容足够长，用于验证覆盖率计算不能把中间空白算进去。'},
                {'start': 95, 'end': 100, 'text': '第二小段位于结尾，时间戳很晚但实际总覆盖仍然很低。'},
            ],
        )
        self.assertAlmostEqual(result['transcript_covered_duration'], 10.0)
        self.assertAlmostEqual(result['transcript_coverage_ratio'], 0.1)
        self.assertFalse(result['transcript_quality_passed'])

    def test_provider_analysis_rejects_board_outside_taxonomy(self):
        with self.assertRaises(ValueError):
            validate_analysis({
                'main_topic': '主题', 'content_summary': '摘要', 'target_board': '虚构专辑',
                'confidence': 'high', 'reason': ['原因'],
            }, ['滑雪'])

    def test_text_analysis_prompt_contains_the_full_machine_output_contract(self):
        prompt = analysis_prompt({'segments': [{'start': 0, 'end': 1, 'text': '内容'}]}, ['其他'])
        for field in ('main_topic', 'content_summary', 'target_board', 'confidence', 'reason'):
            self.assertIn(field, prompt)
        self.assertIn('不得遗漏字段', prompt)

    def test_provider_analysis_rejects_empty_content_and_invalid_empty_board_confidence(self):
        with self.assertRaisesRegex(ValueError, 'main_topic'):
            validate_analysis({
                'main_topic': '', 'content_summary': '摘要', 'target_board': '滑雪',
                'confidence': 'high', 'reason': ['原因'],
            }, ['滑雪'])
        with self.assertRaisesRegex(ValueError, 'confidence'):
            validate_analysis({
                'main_topic': '主题', 'content_summary': '摘要', 'target_board': '',
                'confidence': 'high', 'reason': ['原因'],
            }, ['滑雪'])

    def test_failed_transcript_is_carried_into_analysis_without_provider_call(self):
        calls = []
        rows = build_analysis_rows(
            [{'id': 'v1', 'status': 'failed', 'reason_code': 'transcript_coverage_too_low', 'error': 'bad'}],
            lambda row: calls.append(row) or {},
        )
        self.assertEqual(calls, [])
        self.assertEqual(rows[0]['reason_code'], 'transcript_coverage_too_low')
        self.assertEqual(rows[0]['analysis_basis'], 'transcript_only')
        self.assertEqual(rows[0]['visual_status'], 'not_enabled')

    def test_analysis_resume_reuses_matching_hash_and_reanalyzes_changed_hash(self):
        segments_1 = [{'start': 0, 'end': 10, 'text': '第一条完整文字稿'}]
        segments_2 = [{'start': 0, 'end': 10, 'text': '第二条更新后的完整文字稿'}]
        hash_1 = transcript_sha256(segments_1)
        hash_2 = transcript_sha256(segments_2)
        transcripts = [
            {'id': 'v1', 'status': 'success', 'transcript_sha256': hash_1, 'segments': segments_1,
             'coverage': {'transcript_quality_passed': True}},
            {'id': 'v2', 'status': 'success', 'transcript_sha256': hash_2, 'segments': segments_2,
             'coverage': {'transcript_quality_passed': True}},
        ]
        boards = ['滑雪', '思考与成长']
        identity = {'provider': 'command', 'model': 'test-agent', 'version': '1'}
        existing = [
            {'id': 'v1', 'status': 'success', 'transcript_sha256': hash_1, 'target_board': '滑雪',
             'main_topic': '滑雪主题', 'content_summary': '滑雪摘要', 'confidence': 'high', 'reason': ['原因'],
             'analysis_input_sha256': analysis_input_sha256(
                 transcript_hash=hash_1, boards=boards, provider_identity=identity,
             )},
            {'id': 'v2', 'status': 'success', 'transcript_sha256': 'old', 'target_board': '滑雪',
             'main_topic': '旧主题', 'content_summary': '旧摘要', 'confidence': 'high', 'reason': ['原因']},
        ]
        calls = []

        rows = build_analysis_rows(
            transcripts,
            lambda row: calls.append(row['id']) or {
                'status': 'success', 'target_board': '思考与成长', 'main_topic': '主题',
                'content_summary': '摘要', 'confidence': 'high', 'reason': ['原因'], 'error': '',
            },
            initial_rows=existing,
            allowed_boards=boards,
            analysis_identity=identity,
        )

        self.assertEqual(calls, ['v2'])
        self.assertEqual(rows[0]['target_board'], '滑雪')
        self.assertEqual(rows[1]['target_board'], '思考与成长')
        self.assertEqual(rows[1]['transcript_sha256'], hash_2)
        self.assertEqual(rows[1]['analysis_basis'], 'transcript_only')
        self.assertEqual(rows[1]['visual_status'], 'not_enabled')

    def test_arc_backend_targets_existing_xiaohongshu_tab_without_activation(self):
        captured = {}

        def fake_osascript(script):
            captured['script'] = script
            return 'ok'

        with patch('extract_visible_items.require_macos_app_running') as running, patch('extract_visible_items.osascript', fake_osascript):
            self.assertEqual(arc_js_macos('document.title'), 'ok')
            running.assert_called_once_with('Arc')
        script = captured['script']
        self.assertIn('tell application "Arc"', script)
        self.assertIn('contains "xiaohongshu.com"', script)
        self.assertIn('execute targetTab javascript jsSource', script)
        self.assertNotIn('activate', script.lower())

    def test_arc_collection_cache_supplies_verified_video_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            payload = {
                'data': {'notes': [{'note_id': 'a' * 24, 'type': 'video', 'xsec_token': 'secret-token'}]},
                'code': 0,
                'success': True,
                'msg': '',
            }
            entry = cache / 'entry_0'
            entry.write_bytes(
                b'https://edith.xiaohongshu.com/api/sns/web/v2/note/collect/page?num=30\0'
                + gzip.compress(json.dumps(payload).encode('utf-8'))
            )
            context = find_arc_collection_note_context('a' * 24, cache_directories=[cache])
            self.assertTrue(context['found'])
            self.assertEqual(context['content_type'], 'video')
            self.assertEqual(context['xsec_source'], 'pc_user')
            self.assertEqual(context['xsec_token'], 'secret-token')

    def test_sensitive_query_parameters_are_redacted_from_errors(self):
        value = 'failed https://example.test/note?xsec_token=secret-value&xsec_source=pc_user'
        redacted = redact_sensitive_text(value)
        self.assertNotIn('secret-value', redacted)
        self.assertIn('?<redacted_query>', redacted)
        variants = redact_sensitive_text(
            'xsec_token=plain-secret "xsec_token":"json-secret" '
            'xsec_token%3Dencoded-secret sign=signature-secret Cookie:cookie-secret'
        )
        for secret in ('plain-secret', 'json-secret', 'encoded-secret', 'signature-secret', 'cookie-secret'):
            self.assertNotIn(secret, variants)

    def test_arc_login_requires_cookie_and_non_login_page(self):
        cookie = {'ok': True, 'cookie_count': 21, 'has_session_cookie': True, 'error': ''}
        login_page = {'ok': False, 'tab_found': True, 'path': '/login', 'login_required': True, 'error': 'xiaohongshu_login_required'}
        account_page = {'ok': True, 'tab_found': True, 'path': '/user/profile/id', 'login_required': False, 'error': ''}

        self.assertFalse(combine_arc_login_status(cookie, login_page)['ok'])
        self.assertTrue(combine_arc_login_status(cookie, account_page)['ok'])

    def test_arc_api_type_overrides_dom_marker_without_copying_token(self):
        items = [{'id': 'a' * 24, 'content_type': 'image', 'content_type_source': 'xhs_dom_play_marker_absent'}]
        contexts = {'a' * 24: {'content_type': 'video', 'xsec_token': 'must-not-leak'}}
        self.assertEqual(apply_arc_collection_content_types(items, contexts), 1)
        self.assertEqual(items[0]['content_type'], 'video')
        self.assertEqual(items[0]['content_type_source'], 'xhs_arc_collection_api_type')
        self.assertNotIn('xsec_token', items[0])


if __name__ == '__main__':
    unittest.main()
