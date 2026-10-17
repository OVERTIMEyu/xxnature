import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / '管理-实例库-v1.py'


class CaseLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.case = dict(id='TEST-001', title='测试主线断裂', tags=['主线'], genre='测试',
                         source='隔离测试', user_prompt_raw='这里不对\n 保留空格。',
                         original_excerpt='旧稿', diagnosis='缺少连接', scope='仅测试',
                         revision_instruction='补充真实的前后关系', revised_excerpt='改稿',
                         artifact_version='测试稿-v1')

    def file(self, name, obj):
        path = self.root / name
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), '--root', str(self.root), *args],
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return json.loads(result.stdout if ok else result.stderr)

    def add(self, case=None):
        return self.cli('add', '--input', self.file('输入.json', case or self.case))

    def approval(self, **changes):
        return dict(human_confirmed=True, verbatim='隔离测试认可', source='测试消息',
                    artifact_version='测试稿-v1', scope='测试段落', **changes)

    def test_pending_preserves_raw_text(self):
        result = self.add()
        saved = json.loads(Path(result['path']).read_text(encoding='utf-8'))
        self.assertEqual(saved['user_prompt_raw'], self.case['user_prompt_raw'])
        self.assertEqual(self.cli('list'), [])
        self.assertEqual(len(self.cli('list', '--pending')), 1)

    def test_duplicate_id_and_same_version_are_rejected(self):
        self.add()
        self.cli('add', '--input', self.file('重复ID.json', self.case), ok=False)
        self.cli('add', '--input', self.file('重复内容.json', dict(self.case, id='TEST-002')), ok=False)

    def test_accept_requires_matching_user_feedback(self):
        self.add()
        for key, value in [('human_confirmed', False), ('artifact_version', '其他版本')]:
            approval = self.approval(); approval[key] = value
            self.cli('accept', '--id', self.case['id'], '--feedback', self.file('反馈.json', approval), ok=False)
        self.assertEqual(len(self.cli('list', '--pending')), 1)

    def test_accept_and_withdraw_preserve_history(self):
        self.add()
        result = self.cli('accept', '--id', self.case['id'], '--feedback', self.file('认可.json', self.approval()))
        self.assertEqual(len(self.cli('list')), 1)
        self.assertEqual(self.cli('list', '--pending'), [])
        self.cli('withdraw', '--id', self.case['id'], '--feedback', self.file('撤回.json', self.approval()))
        self.assertEqual(self.cli('list'), [])
        self.assertTrue(Path(result['path']).exists())
        self.cli('check')

    def test_unresolved_is_separate_and_cannot_be_accepted(self):
        directory = self.root / '实例库' / '重要但未解决示例'; directory.mkdir(parents=True)
        rec = dict(self.case, status='unresolved', repair_direction='尚未验证', outcome_status='最终未满意')
        (directory / '案例-TEST-001-v1.json').write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(self.cli('list'), [])
        listed = self.cli('list', '--unresolved')
        self.assertEqual(listed[0]['outcome_status'], '最终未满意')
        self.cli('accept', '--id', self.case['id'], '--feedback', self.file('认可.json', self.approval()), ok=False)
        self.assertEqual(self.cli('check')['counts']['重要但未解决示例'], 1)

    def test_legacy_prompt_record_is_compatible(self):
        case = self.case.copy(); case['generated_prompt'] = case.pop('revision_instruction')
        self.add(case)
        self.cli('check')

    def test_missing_instruction_and_unsafe_id_are_rejected(self):
        case = self.case.copy(); case.pop('revision_instruction')
        self.cli('add', '--input', self.file('缺指令.json', case), ok=False)
        self.cli('add', '--input', self.file('坏ID.json', dict(self.case, id='../escape')), ok=False)
        self.assertFalse((self.root / '实例库' / '.写入锁').exists())


if __name__ == '__main__':
    unittest.main()
